"""D017 real catalogs/jobs plus a deterministic provider, with no external calls."""

from dataclasses import replace
import json
from types import SimpleNamespace

import pytest

from app.application.image_generation import ImageGenerationService
from app.application.image_intake import ImageIntakeService
from app.application.projects import ProjectSession
from app.application.result_publication import ResultPublicationService
from app.application.scene_planning import ScenePlanningService
from app.application.script_generation import ScriptGenerationService
from app.application.visual_prompts import VisualPromptService
from app.domain.generation_job import AttemptStatus
from app.jobs.coordinator import JobCoordinator
from app.jobs.repository import JobRepository
from app.providers.mock_image import MockImageProvider
from app.storage.image_generation import ImageResultIndex, ProjectImageGeneration
from app.storage.local_store import LocalArtifactStore
from app.storage.project_repository import ProjectRepository
from app.storage.scene_plans import ProjectScenePlans
from app.tts.scene_sources import sentence_sources


class Provider(MockImageProvider):
    def __init__(self):
        self.calls, self.version = [], "1"
        self.during = lambda: None
        self.transform = lambda result: result

    def capabilities(self):
        return replace(super().capabilities(), version=self.version)

    def generate(self, request):
        self.calls.append(request)
        result = super().generate(request)
        self.during()
        return self.transform(result)


def compose(session):
    jobs = JobRepository(session.repository)
    index = ImageResultIndex(session.repository, jobs)
    store = LocalArtifactStore(index.root, index=index)
    adapter = ProjectImageGeneration(index, store)
    coordinator = JobCoordinator(jobs)
    provider = Provider()
    service = ImageGenerationService(ResultPublicationService(index, store), coordinator, adapter, provider)
    return SimpleNamespace(session=session, jobs=jobs, index=index, store=store, adapter=adapter,
                           coordinator=coordinator, provider=provider, service=service,
                           prompts=VisualPromptService(index.prompts))


@pytest.fixture
def project(tmp_path):
    with ProjectSession.create(tmp_path / "project", name="Images", repository_factory=ProjectRepository) as session:
        ScriptGenerationService(session).append_text("First.\n\nSecond.", title="Story", expected_active_revision_id=session.active_script.id)
        p = compose(session)
        scenes = ScenePlanningService(ProjectScenePlans(session.repository, p.store), sentence_sources)
        accepted = scenes.accept(scenes.suggest(session.active_script.sections[0]).id, reviewer_id="editor")
        brief = p.prompts.pin_context("film_brief", "A short film.")
        style = p.prompts.pin_context("visual_style", "Muted colors.")
        p.prompt = p.prompts.create_manual(accepted.id, accepted.plan.scenes[0].id, brief.id, style.id, "A red tree.")
        p.prompt_choice = p.prompts.select(p.prompt.id, expected_selection_id=None)
        p.scene_id = p.prompt.inputs.scene_id
        p.key = "scene:" + p.scene_id + ":image_candidate"
        yield p


def enqueue(p, **kwargs):
    return p.service.enqueue(p.prompt.id, **({"width": 32, "height": 24} | kwargs))


def run(p, **kwargs):
    submission = enqueue(p, **kwargs)
    assert submission.attempt is not None and submission.cached_artifact_id is None
    claim = p.coordinator.claim_next("fixture")
    assert claim.job_id == submission.attempt.job_id
    return p.service.run(claim), claim


def change_prompt(p):
    revision = p.prompts.edit_manual(p.prompt.id, "A blue tree.")
    chosen = p.index.prompts.selected(p.scene_id)
    return revision, p.prompts.select(revision.id, expected_selection_id=chosen.id)


def test_cache_force_and_old_variant_selection_preserve_audio_and_bytes(project):
    p = project
    audio = p.store.save_artifact("voice.wav", b"retained audio")
    one, claim = run(p)
    assert one.selected_at_publication and p.adapter.images.selected(p.scene_id) is None
    image = p.adapter.images.image(one.artifact_id)
    assert (image.format, image.width, image.height, image.provenance) == ("PNG", 32, 24, "generated")
    first_choice = p.service.select(one.artifact_id, expected_selection_id=None)
    cached = enqueue(p)
    assert cached.cached_artifact_id == one.artifact_id and cached.attempt is None
    assert len(p.provider.calls) == 1 and len(p.jobs.jobs()) == 1
    two, second_claim = run(p, force=True)
    assert one.artifact_id != two.artifact_id and claim.job_id != second_claim.job_id
    jobs = (p.jobs.get_job(claim.job_id), p.jobs.get_job(second_claim.job_id))
    assert jobs[0].request == jobs[1].request
    snapshots = [json.loads(job.input_snapshot_json)["desktop_publication"] for job in jobs]
    assert snapshots[0]["generation_id"] != snapshots[1]["generation_id"]
    assert p.adapter.images.selected(p.scene_id) == first_choice
    second_choice = p.service.select(two.artifact_id, expected_selection_id=first_choice.id)
    before = {m.storage_key: p.store.read_artifact(m.storage_key) for m in p.store.list_artifacts()}
    p.service.provider = None  # Historical variant choice needs no generation provider.
    restored = p.service.select(one.artifact_id, expected_selection_id=second_choice.id)
    assert p.adapter.images.selected(p.scene_id) == restored
    assert all(p.store.read_artifact(key) == data for key, data in before.items())
    assert p.store.read_artifact(audio.storage_key) == b"retained audio"
    assert len(p.provider.calls) == 2
    assert p.service.run(claim) == one and len(p.provider.calls) == 2
    assert p.index.selected()[p.key] == two.artifact_id


@pytest.mark.parametrize("settings", [{"seed": 2}, {"width": 16}, {"height": 16}, {"negative_prompt": "blur"}])
def test_settings_change_bypasses_cache(project, settings):
    one, first = run(project)
    submission = enqueue(project, **settings)
    assert submission.attempt is not None
    assert project.jobs.get_job(submission.attempt.job_id).request.fingerprint != project.jobs.get_job(first.job_id).request.fingerprint


def test_prompt_selection_and_provider_identity_are_fingerprinted(project):
    p = project
    run(p)
    p.provider.version = "2"
    assert enqueue(p).attempt is not None
    changed, _ = change_prompt(p)
    with pytest.raises(ValueError): enqueue(p)
    assert p.service.enqueue(changed.id, width=32, height=24).attempt is not None


@pytest.mark.parametrize("change", ["prompt", "section", "prompt_aba", "before_commit"])
def test_late_results_are_retained_without_becoming_current_candidates(project, monkeypatch, change):
    p = project
    old, _ = run(p)
    choice = p.service.select(old.artifact_id, expected_selection_id=None)
    def alter():
        if change == "section":
            p.session.edit_section(p.session.active_script.sections[0].section_id, text="New source.")
        else:
            _, event = change_prompt(p)
            if change == "prompt_aba":
                p.prompts.select(p.prompt.id, expected_selection_id=event.id)
    if change == "before_commit":
        clock = p.index.clock
        def changing_clock():
            alter()
            return clock()
        monkeypatch.setattr(p.index, "clock", changing_clock)
    else:
        p.provider.during = alter
    late, claim = run(p, force=True)
    assert not late.selected_at_publication
    assert late.reason == ("obsolete_revisions" if change == "section" else "obsolete_inputs")
    assert p.index.selected()[p.key] == old.artifact_id
    assert p.adapter.images.selected(p.scene_id) == choice
    assert p.adapter.images.image(late.artifact_id).provenance == "generated"
    assert p.jobs.get_attempt(claim.id).status == AttemptStatus.COMPLETED


def test_newer_generation_reservation_wins_and_replay_never_reselects(project):
    p = project
    p.provider.during = lambda: enqueue(p, force=True, seed=7)
    old, claim = run(p)
    assert old.reason == "superseded_generation" and not old.selected_at_publication
    p.provider.during = lambda: None
    newer_claim = p.coordinator.claim_next("fixture")
    newer = p.service.run(newer_claim)
    assert newer.selected_at_publication and newer.artifact_id != old.artifact_id
    assert p.service.run(claim) == old and p.index.selected()[p.key] == newer.artifact_id


@pytest.mark.parametrize("bad", ["bytes", "width", "format", "contract"])
def test_bad_provider_output_fails_without_changing_previous_choice(project, bad):
    p = project
    old, _ = run(p)
    choice = p.service.select(old.artifact_id, expected_selection_id=None)
    p.provider.transform = {
        "bytes": lambda r: replace(r, image_bytes=b"not an image"),
        "width": lambda r: replace(r, width=r.width + 1),
        "format": lambda r: replace(r, format="JPEG"),
        "contract": lambda r: {"image_bytes": r.image_bytes},
    }[bad]
    queued = enqueue(p, force=True)
    claim = p.coordinator.claim_next("fixture")
    before = p.store.list_artifacts()
    with pytest.raises(ValueError): p.service.run(claim)
    assert p.jobs.get_attempt(queued.attempt.id).status == AttemptStatus.FAILED
    assert p.store.list_artifacts() == before
    assert p.adapter.images.selected(p.scene_id) == choice and p.index.selected()[p.key] == old.artifact_id


def test_failure_retry_preserves_frozen_inputs_and_replay_does_not_call_provider(project):
    p = project
    queued = enqueue(p)
    claim = p.coordinator.claim_next("fixture")
    def fail(): raise RuntimeError("fixture failure")
    p.provider.during = fail
    with pytest.raises(RuntimeError): p.service.run(claim)
    job = p.jobs.get_job(claim.job_id)
    p.coordinator.retry(claim.job_id)
    retried = p.coordinator.claim_next("fixture")
    p.provider.during = lambda: None
    result = p.service.run(retried)
    assert p.jobs.get_job(retried.job_id) == job and retried.id != queued.attempt.id
    assert p.service.run(retried) == result and len(p.provider.calls) == 2


@pytest.mark.parametrize("when", ["before", "during"])
def test_cancel_prevents_publication(project, when):
    p = project
    enqueue(p)
    claim = p.coordinator.claim_next("fixture")
    if when == "before": p.coordinator.cancel(claim.id)
    else: p.provider.during = lambda: p.coordinator.cancel(claim.id)
    before = p.store.list_artifacts()
    with pytest.raises(ValueError): p.service.run(claim)
    assert p.jobs.get_attempt(claim.id).status == AttemptStatus.CANCELED
    assert p.store.list_artifacts() == before


@pytest.mark.parametrize("when", ["before", "during"])
def test_provider_identity_drift_is_rejected(project, when):
    p = project
    enqueue(p)
    claim = p.coordinator.claim_next("fixture")
    if when == "before": p.provider.version = "changed"
    else: p.provider.during = lambda: setattr(p.provider, "version", "changed")
    with pytest.raises(ValueError, match="identity changed"): p.service.run(claim)
    assert p.jobs.get_attempt(claim.id).status == AttemptStatus.FAILED
    assert p.index.history(p.key) == ()


def test_post_commit_cleanup_failure_returns_the_committed_outcome(project, monkeypatch):
    def fail(stage): raise OSError("cleanup failed after index commit")
    monkeypatch.setattr(project.store, "_discard_stage", fail)
    result, claim = run(project)
    assert result.selected_at_publication and project.jobs.get_attempt(claim.id).status == AttemptStatus.COMPLETED
    assert project.service.run(claim) == result and len(project.provider.calls) == 1


def test_corrupt_candidate_is_not_a_cache_hit(project):
    p = project
    result, _ = run(p)
    manifest = next(m for m in p.store.list_artifacts() if m.artifact_id == result.artifact_id)
    (p.store.root / manifest.storage_key).write_bytes(b"corrupt")
    assert enqueue(p).attempt is not None


def test_imported_choice_and_later_manual_choice_are_not_overwritten(project, tmp_path):
    p = project
    one, _ = run(p)
    chosen = p.service.select(one.artifact_id, expected_selection_id=None)
    source = tmp_path / "imported.png"
    source.write_bytes(p.provider.generate(p.provider.calls[0]).image_bytes)
    intake = ImageIntakeService(p.adapter.images)
    imported = intake.import_file(p.prompt.inputs.acceptance_id, p.scene_id, source)
    p.provider.during = lambda: intake.select(imported.artifact_id, expected_selection_id=chosen.id)
    late, _ = run(p, force=True)
    assert late.selected_at_publication  # Candidate only, not the scene image choice.
    assert p.adapter.images.selected(p.scene_id).artifact_id == imported.artifact_id


def test_reopen_retains_variants_cache_and_explicit_selection(project):
    p = project
    one, _ = run(p)
    two, _ = run(p, force=True, seed=3)
    chosen = p.service.select(one.artifact_id, expected_selection_id=None)
    root = p.session.repository.workspace
    p.session.close()
    with ProjectSession.open(root, repository_factory=ProjectRepository) as session:
        restored = compose(session)
        assert restored.adapter.images.selected(p.scene_id) == chosen
        assert {image.artifact_id for image in restored.adapter.images.history(p.scene_id)} == {one.artifact_id, two.artifact_id}
        assert restored.service.enqueue(p.prompt.id, width=32, height=24, seed=3).cached_artifact_id == two.artifact_id
        assert restored.provider.calls == []


def test_second_injected_adapter_can_publish_jpeg_without_core_changes(project):
    import io
    from PIL import Image
    from app.providers.image_generation import ImageGenerationCapabilities, ImageGenerationResult
    class JpegFixture:
        def capabilities(self):
            return ImageGenerationCapabilities("jpeg-fixture", "solid", "1", formats=("JPEG",))
        def generate(self, request):
            buffer = io.BytesIO()
            Image.new("RGB", (request.width, request.height), "red").save(buffer, format="JPEG")
            return ImageGenerationResult(buffer.getvalue(), "JPEG", request.width, request.height)
    project.service.provider = JpegFixture()
    result, _ = run(project, format="JPEG")
    assert project.adapter.images.image(result.artifact_id).format == "JPEG"
    assert project.provider.calls == []


@pytest.mark.parametrize("settings", [{"width": 0}, {"width": 2048}, {"format": "JPEG"}, {"seed": True}, {"force": "yes"}])
def test_invalid_or_unsupported_requests_do_not_enqueue_or_call_provider(project, settings):
    before = project.store.list_artifacts()
    with pytest.raises(ValueError): enqueue(project, **settings)
    assert project.jobs.jobs() == () and project.provider.calls == []
    assert project.store.list_artifacts() == before


def test_missing_provider_is_reported_without_creating_a_job(project):
    project.service.provider = None
    with pytest.raises(ValueError, match="configured provider"): enqueue(project)
    assert project.jobs.jobs() == ()


def test_index_transaction_failure_retains_prior_head_and_user_choice(project, monkeypatch):
    old, _ = run(project)
    choice = project.service.select(old.artifact_id, expected_selection_id=None)
    before = project.store.list_artifacts()
    select = project.index._select
    def fail(connection, output_key, artifact_id):
        select(connection, output_key, artifact_id)
        raise OSError("injected transaction failure")
    monkeypatch.setattr(project.index, "_select", fail)
    queued = enqueue(project, force=True)
    claim = project.coordinator.claim_next("fixture")
    with pytest.raises(OSError): project.service.run(claim)
    assert project.store.list_artifacts() == before
    assert project.jobs.get_attempt(queued.attempt.id).status == AttemptStatus.FAILED
    assert project.index.selected()[project.key] == old.artifact_id
    assert project.adapter.images.selected(project.scene_id) == choice


def test_obsolete_prompt_variant_can_be_chosen_explicitly(project):
    old, _ = run(project)
    choice = project.service.select(old.artifact_id, expected_selection_id=None)
    changed, _ = change_prompt(project)
    queued = project.service.enqueue(changed.id, width=32, height=24)
    claim = project.coordinator.claim_next("fixture")
    assert queued.attempt.job_id == claim.job_id
    new = project.service.run(claim)
    second = project.service.select(new.artifact_id, expected_selection_id=choice.id)
    project.service.select(old.artifact_id, expected_selection_id=second.id)
    assert project.adapter.images.selected(project.scene_id).artifact_id == old.artifact_id


def test_cache_rechecks_prompt_after_provider_capability_probe(project, monkeypatch):
    run(project)
    capabilities = project.provider.capabilities
    def changing_capabilities():
        change_prompt(project)
        return capabilities()
    monkeypatch.setattr(project.provider, "capabilities", changing_capabilities)
    with pytest.raises(ValueError): enqueue(project)
    assert len(project.jobs.jobs()) == 1
