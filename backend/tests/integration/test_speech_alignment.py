"""D031 immutable alignment publication over retained D012 audio."""

from dataclasses import replace

import pytest

from app.application.speech_alignment import SpeechAlignmentService
from app.desktop.alignment_composition import compose_whisperx_alignment
from app.domain.section_audio import SectionAudio
from app.domain.dependencies import DependencyDeclaration
from app.providers.whisperx_alignment import WhisperXAlignmentAdapter, WhisperXAlignmentError
from app.application.projects import ProjectSession
from app.storage.local_store import LocalArtifactStore
from app.storage.project_repository import ProjectRepository
from app.storage.speech_alignment import ProjectSpeechAlignments
from tests.integration.test_section_audio import setup  # noqa: F401 - shared project fixture
from tests.integration.test_speech_boundary import publish


TEXT = "First known words. Second sentence here."


def timed_backend(audio_bytes, text, language):
    assert audio_bytes.startswith(b"RIFF") and text == TEXT and language == "en"
    words = []
    for index, value in enumerate(("First", "known", "words", "Second", "sentence", "here")):
        words.append({"word": value, "start": .05 + index * .12,
                      "end": .14 + index * .12,
                      "score": .45 if value == "sentence" else .95})
    words.pop(2)  # Explicit source omission: "words".
    return {"segments": [{"words": words}]}


def services(setup):
    section, manifest, _, _ = publish(setup, TEXT, max_words=2)
    audio = SectionAudio.from_manifest(manifest)
    artifacts = ProjectSpeechAlignments(setup[0].repository, setup[3])
    provider = WhisperXAlignmentAdapter(
        timed_backend, model_name="fixture-wav2vec2", runtime_version="fixture-1")
    return section, audio, artifacts, SpeechAlignmentService(
        artifacts, provider, confidence_threshold=.6)


def test_known_text_alignment_publishes_versioned_quality_artifact(setup):
    section, audio, artifacts, service = services(setup)
    result = service.align(section, audio, language="en")
    assert result.outcome == "omissions_and_low_confidence"
    assert result.coverage == pytest.approx(5 / 6)
    assert [section.text[word.source_start:word.source_end] for word in result.words] == [
        "First", "known", "words", "Second", "sentence", "here"]
    assert result.words[2].status == "omitted"
    assert result.words[4].status == "low_confidence"
    assert result.words[0].sentence_id != result.words[3].sentence_id
    assert artifacts.alignment(result.id) == result
    manifest = next(item for item in setup[3].list_artifacts()
                    if item.artifact_type == "desktop_speech_alignment")
    assert manifest.artifact_version == "1"
    assert manifest.metadata["outcome"] == result.outcome
    assert manifest.metadata["audio_artifact_id"] == audio.artifact_id
    dependency = DependencyDeclaration.from_payload(manifest.metadata["desktop_dependencies"])
    assert dependency.output_key == f"section:{section.section_id}:speech_alignment"
    assert {edge.name for edge in dependency.request.inputs} == {
        "section_revision", "section_audio"}


def test_provider_failure_or_invalid_timing_retains_previous_alignment(setup):
    section, audio, artifacts, service = services(setup)
    previous = service.align(section, audio, language="en")
    service.provider = WhisperXAlignmentAdapter(
        lambda *_: (_ for _ in ()).throw(RuntimeError("runtime failed")))
    with pytest.raises(WhisperXAlignmentError, match="failed"):
        service.align(section, audio, language="en")
    assert artifacts.latest(section.section_id, revision_id=section.id,
                            audio_artifact_id=audio.artifact_id) == previous
    assert artifacts.history(section.section_id) == (previous,)

    service.provider = WhisperXAlignmentAdapter(lambda *_: {"segments": [{"words": [
        {"word": "First", "start": 999, "end": 1000, "score": .9},
    ]}]})
    with pytest.raises(ValueError, match="WAV"):
        service.align(section, audio, language="en")
    assert artifacts.history(section.section_id) == (previous,)


def test_publication_failure_retains_previous_alignment(setup, monkeypatch):
    section, audio, artifacts, service = services(setup)
    previous = service.align(section, audio, language="en")

    def failed_save(*args, **kwargs):
        raise OSError("publication failed")

    monkeypatch.setattr(artifacts.store, "save_artifact", failed_save)
    with pytest.raises(OSError, match="publication failed"):
        service.align(section, audio, language="en")
    assert artifacts.history(section.section_id) == (previous,)


def test_changed_text_audio_or_registered_bytes_cannot_reuse_alignment(setup):
    section, audio, artifacts, service = services(setup)
    result = service.align(section, audio, language="en")
    with pytest.raises(ValueError, match="section text"):
        result.validate_source(replace(section, text=section.text + " changed"), audio,
                               audio.speech_boundary_map.blocks)
    changed_audio = replace(audio, checksum="b" * 64)
    with pytest.raises(ValueError, match="selected audio"):
        result.validate_source(section, changed_audio, audio.speech_boundary_map.blocks)
    manifest = next(item for item in setup[3].list_artifacts()
                    if item.artifact_id == audio.artifact_id)
    (setup[3].root / manifest.storage_key).write_bytes(b"changed")
    with pytest.raises(ValueError):
        artifacts.inputs(section, audio)


def test_alignment_history_survives_project_reopen(setup):
    section, audio, artifacts, service = services(setup)
    result = service.align(section, audio, language="en")
    workspace = setup[0].repository.workspace
    setup[0].close()
    with ProjectSession.open(workspace, repository_factory=ProjectRepository) as reopened:
        restored = ProjectSpeechAlignments(
            reopened.repository, LocalArtifactStore.for_project(reopened.repository))
        assert restored.alignment(result.id) == result
        assert restored.latest(section.section_id, revision_id=section.id,
                               audio_artifact_id=audio.artifact_id) == result


def test_opt_in_composition_uses_the_project_artifact_index(setup):
    section, manifest, _, _ = publish(setup, TEXT, max_words=2)
    audio = SectionAudio.from_manifest(manifest)
    service = compose_whisperx_alignment(
        setup[0], timed_backend, model_name="fixture-wav2vec2",
        runtime_version="fixture-1", confidence_threshold=.6)
    result = service.align(section, audio, language="en")
    assert result.audio_artifact_id == audio.artifact_id
    assert result.provider == "whisperx" and result.model == "fixture-wav2vec2"
