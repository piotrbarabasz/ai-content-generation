"""Actual immutable artifacts, source selections and reopened SQLite history."""

from dataclasses import replace

import pytest

from app.application.projects import ProjectSession
from app.desktop.timeline_composition import compose_timeline
from app.storage.project_repository import ProjectRepository
from tests.integration.test_timeline import prepared, setup, snapshot  # noqa: F401
from tests.integration.test_image_intake import encoded


def test_edits_reopen_and_selection_changes_preserve_original_media(setup, prepared):
    session, _, _, store, _, _ = setup
    service = compose_timeline(session)
    inputs = prepared[-1]
    assert {s for _, s in service.candidates()} == set(inputs)
    before = snapshot(store)
    edit = None
    for source in inputs:
        edit = service.append(source, expected=edit.id if edit else None)
    original = edit
    clip = edit.timeline.clips[0]
    edges = service.media.boundaries(clip.media)
    assert clip.media.audio.start_sample in edges and clip.media.audio.end_sample in edges
    with pytest.raises(ValueError, match="boundaries"):
        service.set_range(clip.media.scene_id, edges[0] + 1, edges[-1], expected=edit.id)
    assert service.current() == original
    source = prepared[7]
    source.write_bytes(encoded(size=(19, 13)))
    prepared[6].import_and_select(prepared[4].id, clip.media.scene_id, source,
                                  expected_selection_id=prepared[8][0][1].id)
    edit = service.move(clip.media.scene_id, len(inputs) - 1, expected=edit.id)
    assert edit.timeline.clips[-1].media == clip.media
    assert all(snapshot(store)[key] == value for key, value in before.items())
    workspace = session.repository.workspace
    session.close()
    with ProjectSession.open(workspace, repository_factory=ProjectRepository) as reopened:
        assert compose_timeline(reopened).current() == edit


def test_stale_writes_and_publication_failure_keep_history(setup, prepared, monkeypatch):
    service = compose_timeline(setup[0])
    first = service.append(prepared[-1][0], expected=None)
    second = service.append(prepared[-1][1], expected=first.id)
    with pytest.raises(ValueError, match="changed"):
        service.history.save(first.timeline, first.id)
    save = service.history.store.save_artifact

    def fail(*args, **kwargs):
        raise OSError("disk unavailable")

    monkeypatch.setattr(service.history.store, "save_artifact", fail)
    with pytest.raises(OSError):
        service.move(second.timeline.clips[0].media.scene_id, 1, expected=second.id)
    assert service.current() == second

    def committed(*args, **kwargs):
        save(*args, **kwargs)
        raise OSError("staging cleanup failed")

    monkeypatch.setattr(service.history.store, "save_artifact", committed)
    moved = service.move(second.timeline.clips[0].media.scene_id, 1, expected=second.id)
    assert service.current() == moved


def test_approximate_boundaries_offer_no_new_internal_cut(setup, prepared, monkeypatch):
    service = compose_timeline(setup[0])
    edit = service.append(prepared[-1][0], expected=None)
    clip = edit.timeline.clips[0]
    read = service.media.audio._read

    def approximate(artifact_id):
        manifest, audio, payload = read(artifact_id)
        return manifest, replace(audio, speech_boundary_map=None), payload

    monkeypatch.setattr(service.media.audio, "_read", approximate)
    assert service.media.boundaries(clip.media) == (clip.media.audio.start_sample, clip.media.audio.end_sample)


def test_measured_sentence_trim_round_trip_rejects_technical_chunk_edge(setup, tmp_path):
    from app.application.image_intake import ImageIntakeService
    from app.application.scene_planning import ScenePlanningService
    from app.application.timeline import TimelineSceneInput
    from app.domain.section_audio import SectionAudio
    from app.storage.scene_images import ProjectSceneImages
    from app.storage.scene_plans import ProjectScenePlans
    from app.tts.scene_sources import sentence_sources
    from tests.integration.test_speech_boundary import publish

    section, manifest, provider, _ = publish(setup, text="First two three four five six. Second sentence!")
    scenes = ScenePlanningService(ProjectScenePlans(setup[0].repository, setup[3]), sentence_sources)
    accepted = scenes.accept(scenes.suggest(section).id, reviewer_id="editor")
    audio = SectionAudio.from_manifest(manifest)
    timing = scenes.retime(accepted.id, section, audio)
    assert len(timing.scenes) == 1
    source = tmp_path / "still.png"
    source.write_bytes(encoded())
    scene_id = timing.scenes[0].scene_id
    ImageIntakeService(ProjectSceneImages(setup[0].repository, setup[3])).import_and_select(
        accepted.id, scene_id, source, expected_selection_id=None)
    service = compose_timeline(setup[0])
    edit = service.append(TimelineSceneInput(timing.id, scene_id, "original"), expected=None)
    mapping = audio.speech_boundary_map
    with pytest.raises(ValueError, match="boundaries"):
        service.set_range(scene_id, mapping.chunks[0].end_frame, audio.frame_count, expected=edit.id)
    before, calls = snapshot(setup[3]), list(provider.calls)
    trimmed = service.set_range(scene_id, mapping.blocks[1].start_frame, audio.frame_count, expected=edit.id)
    assert trimmed.timeline.clips[0].media.audio.start_sample == mapping.blocks[1].start_frame
    assert trimmed.timeline.duration < edit.timeline.duration
    assert compose_timeline(setup[0]).current() == trimmed
    assert provider.calls == calls and all(snapshot(setup[3])[key] == value for key, value in before.items())
    restored = service.set_range(scene_id, 0, audio.frame_count, expected=trimmed.id)
    assert restored.timeline == edit.timeline and restored.id != edit.id


def test_corrupt_history_fails_closed(setup, prepared):
    service = compose_timeline(setup[0])
    event = service.append(prepared[-1][0], expected=None)
    manifest = next(m for m in setup[3].list_artifacts() if m.metadata.get("value_id") == event.id)
    # Corrupt only the private test project's retained snapshot, never real media.
    with setup[3].open_artifact(manifest.storage_key) as stream:
        path = stream.name
    from pathlib import Path
    Path(path).write_bytes(b"corrupt")
    with pytest.raises(ValueError, match="checksum"):
        service.current()
