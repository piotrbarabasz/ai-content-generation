"""D018 consumes actual retained selections; synthetic media and no external process."""

from dataclasses import replace
from hashlib import sha256
import json
from types import SimpleNamespace

import pytest

from app.application.image_intake import ImageIntakeService
from app.application.image_upscale import ImageUpscaleService
from app.providers.image_upscale import ImageUpscaleCapabilities, ImageUpscaleResult
from app.application.projects import ProjectSession
from app.application.scene_planning import ScenePlanningService
from app.application.section_tempo import SectionTempoService
from app.application.timeline import TimelineCompiler, TimelineSceneInput
from app.domain.section_audio import SectionAudio
from app.domain.timeline import TimelineRevision
from app.domain.dependencies import content_fingerprint
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


def test_timeline_uses_selected_upscaled_derivative(setup, prepared):
    _, _, _, _, _, _, _, _, images, compiler, inputs = prepared
    from io import BytesIO
    from PIL import Image

    class Upscaler:
        def capabilities(self):
            return ImageUpscaleCapabilities("fake", "realesr-general-x4v3", "v1", "runtime")

        def upscale(self, request):
            output = BytesIO()
            Image.new("RGB", (request.target_width, request.target_height)).save(output, format="PNG")
            return ImageUpscaleResult(output.getvalue(), "PNG", request.target_width, request.target_height)

    source = images[0][0]
    service = ImageUpscaleService(ProjectSceneImages(setup[0].repository, setup[3]), setup[3], Upscaler())
    derivative_id = service.upscale_selected(source.scene_id, 2)
    compiled = compiler.compile(setup[0].project.id, inputs)
    assert compiled.clips[0].media.image.artifact_id == derivative_id
    assert compiled.clips[0].media.image.provenance == "upscaled"
    assert compiled.clips[0].media.image.source_artifact_id == source.artifact_id


def test_missing_image_is_not_replaced_by_an_unselected_candidate(setup, prepared):
    section, _, _, scenes, _, _, intake, source, _, compiler, _ = prepared
    accepted = scenes.accept(scenes.suggest(section).id, reviewer_id="editor")
    raw = SectionTempoArtifacts(setup[2], setup[3]).selected(section, "original")
    timing = scenes.retime(accepted.id, section, raw)
    scene = accepted.plan.scenes[0]
    intake.import_file(accepted.id, scene.id, source)  # Candidate only, no selection.
    with pytest.raises(ValueError, match="no selected image"):
        compiler.compile(section.project_id, [TimelineSceneInput(timing.id, scene.id, "original")])


def test_timeline_candidate_diagnostics_share_resolver_and_preserve_rejection_reason(setup, prepared):
    from app.desktop.timeline_composition import compose_timeline

    section, _, _, scenes, _, _, _, _, _, _, _ = prepared
    accepted = scenes.accept(scenes.suggest(section).id, reviewer_id="editor")
    raw = SectionTempoArtifacts(setup[2], setup[3]).selected(section, "original")
    timing = scenes.retime(accepted.id, section, raw)
    timeline = compose_timeline(setup[0])
    current = timeline.current_candidate_diagnostics()
    current_for_new = [item for item in current if item.timing_id == timing.id]
    assert len(current_for_new) == len(accepted.plan.scenes)
    assert all(not item.accepted for item in current_for_new)
    assert all(item.reason == "Timeline scene has no selected image." for item in current_for_new)


def test_timeline_diagnostics_report_current_valid_candidates(setup, prepared):
    from app.desktop.timeline_composition import compose_timeline

    timeline = compose_timeline(setup[0])
    candidates = timeline.current_candidate_diagnostics()
    assert candidates
    assert all(item.accepted and item.reason is None and item.media is not None for item in candidates)


def test_legacy_timeline_edit_loads_and_accepts_a_current_format_child(setup, prepared):
    from app.desktop.timeline_composition import compose_timeline
    from tests.unit.test_timeline import legacy_timeline_payload

    session, _, _, store, _, _ = setup
    legacy = legacy_timeline_payload()
    project_id = session.project.id
    legacy["project_id"] = project_id
    legacy["clips"][0]["media"]["project_id"] = project_id
    legacy["clips"][0]["media"]["image"]["project_id"] = project_id
    legacy["id"] = "timeline_" + content_fingerprint({key: value for key, value in legacy.items()
                                                        if key != "id"})
    old_event_id = "timeline_edit_legacy"
    payload = json.dumps({"version": 1, "id": old_event_id, "parent": None, "timeline": legacy})
    store.save_artifact("timeline-edit.json", payload, {
        "artifact_type": "desktop_timeline_edit", "project_id": project_id,
        "value_id": old_event_id, "module_name": "desktop_timeline",
    })

    timeline = compose_timeline(session)
    old = timeline.current()
    assert old.id == old_event_id and old.timeline.id == legacy["id"]
    candidates = timeline.current_candidate_diagnostics()
    assert len(candidates) == 2 and all(item.accepted for item in candidates)
    child = timeline.rebuild_from_sources(
        [item.source for item in candidates], expected=old.id)
    assert child.timeline.id != legacy["id"]
    assert "lineage_version" in child.timeline.to_payload()["clips"][0]["media"]["image"]

    reopened = compose_timeline(session)
    restored = reopened.current()
    assert restored == child
    assert TimelineRevision.from_payload(restored.timeline.to_payload()) == restored.timeline
    events = [item for item in store.list_artifacts() if item.artifact_type == "desktop_timeline_edit"]
    assert len(events) == 2


@pytest.mark.parametrize("corruption", ["branch", "duplicate", "missing_parent", "checksum", "foreign_project"])
def test_timeline_history_integrity_checks_still_reject_corruption(setup, corruption):
    from app.storage.timeline_edits import ProjectTimelineEdits
    from tests.unit.test_timeline import legacy_timeline_payload

    repository = setup[0].repository
    project_id = repository.project().id
    timeline = legacy_timeline_payload()
    timeline["project_id"] = project_id
    timeline["clips"][0]["media"]["project_id"] = project_id
    timeline["clips"][0]["media"]["image"]["project_id"] = project_id
    if corruption == "foreign_project":
        timeline["project_id"] = timeline["clips"][0]["media"]["project_id"] = "foreign"
        timeline["clips"][0]["media"]["image"]["project_id"] = "foreign"
    timeline["id"] = "timeline_" + content_fingerprint({key: value for key, value in timeline.items()
                                                        if key != "id"})

    specs = [("event-a", None)]
    if corruption == "branch":
        specs.append(("event-b", None))
    elif corruption == "duplicate":
        specs.append(("event-a", "event-a"))
    elif corruption == "missing_parent":
        specs = [("event-a", "missing-event")]
    manifests, values = [], {}
    for index, (event_id, parent) in enumerate(specs):
        body = json.dumps({"version": 1, "id": event_id, "parent": parent, "timeline": timeline}).encode()
        storage_key = f"{event_id}-{index}.json"
        checksum = sha256(body).hexdigest()
        if corruption == "checksum":
            checksum = "0" * 64
        manifests.append(SimpleNamespace(
            artifact_type="desktop_timeline_edit", storage_key=storage_key, checksum=checksum,
            metadata={"project_id": project_id, "value_id": event_id},
        ))
        values[storage_key] = body

    class ReadOnlyStore:
        def __init__(self):
            self._index = SimpleNamespace(repository=repository)
        def list_artifacts(self): return tuple(manifests)
        def read_artifact(self, key): return values[key]

    edits = ProjectTimelineEdits(repository, ReadOnlyStore())
    message = "checksum mismatch" if corruption == "checksum" else (
        "Incomplete timeline history" if corruption == "missing_parent" else "Invalid or branching timeline history")
    with pytest.raises(ValueError, match=message):
        edits.current()


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
