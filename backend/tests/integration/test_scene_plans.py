"""D013 project retention and actual D012/D011 media integration."""

from dataclasses import replace
import json

import pytest

from app.application.projects import ProjectSession
from app.application.scene_planning import ScenePlanningService
from app.application.section_tempo import SectionTempoService
from app.domain.section_audio import SectionAudio
from app.jobs.coordinator import JobCoordinator
from app.storage.local_store import LocalArtifactStore
from app.storage.project_repository import ProjectRepository
from app.storage.scene_plans import ProjectScenePlans
from app.storage.section_tempo import SectionTempoArtifacts
from app.tts.scene_sources import sentence_sources
from tests.integration.test_section_audio import setup  # noqa: F401 - project fixture
from tests.integration.test_section_tempo import FFmpeg
from tests.integration.test_speech_boundary import publish


def service(setup):
    storage = ProjectScenePlans(setup[0].repository, setup[3])
    return storage, ScenePlanningService(storage, sentence_sources)


def test_real_D012_and_D011_audio_retime_retained_plan_and_reopen_without_changing_visuals(setup):
    section, raw_manifest, _, _ = publish(setup)
    raw = SectionAudio.from_manifest(raw_manifest)
    storage, scenes = service(setup)
    plan = scenes.suggest(section, raw)
    assert len(plan.scenes) == 2  # Paragraph boundary wins even though both are short.
    accepted = scenes.accept(plan.id, reviewer_id="editor")
    first = scenes.retime(accepted.id, section, raw)
    before = {m.storage_key: setup[3].read_artifact(m.storage_key) for m in setup[3].list_artifacts()}
    coordinator = JobCoordinator(setup[1])
    tempo = SectionTempoService(setup[-1].publication, coordinator,
                               SectionTempoArtifacts(setup[2], setup[3], process_runner=FFmpeg(), ffmpeg_locator=lambda _: "fixture"))
    tempo.enqueue(section, raw.artifact_id, 0.8)
    processed_result = tempo.run(coordinator.claim_next("tempo"))
    processed = tempo.selected(section, variant="processed")
    assert processed.artifact_id == processed_result.artifact_id
    second = scenes.retime(accepted.id, section, processed)
    assert second.audio_artifact_id != first.audio_artifact_id and second.frame_count > first.frame_count
    assert second.scenes[-1].end_frame == processed.frame_count
    assert [s.scene_id for s in first.scenes] == [s.scene_id for s in second.scenes] == [s.id for s in plan.scenes]
    assert storage.plan(plan.id) == accepted.plan == plan
    assert all(setup[3].read_artifact(key) == value for key, value in before.items())
    path = setup[0].repository.workspace
    setup[0].close()
    with ProjectSession.open(path, repository_factory=ProjectRepository) as reopened:
        retained = ProjectScenePlans(reopened.repository, LocalArtifactStore.for_project(reopened.repository))
        assert retained.plan(plan.id) == plan and retained.acceptance(accepted.id) == accepted
        assert retained.proposals(section.section_id) == (plan,)
        assert retained.acceptances(section.section_id) == (accepted,)
        assert retained.timing(first.id) == first and retained.timing(second.id) == second


def test_new_proposal_does_not_replace_explicit_acceptance_or_erase_older_proposals(setup):
    section, manifest, _, _ = publish(setup)
    storage, scenes = service(setup)
    first = scenes.suggest(section)
    accepted = scenes.accept(first.id, reviewer_id="editor")
    newer = scenes.suggest(section, SectionAudio.from_manifest(manifest),
                            semantic_breaks=(sentence_sources(section.text)[1].sentence_id,))
    assert first.id != newer.id and storage.acceptance(accepted.id).plan == first
    assert storage.proposals(section.section_id) == (first, newer)
    assert storage.plan(newer.id) == newer and storage.plan(first.id) == first
    before = len(setup[3].list_artifacts())
    with pytest.raises(ValueError): scenes.retime(newer.id, section, SectionAudio.from_manifest(manifest))
    assert len(setup[3].list_artifacts()) == before
    second_acceptance = scenes.accept(newer.id, reviewer_id="second editor")
    assert storage.acceptance(accepted.id) == accepted and storage.acceptance(second_acceptance.id).plan == newer
    assert storage.acceptances(section.section_id) == (accepted, second_acceptance)


def test_accepted_text_edit_requires_explicit_new_plan_and_retains_history(setup):
    section, manifest, _, _ = publish(setup)
    storage, scenes = service(setup)
    plan = scenes.suggest(section)
    accepted = scenes.accept(plan.id, reviewer_id="editor")
    setup[0].edit_section(section.section_id, text="Changed narration.")
    current = setup[0].active_script.section(section.section_id)
    before = setup[3].list_artifacts()
    with pytest.raises(ValueError): scenes.accept(plan.id, reviewer_id="other editor")
    with pytest.raises(ValueError): scenes.retime(accepted.id, current, SectionAudio.from_manifest(manifest))
    with pytest.raises(ValueError): scenes.suggest(section)
    assert setup[3].list_artifacts() == before
    assert storage.plan(plan.id) == plan and storage.acceptance(accepted.id) == accepted


def test_forged_or_corrupt_audio_is_rejected_without_persisting_scenes(setup):
    section, manifest, _, _ = publish(setup)
    _, scenes = service(setup)
    raw = SectionAudio.from_manifest(manifest)
    before = setup[3].list_artifacts()
    with pytest.raises(ValueError): scenes.suggest(section, replace(raw, artifact_id="unknown"))
    with pytest.raises(ValueError): scenes.suggest(section, replace(raw, frame_count=raw.frame_count + 1))
    path = setup[3].root / manifest.storage_key
    payload = path.read_bytes()
    path.write_bytes(payload[:-2] + b"\x02\x00")
    with pytest.raises(ValueError, match="bytes differ"): scenes.suggest(section, raw)
    assert setup[3].list_artifacts() == before


def test_cross_project_store_plan_and_section_are_rejected(setup, tmp_path):
    section, _, _, _ = publish(setup)
    storage, scenes = service(setup)
    plan = scenes.suggest(section)
    with ProjectSession.create(tmp_path / "other", name="Other", repository_factory=ProjectRepository) as other:
        with pytest.raises(ValueError): ProjectScenePlans(other.repository, setup[3])
        other_storage = ProjectScenePlans(other.repository, LocalArtifactStore.for_project(other.repository))
        with pytest.raises(ValueError): other_storage.plan(plan.id)
        with pytest.raises(ValueError): other_storage.save_plan(section, plan)
    with pytest.raises(ValueError): scenes.suggest(replace(section, project_id="another"))


def test_corrupt_proposal_cannot_be_accepted_and_has_no_new_history_entry(setup):
    section, _, _, _ = publish(setup)
    _, scenes = service(setup)
    plan = scenes.suggest(section)
    manifest = next(m for m in setup[3].list_artifacts() if m.metadata.get("value_id") == plan.id)
    path = setup[3].root / manifest.storage_key
    data = json.loads(path.read_bytes())
    data["scenes"][0]["visual_description"] = "tampered"
    path.write_text(json.dumps(data))
    before = setup[3].list_artifacts()
    with pytest.raises(ValueError, match="checksum"): scenes.accept(plan.id, reviewer_id="editor")
    assert setup[3].list_artifacts() == before


def test_index_failure_does_not_advertise_accepted_plan(setup, monkeypatch):
    section, _, _, _ = publish(setup)
    storage, scenes = service(setup)
    plan = scenes.suggest(section)
    before = setup[3].list_artifacts()
    def fail(*args): raise OSError("index unavailable")
    monkeypatch.setattr(setup[3]._index, "register", fail)
    with pytest.raises(OSError): scenes.accept(plan.id, reviewer_id="editor")
    assert setup[3].list_artifacts() == before
    assert storage.plan(plan.id) == plan


def test_pre_audio_accepted_plan_can_receive_first_measurements_without_replanning(setup):
    section, manifest, _, _ = publish(setup)
    storage, scenes = service(setup)
    plan = scenes.suggest(section)
    accepted = scenes.accept(plan.id, reviewer_id="editor")
    timing = scenes.retime(accepted.id, section, SectionAudio.from_manifest(manifest))
    assert plan.planning_audio_id is None
    assert storage.plan(plan.id) == plan and timing.plan_id == plan.id
