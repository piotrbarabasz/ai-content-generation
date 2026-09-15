"""D018 consumes actual retained selections; synthetic media and no external process."""

from dataclasses import replace
import json

import pytest

from app.application.image_intake import ImageIntakeService
from app.application.projects import ProjectSession
from app.application.scene_planning import ScenePlanningService
from app.application.section_tempo import SectionTempoService
from app.application.timeline import TimelineCompiler, TimelineSceneInput
from app.domain.section_audio import SectionAudio
from app.domain.timeline import TimelineRevision
from app.jobs.coordinator import JobCoordinator
from app.jobs.repository import JobRepository
from app.runtime.section_synthesis import generate
from app.storage.local_store import LocalArtifactStore
from app.storage.project_repository import ProjectRepository
from app.storage.result_publication import ResultArtifactIndex
from app.storage.scene_images import ProjectSceneImages
from app.storage.scene_plans import ProjectScenePlans
from app.storage.section_tempo import SectionTempoArtifacts
from app.storage.timeline import ProjectTimelineMedia
from app.tts.scene_sources import sentence_sources
from tests.integration.test_image_intake import encoded
from tests.integration.test_section_audio import Provider, setup  # noqa: F401 - real project fixture
from tests.integration.test_section_tempo import FFmpeg
from tests.integration.test_speech_boundary import publish


@pytest.fixture
def prepared(setup, tmp_path):
    section, manifest, provider, _ = publish(setup)
    scene_store = ProjectScenePlans(setup[0].repository, setup[3])
    scenes = ScenePlanningService(scene_store, sentence_sources)
    accepted = scenes.accept(scenes.suggest(section).id, reviewer_id="editor")
    timing = scenes.retime(accepted.id, section, SectionAudio.from_manifest(manifest))
    image_store = ProjectSceneImages(setup[0].repository, setup[3])
    intake = ImageIntakeService(image_store)
    source = tmp_path / "fixture.png"
    source.write_bytes(encoded())
    images = tuple(intake.import_and_select(accepted.id, scene.id, source, expected_selection_id=None)
                   for scene in accepted.plan.scenes)
    compiler = TimelineCompiler(ProjectTimelineMedia(setup[2], setup[3]))
    inputs = tuple(TimelineSceneInput(timing.id, scene.id, "original") for scene in accepted.plan.scenes)
    return section, manifest, provider, scenes, accepted, timing, intake, source, images, compiler, inputs


def snapshot(store):
    return {m.artifact_id: store.read_artifact(m.storage_key) for m in store.list_artifacts()}


def test_compile_reorder_reopen_preserves_all_bytes_jobs_and_selections(setup, prepared):
    section, manifest, provider, scenes, accepted, timing, intake, source, images, compiler, inputs = prepared
    session, jobs, index, store, _, _ = setup
    before, heads, calls = snapshot(store), index.selected(), list(provider.calls)
    job_ids = [job.id for job in jobs.jobs()]
    result = compiler.compile(session.project.id, inputs)
    reordered = compiler.compile(session.project.id, reversed(inputs))
    assert result.id != reordered.id
    assert snapshot(store) == before and index.selected() == heads
    assert [job.id for job in jobs.jobs()] == job_ids and provider.calls == calls
    for clip, span, (image, choice) in zip(result.clips, timing.scenes, images):
        assert clip.media.audio.artifact_id == manifest.artifact_id
        assert (clip.media.audio.start_sample, clip.media.audio.end_sample) == (span.start_frame, span.end_frame)
        assert clip.media.image == image and clip.media.image_selection_id == choice.id
    encoded_snapshot = json.dumps(result.to_payload())
    workspace = session.repository.workspace
    session.close()
    with ProjectSession.open(workspace, repository_factory=ProjectRepository) as reopened:
        reopened_index = ResultArtifactIndex(reopened.repository, JobRepository(reopened.repository))
        reopened_store = LocalArtifactStore(reopened_index.root, index=reopened_index)
        compiled = TimelineCompiler(ProjectTimelineMedia(reopened_index, reopened_store)).compile(reopened.project.id, inputs)
        assert compiled == result == TimelineRevision.from_payload(json.loads(encoded_snapshot))
        assert snapshot(reopened_store) == before


def test_new_selection_keeps_old_snapshot_and_explicit_subset_uses_exact_source_span(setup, prepared):
    section, manifest, provider, scenes, accepted, timing, intake, source, images, compiler, inputs = prepared
    old = compiler.compile(section.project_id, inputs)
    source.write_bytes(encoded(size=(19, 13)))
    new_image, new_choice = intake.import_and_select(accepted.id, inputs[0].scene_id, source,
                                                    expected_selection_id=images[0][1].id)
    before = snapshot(setup[3])
    new = compiler.compile(section.project_id, inputs)
    assert new.id != old.id and old.clips[0].media.image == images[0][0]
    assert new.clips[0].media.image == new_image and new.clips[0].media.image_selection_id == new_choice.id
    subset = compiler.compile(section.project_id, inputs[1:])
    assert subset.clips[0].audio_offset == 0 and subset.clips[0].start_frame == 0
    assert subset.clips[0].media.audio.start_sample == timing.scenes[1].start_frame > 0
    assert snapshot(setup[3]) == before
    assert TimelineRevision.from_payload(old.to_payload()) == old


def test_missing_image_is_not_replaced_by_an_unselected_candidate(setup, prepared):
    section, _, _, scenes, _, _, intake, source, _, compiler, _ = prepared
    accepted = scenes.accept(scenes.suggest(section).id, reviewer_id="editor")
    raw = SectionTempoArtifacts(setup[2], setup[3]).selected(section, "original")
    timing = scenes.retime(accepted.id, section, raw)
    scene = accepted.plan.scenes[0]
    intake.import_file(accepted.id, scene.id, source)  # Candidate only, no selection.
    with pytest.raises(ValueError, match="no selected image"):
        compiler.compile(section.project_id, [TimelineSceneInput(timing.id, scene.id, "original")])


@pytest.mark.parametrize("kind", ["timing", "scene", "variant", "project", "section_edit"])
def test_missing_foreign_and_stale_inputs_fail_without_writes(setup, prepared, kind):
    section, _, _, _, _, _, _, _, _, compiler, inputs = prepared
    project_id = section.project_id
    if kind == "timing":
        inputs = [replace(inputs[0], timing_id="missing")]
    elif kind == "scene":
        inputs = [replace(inputs[0], scene_id="unknown")]
    elif kind == "variant":
        inputs = [replace(inputs[0], audio_variant="processed")]
    elif kind == "project":
        project_id = "foreign-project"
    else:
        setup[0].edit_section(section.section_id, text="Changed source.")
    before, heads = snapshot(setup[3]), setup[2].selected()
    with pytest.raises(ValueError):
        compiler.compile(project_id, inputs)
    assert snapshot(setup[3]) == before and setup[2].selected() == heads


def test_processed_choice_requires_explicit_retime_and_preserves_quality_and_original(setup, prepared):
    section, manifest, provider, scenes, accepted, timing, intake, source, images, compiler, inputs = prepared
    original = compiler.compile(section.project_id, inputs)
    jobs, index, store, synthesis = setup[1], setup[2], setup[3], setup[-1]
    coordinator, runner = JobCoordinator(jobs), FFmpeg()
    tempo = SectionTempoService(synthesis.publication, coordinator,
                               SectionTempoArtifacts(index, store, process_runner=runner, ffmpeg_locator=lambda _: "fixture"))
    tempo.enqueue(section, manifest.artifact_id, 0.8)
    tempo.run(coordinator.claim_next("tempo"))
    with pytest.raises(ValueError, match="explicitly retime"):
        compiler.compile(section.project_id, [replace(source, audio_variant="processed") for source in inputs])
    processed = tempo.selected(section, variant="processed")
    retimed = scenes.retime(accepted.id, section, processed)
    before, calls = snapshot(store), list(provider.calls)
    result = compiler.compile(section.project_id, [replace(source, timing_id=retimed.id, audio_variant="processed")
                                                  for source in inputs])
    assert result.id != original.id and result.duration > original.duration
    assert result.clips[-1].media.audio.end_sample == processed.frame_count
    assert all(c.media.timing_quality == "approximate_internal_positions" for c in result.clips)
    assert [c.media.image for c in result.clips] == [c.media.image for c in original.clips]
    assert snapshot(store) == before and provider.calls == calls
    assert compiler.compile(section.project_id, inputs) == original


def test_new_raw_audio_cannot_use_old_timing_even_for_same_section(setup, prepared):
    section, _, _, _, _, _, _, _, _, compiler, inputs = prepared
    synthesis, jobs, output = setup[-1], setup[1], setup[4]
    queued = synthesis.enqueue(section, {"variant": "changed"}, max_words=1)
    claim = JobCoordinator(jobs).claim_next("fixture")
    generate(jobs.get_job(queued.job_id), Provider(variant="changed"), output.root)
    synthesis.complete(claim)
    before = snapshot(setup[3])
    with pytest.raises(ValueError, match="exact selected audio"):
        compiler.compile(section.project_id, inputs)
    assert snapshot(setup[3]) == before


@pytest.mark.parametrize("kind", ["image", "audio", "missing_image", "missing_audio"])
def test_corrupt_or_missing_selected_bytes_fail(setup, prepared, kind):
    section, manifest, _, _, _, _, _, _, images, compiler, inputs = prepared
    artifact_id = images[0][0].artifact_id if "image" in kind else manifest.artifact_id
    store = setup[3]
    selected_manifest = next(m for m in store.list_artifacts() if m.artifact_id == artifact_id)
    path = store.root / selected_manifest.storage_key
    if kind.startswith("missing"):
        path.unlink()
    else:
        path.write_bytes(b"corrupt fixture")
    with pytest.raises((ValueError, FileNotFoundError)):
        compiler.compile(section.project_id, inputs)
