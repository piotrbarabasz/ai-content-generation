"""D024 composes real jobs, immutable artifacts and offline synthesis."""

import asyncio
import pytest

from app.application.projects import ProjectSession
from app.application.result_publication import ResultPublicationService
from app.application.section_audio import SectionAudioService
from app.jobs.coordinator import JobCoordinator
from app.jobs.repository import JobRepository
from app.runtime.section_synthesis import generate
from app.storage.local_store import LocalArtifactStore
from app.storage.project_repository import ProjectRepository
from app.storage.regeneration import ProjectRegeneration
from app.storage.result_publication import ResultArtifactIndex
from tests.integration.test_section_audio import setup, Provider, Voices  # noqa: F401
from tests.integration.test_timeline import prepared  # noqa: F401
from tests.integration.test_video_render import render  # noqa: F401


def compose(setup, *, during=lambda: None, fail=False):
    session, jobs, index, store, outputs, audio = setup
    calls = []

    async def run(attempt):
        claim = JobCoordinator(jobs).claim_next("regeneration-test")
        assert claim.id == attempt.id
        job = jobs.get_job(claim.job_id)
        calls.append(job.output_key)
        generate(job, Provider(), outputs.root)
        during()
        if fail:
            raise RuntimeError("fixture interruption after chunk persistence")
        audio.complete(claim)

    adapter = ProjectRegeneration(session, index, store, audio=audio,
        audio_selection=lambda section: {"variant": "one"}, run_audio=run)
    return adapter, calls


def test_edit_b_rebuilds_only_b_and_preserves_a_c_checksums(setup):
    adapter, calls = compose(setup)
    first = asyncio.run(adapter.service.run())
    assert [item.status for item in first] == ["rebuilt"] * 3
    old_heads = setup[2].selected()
    old_bytes = {m.artifact_id: setup[3].read_artifact(m.storage_key) for m in setup[3].list_artifacts()}
    calls.clear()
    assert all(item.status == "reused" for item in asyncio.run(adapter.service.run()))
    assert calls == []
    b = setup[0].active_script.sections[1]
    setup[0].edit_section(b.section_id, text="Only B changed.")
    outcomes = asyncio.run(adapter.service.run())
    assert [item.status for item in outcomes] == ["reused", "rebuilt", "reused"]
    assert calls == [f"section:{b.section_id}:audio:raw"]
    heads = setup[2].selected()
    for section in (setup[0].active_script.sections[0], setup[0].active_script.sections[2]):
        key = f"section:{section.section_id}:audio:raw"
        assert heads[key] == old_heads[key]
    for manifest in setup[3].list_artifacts():
        if manifest.artifact_id in old_bytes:
            assert setup[3].read_artifact(manifest.storage_key) == old_bytes[manifest.artifact_id]


def test_single_output_does_not_generate_other_sections(setup):
    adapter, calls = compose(setup)
    b = setup[0].active_script.sections[1]
    key = f"section:{b.section_id}:audio:raw"
    result = asyncio.run(adapter.service.run(key))
    assert len(result) == 1 and result[0].status == "rebuilt" and calls == [key]


def test_late_b1_never_replaces_b2_and_stops_dependent_work(setup):
    b = setup[0].active_script.sections[1]
    adapter, calls = compose(setup, during=lambda: setup[0].edit_section(b.section_id, text="New B2."))
    key = f"section:{b.section_id}:audio:raw"
    result = asyncio.run(adapter.service.run(key))
    assert result[0].status == "obsolete"
    assert key not in setup[2].selected()
    assert len(setup[2].history(key)) == 1
    assert not setup[2].history(key)[0].selected_at_publication
    current, _ = compose(setup)
    assert asyncio.run(current.service.run(key))[0].status == "rebuilt"


@pytest.mark.parametrize("cancel", [False, True])
def test_failed_or_canceled_audio_resumes_same_job_and_retains_history(setup, cancel):
    adapter, _ = compose(setup, fail=not cancel)
    if cancel:
        adapter, _ = compose(setup)
        original = adapter.run_audio
        async def run(attempt):
            adapter.service.cancel()
            await original(attempt)
        adapter.run_audio = run
    key = f"section:{setup[0].active_script.sections[1].section_id}:audio:raw"
    first = asyncio.run(adapter.service.run(key))
    assert first[0].status in ("failed", "canceled")
    job = setup[1].jobs()[0]
    retry, calls = compose(setup)
    assert asyncio.run(retry.service.run(key))[0].status == "rebuilt"
    assert len(setup[1].jobs()) == 1
    assert len(setup[1].attempts(job.id)) == 2


def test_restart_recovers_interrupted_exact_job_and_reuses_completed_sections(setup):
    session, jobs, index, store, output, audio = setup
    adapter, _ = compose(setup)
    sections = session.active_script.sections
    a_key = f"section:{sections[0].section_id}:audio:raw"
    assert asyncio.run(adapter.service.run(a_key))[0].status == "rebuilt"
    attempt = audio.enqueue(sections[1], {"variant": "one"})
    claim = JobCoordinator(jobs).claim_next("crashed-owner")
    generate(jobs.get_job(claim.job_id), Provider(), output.root)
    workspace = session.repository.workspace
    session.close()  # No completion: next session must recover the running attempt.
    with ProjectSession.open(workspace, repository_factory=ProjectRepository) as reopened:
        jobs = JobRepository(reopened.repository)
        assert claim.id in jobs.recovered_attempt_ids
        index = ResultArtifactIndex(reopened.repository, jobs)
        store = LocalArtifactStore(index.root, index=index)
        audio = SectionAudioService(ResultPublicationService(index, store), jobs, Voices(), output)
        resumed, calls = compose((reopened, jobs, index, store, output, audio))
        result = asyncio.run(resumed.service.run())
        assert [item.status for item in result] == ["reused", "rebuilt", "rebuilt"]
        assert a_key not in calls and len(jobs.attempts(attempt.job_id)) == 2


def test_other_pending_job_is_not_claimed_or_canceled(setup):
    session, jobs, _, _, _, audio = setup
    pending = audio.enqueue(session.active_script.sections[0], {"variant": "different"})
    adapter, calls = compose(setup)
    key = f"section:{session.active_script.sections[1].section_id}:audio:raw"
    result = asyncio.run(adapter.service.run(key))
    assert result[0].status == "failed" and not calls
    assert jobs.get_attempt(pending.id).status == "queued"


def visual_services(setup):
    from app.desktop.scene_composition import compose_scenes
    from tests.integration.test_visual_prompts import Provider as PromptProvider
    from tests.integration.test_image_generation import Provider as ImageProvider
    prompt_provider, image_provider = PromptProvider(), ImageProvider()
    scenes = compose_scenes(setup[0], prompt_provider=prompt_provider,
                            prompt_identity={"provider": "test", "version": "1"}, image_provider=image_provider)
    brief = scenes.prompts.pin_context("film_brief", "Test film")
    style = scenes.prompts.pin_context("visual_style", "Blue")
    scenes.context_resolver = lambda _: (brief.id, style.id)
    return scenes, prompt_provider, image_provider


def accept_proposals(setup, scenes):
    from app.application.scene_planning import ScenePlanningService
    from app.tts.scene_sources import sentence_sources
    plans = ScenePlanningService(scenes.plans, sentence_sources)
    for section in setup[0].active_script.sections:
        if not any(a.plan.revision_id == section.id for a in scenes.plans.acceptances(section.section_id)):
            proposal = next(p for p in scenes.plans.proposals(section.section_id) if p.revision_id == section.id)
            plans.accept(proposal.id, reviewer_id="test-editor")


def test_abc_visual_pipeline_reuses_a_c_and_preserves_manual_choices(setup):
    adapter, audio_calls = compose(setup)
    scenes, prompts, images = visual_services(setup)
    adapter.scenes = scenes
    first = asyncio.run(adapter.service.run())
    assert [r.status for r in first] == ["rebuilt", "review"] * 3
    assert not prompts.calls and not images.calls
    accept_proposals(setup, scenes)
    second = asyncio.run(adapter.service.run())
    assert all(r.status in ("rebuilt", "reused") for r in second)
    assert len(prompts.calls) == len(images.calls) == 3
    before = {m.artifact_id: setup[3].read_artifact(m.storage_key) for m in setup[3].list_artifacts()}
    audio_calls.clear()
    assert all(r.status == "reused" for r in asyncio.run(adapter.service.run()))
    assert len(prompts.calls) == len(images.calls) == 3 and not audio_calls

    b = setup[0].active_script.sections[1]
    setup[0].edit_section(b.section_id, text="B has changed.")
    changed = asyncio.run(adapter.service.run())
    assert sum(r.status == "review" for r in changed) == 1
    assert audio_calls == [f"section:{b.section_id}:audio:raw"]
    accept_proposals(setup, scenes)
    result = asyncio.run(adapter.service.run())
    assert all(r.status in ("rebuilt", "reused") for r in result)
    assert len(prompts.calls) == len(images.calls) == 4
    for manifest in setup[3].list_artifacts():
        if manifest.artifact_id in before:
            assert setup[3].read_artifact(manifest.storage_key) == before[manifest.artifact_id]

    a = setup[0].active_script.sections[0]
    accepted = adapter._acceptance(a)
    scene = accepted.plan.scenes[0]
    previous = scenes.prompts.prompts.selected(scene.id)
    manual = scenes.prompts.edit_manual(previous.revision_id, "Hand-painted user prompt")
    scenes.prompts.select(manual.id, expected_selection_id=previous.id)
    old_image = scenes.images.selected(scene.id)
    result = asyncio.run(adapter.service.run(f"scene:{scene.id}:image"))
    assert result[-1].status == "review"
    assert scenes.images.selected(scene.id) == old_image
    assert scenes.prompts.selected(scene.id) == manual
    assert len(prompts.calls) == len(images.calls) == 4


def test_image_resume_reuses_prompt_and_retries_same_job(setup):
    adapter, _ = compose(setup)
    scenes, prompts, images = visual_services(setup)
    adapter.scenes = scenes
    asyncio.run(adapter.service.run())
    accept_proposals(setup, scenes)
    scene = adapter._acceptance(setup[0].active_script.sections[0]).plan.scenes[0]
    key = f"scene:{scene.id}:image"
    original = images.generate
    def failed(request):
        raise ValueError("temporary image failure")
    images.generate = failed
    result = asyncio.run(adapter.service.run(key))
    assert result[-1].status == "failed"
    image_job = next(j for j in setup[1].jobs() if j.output_key == f"scene:{scene.id}:image_candidate")
    images.generate = original
    result = asyncio.run(adapter.service.run(key))
    assert result[-1].status == "rebuilt"
    assert len(setup[1].attempts(image_job.id)) == 2
    assert len(prompts.calls) == 1 and len(images.calls) == 1


def test_late_prompt_cannot_replace_a_new_manual_selection(setup):
    adapter, _ = compose(setup)
    scenes, prompts, _ = visual_services(setup)
    adapter.scenes = scenes
    asyncio.run(adapter.service.run())
    accept_proposals(setup, scenes)
    accepted = adapter._acceptance(setup[0].active_script.sections[0])
    scene = accepted.plan.scenes[0]
    def manual_during_generation():
        brief, style = scenes.context_resolver(scene.id)
        revision = scenes.prompts.create_manual(accepted.id, scene.id, brief, style, "User-owned replacement")
        scenes.prompts.select(revision.id, expected_selection_id=None)
    prompts.during = manual_during_generation
    result = asyncio.run(adapter.service.run(f"scene:{scene.id}:visual_prompt"))
    assert result[-1].status == "failed"
    assert scenes.prompts.selected(scene.id).prompt == "User-owned replacement"
    assert len(scenes.prompts.prompts.history(scene.id)) == 2


def test_proxy_and_final_use_pinned_timeline_and_reuse_without_other_generation(setup, render):
    from app.application.preview import PreviewService
    from app.desktop.timeline_composition import compose_timeline
    from app.providers.ffmpeg_render import FFmpegRenderer
    from app.storage.preview import ProjectPreviewMedia
    from tests.integration.test_preview import Process
    timeline = compose_timeline(setup[0])
    timeline.history.save(render.timeline, None)
    process = Process(render.timeline)
    preview = PreviewService(timeline.current, ProjectPreviewMedia(render.adapter),
        FFmpegRenderer(render.provider.ffmpeg, render.provider.ffprobe, process=process, proxy=True))
    adapter = ProjectRegeneration(setup[0], render.index, render.store,
                                   timeline=timeline, preview=preview, render=render.service)
    for target in ("project:proxy", "project:video_render"):
        first = asyncio.run(adapter.service.run(target))
        assert [r.status for r in first] == ["reused", "rebuilt"]
        second = asyncio.run(adapter.service.run(target))
        assert [r.status for r in second] == ["reused", "reused"]
    assert len(process.calls) == len(render.process.calls) == 3
    original_timeline = timeline.current()
    media = render.timeline.clips[0].media
    render.prepared[6].import_and_select(render.prepared[4].id, media.scene_id, render.prepared[7],
                                         expected_selection_id=media.image_selection_id)
    stale = asyncio.run(adapter.service.run("project:proxy"))
    assert [r.status for r in stale] == ["review", "blocked"]
    assert timeline.current() == original_timeline and len(process.calls) == 3
