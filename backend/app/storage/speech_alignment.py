"""Immutable D031 alignment artifacts tied to retained section audio."""

from __future__ import annotations

from hashlib import sha256
import json

from app.domain.dependencies import (DEPENDENCY_METADATA_KEY, DependencyDeclaration,
                                     InputEdge, RequestFingerprint, canonical_json)
from app.domain.section_audio import SectionAudio
from app.domain.speech_alignment import SpeechAlignment
from app.tts.assembly import inspect_pcm_wav


class ProjectSpeechAlignments:
    artifact_type = "desktop_speech_alignment"

    def __init__(self, repository, store):
        if store._index is None or store._index.repository is not repository:
            raise ValueError("Speech alignments require the owning project artifact store.")
        self.repository, self.store = repository, store
        self.project_id = repository.project().id

    def _current(self, section):
        if (section.project_id != self.project_id
                or self.repository.active_script().section(section.section_id) != section):
            raise ValueError("Speech alignment requires the current project section revision.")

    def inputs(self, section, audio):
        self._current(section)
        if (audio.section_id, audio.revision_id) != (section.section_id, section.id):
            raise ValueError("Alignment audio belongs to another section revision.")
        matches = [item for item in self.store.list_artifacts()
                   if item.artifact_id == audio.artifact_id]
        if len(matches) != 1 or SectionAudio.from_manifest(matches[0]) != audio:
            raise ValueError("Alignment audio is not the retained SectionAudio artifact.")
        payload = self.store.read_artifact(matches[0].storage_key)
        parameters, _ = inspect_pcm_wav(payload)
        if (sha256(payload).hexdigest() != audio.checksum
                or (parameters.sample_rate, parameters.frame_count)
                != (audio.sample_rate, audio.frame_count)):
            raise ValueError("Alignment audio bytes differ from retained measurements.")
        boundary = audio.speech_boundary_map
        if boundary is None:
            raise ValueError("Word alignment requires the retained D012 sentence map.")
        boundary.validate_source(section.text, audio.checksum, audio.sample_rate, audio.frame_count)
        try:
            audio_declaration = DependencyDeclaration.from_payload(
                matches[0].metadata[DEPENDENCY_METADATA_KEY])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("Alignment audio lacks retained dependency identity.") from exc
        edge = InputEdge.artifact(
            "section_audio", audio_declaration.output_key, audio.artifact_id, audio.checksum)
        return (payload,
                tuple(chunk.source for chunk in boundary.chunks
                      if chunk.source.start == chunk.source.sentence_start),
                edge)

    def save(self, section, audio, alignment, request):
        audio_bytes, spans, audio_edge = self.inputs(section, audio)
        del audio_bytes
        alignment.validate_source(section, audio, spans)
        if not isinstance(request, RequestFingerprint):
            raise ValueError("Speech alignment dependency identity is invalid.")
        expected_edges = {edge.name: edge for edge in request.inputs}
        if (request.operation != "speech_alignment.align"
                or request.algorithm_version != "1"
                or expected_edges.get("section_audio") != audio_edge):
            raise ValueError("Speech alignment dependency identity is invalid.")
        if any(item.metadata.get("value_id") == alignment.id for item in self.store.list_artifacts()):
            raise ValueError("Speech alignment identity already exists.")
        payload = canonical_json(alignment.to_payload()).encode("utf-8")
        dependency = DependencyDeclaration(
            f"section:{section.section_id}:speech_alignment", request).to_metadata()
        metadata = {"artifact_type": self.artifact_type, "artifact_version": "1",
                    "module_name": "desktop_speech_alignment", "project_id": self.project_id,
                    "value_id": alignment.id, "section_id": section.section_id,
                    "revision_id": section.id, "audio_artifact_id": audio.artifact_id,
                    "audio_checksum": audio.checksum, "outcome": alignment.outcome,
                    "coverage": alignment.coverage,
                    "provider_identity": request.to_payload()["effective_identity"], **dependency}
        try:
            return self.store.save_artifact("speech-alignment.json", payload, metadata)
        except Exception:
            # Cleanup may fail after D004 committed the immutable bytes/index. Treat
            # exactly one matching registered payload as the successful publication.
            try:
                matches = [item for item in self.store.list_artifacts()
                           if item.artifact_type == self.artifact_type
                           and item.metadata.get("value_id") == alignment.id]
                if (len(matches) == 1 and matches[0].checksum == sha256(payload).hexdigest()
                        and self.store.read_artifact(matches[0].storage_key) == payload):
                    return matches[0]
            except Exception:
                pass
            raise

    def alignment(self, alignment_id):
        matches = [item for item in self.store.list_artifacts()
                   if item.artifact_type == self.artifact_type
                   and item.metadata.get("value_id") == alignment_id
                   and item.metadata.get("project_id") == self.project_id]
        if len(matches) != 1:
            raise ValueError("Unknown or ambiguous speech alignment artifact.")
        manifest = matches[0]
        payload = self.store.read_artifact(manifest.storage_key)
        if sha256(payload).hexdigest() != manifest.checksum:
            raise ValueError("Speech alignment artifact checksum mismatch.")
        alignment = SpeechAlignment.from_payload(json.loads(payload))
        identity = manifest.metadata.get("provider_identity")
        try:
            declaration = DependencyDeclaration.from_payload(
                manifest.metadata[DEPENDENCY_METADATA_KEY])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("Speech alignment dependency metadata is invalid.") from exc
        edges = {edge.name: edge for edge in declaration.request.inputs}
        audio_edge = edges.get("section_audio")
        if (alignment.id != alignment_id
                or not isinstance(identity, dict)
                or identity.get("provider") != alignment.provider
                or identity.get("model") != alignment.model
                or declaration.output_key != f"section:{alignment.section_id}:speech_alignment"
                or declaration.request.operation != "speech_alignment.align"
                or audio_edge is None or audio_edge.artifact_id != alignment.audio_artifact_id
                or declaration.request.to_payload()["settings"] != {
                    "language": alignment.language,
                    "confidence_threshold": alignment.confidence_threshold}
                or declaration.request.to_payload()["effective_identity"] != identity
                or manifest.metadata.get("section_id") != alignment.section_id
                or manifest.metadata.get("revision_id") != alignment.revision_id
                or manifest.metadata.get("audio_artifact_id") != alignment.audio_artifact_id
                or manifest.metadata.get("audio_checksum") != alignment.audio_checksum
                or manifest.metadata.get("outcome") != alignment.outcome
                or manifest.metadata.get("coverage") != alignment.coverage):
            raise ValueError("Speech alignment index metadata differs from its payload.")
        return alignment

    def history(self, section_id):
        manifests = sorted(self.store.list_artifacts(), key=lambda item: (item.created_at, item.artifact_id))
        return tuple(self.alignment(item.metadata["value_id"]) for item in manifests
                     if item.artifact_type == self.artifact_type
                     and item.metadata.get("project_id") == self.project_id
                     and item.metadata.get("section_id") == section_id)

    def latest(self, section_id, *, revision_id=None, audio_artifact_id=None):
        history = tuple(value for value in self.history(section_id)
                        if (revision_id is None or value.revision_id == revision_id)
                        and (audio_artifact_id is None or value.audio_artifact_id == audio_artifact_id))
        return history[-1] if history else None


__all__ = ["ProjectSpeechAlignments"]
