"""Project-owned reference WAV intake and append-only approval history."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
import os
from pathlib import Path
import tempfile

from app.domain.base import new_id
from app.domain.dependencies import canonical_json
from app.domain.reference_audio import (
    ApprovedReferenceAudioChoice, ReferenceAudioDecision, ReferenceAudioSource,
)
from app.tts.assembly import inspect_pcm_wav
from app.tts.reference_audio import ApprovedReferenceAudio, cached_reference_path
from .local_store import CHUNK_SIZE
from .paths import import_path, storage_root


@dataclass(frozen=True, slots=True)
class ReferenceAudioLimits:
    max_bytes: int = 64 * 1024 * 1024
    min_duration_seconds: float = .25
    max_duration_seconds: float = 120.0
    min_sample_rate: int = 8_000
    max_sample_rate: int = 192_000
    max_channels: int = 2


class ProjectReferenceAudio:
    def __init__(self, repository, store, runtime_root, *, limits=ReferenceAudioLimits()):
        self.repository, self.store = repository, store
        self.project_id = repository.project().id
        self.runtime_root = storage_root(runtime_root)
        self.runtime_root.mkdir(parents=True, exist_ok=True)
        self.limits = limits

    def _publish(self, name, payload, artifact_type, value_id, metadata):
        if any(item.metadata.get("value_id") == value_id for item in self.store.list_artifacts()):
            raise ValueError("Reference-audio publication identity already exists.")
        metadata = {"artifact_type": artifact_type, "project_id": self.project_id,
                    "value_id": value_id, "module_name": "desktop_reference_audio", **metadata}
        try:
            return self.store.save_artifact(name, payload, metadata)
        except Exception:
            committed = None
            try:
                matches = [item for item in self.store.list_artifacts()
                           if item.artifact_type == artifact_type
                           and item.metadata.get("value_id") == value_id
                           and item.metadata.get("project_id") == self.project_id]
                if len(matches) == 1 and matches[0].checksum == sha256(payload).hexdigest():
                    if self.store.read_artifact(matches[0].storage_key) == payload:
                        committed = matches[0]
            except Exception:
                pass
            if committed is not None:
                return committed
            raise

    def import_file(self, source, *, source_root=None):
        path = import_path(source, source_root)
        chunks, size = [], 0
        with path.open("rb") as stream:
            while size <= self.limits.max_bytes:
                chunk = stream.read(min(CHUNK_SIZE, self.limits.max_bytes + 1 - size))
                if not chunk:
                    break
                chunks.append(chunk)
                size += len(chunk)
        if size > self.limits.max_bytes:
            raise ValueError("Reference audio exceeds the configured intake limit.")
        payload = b"".join(chunks)
        parameters, _ = inspect_pcm_wav(payload)
        duration = parameters.duration_seconds
        if (not self.limits.min_duration_seconds <= duration <= self.limits.max_duration_seconds
                or not self.limits.min_sample_rate <= parameters.sample_rate <= self.limits.max_sample_rate
                or parameters.channels > self.limits.max_channels):
            raise ValueError("Reference WAV duration, sample rate or channel count is unsupported.")
        if self.repository.project().id != self.project_id:
            raise ValueError("Reference-audio project changed during intake.")
        metadata = {"version": 1, "project_id": self.project_id, "source_name": path.name,
                    "duration_seconds": duration, "sample_rate": parameters.sample_rate,
                    "channels": parameters.channels, "sample_width": parameters.sample_width}
        value_id = new_id("reference_source")
        manifest = self._publish("reference-audio.wav", payload, "reference_audio_source", value_id,
                                 {"reference_audio": metadata})
        return ReferenceAudioSource.from_manifest(manifest)

    def source(self, artifact_id):
        matches = [item for item in self.store.list_artifacts()
                   if item.artifact_id == artifact_id and item.artifact_type == "reference_audio_source"
                   and item.metadata.get("project_id") == self.project_id]
        if len(matches) != 1:
            raise ValueError("Unknown reference-audio artifact in this project.")
        source = ReferenceAudioSource.from_manifest(matches[0])
        with self.store.open_artifact_id(artifact_id) as stream:
            payload = stream.read(self.limits.max_bytes + 1)
        parameters, _ = inspect_pcm_wav(payload)
        if (len(payload) != source.size_bytes or len(payload) > self.limits.max_bytes
                or sha256(payload).hexdigest() != source.checksum
                or (parameters.duration_seconds, parameters.sample_rate, parameters.channels, parameters.sample_width)
                != (source.duration_seconds, source.sample_rate, source.channels, source.sample_width)):
            raise ValueError("Reference audio bytes differ from retained measurements.")
        return source

    def sources(self):
        manifests = sorted(self.store.list_artifacts(), key=lambda item: (item.created_at, item.artifact_id))
        return tuple(self.source(item.artifact_id) for item in manifests
                     if item.artifact_type == "reference_audio_source"
                     and item.metadata.get("project_id") == self.project_id)

    def decision_history(self, reference_id):
        source = self.source(reference_id)
        children = {}
        for manifest in self.store.list_artifacts():
            if (manifest.artifact_type != "reference_audio_decision"
                    or manifest.metadata.get("reference_id") != reference_id):
                continue
            payload = self.store.read_artifact(manifest.storage_key)
            if sha256(payload).hexdigest() != manifest.checksum:
                raise ValueError("Reference-audio decision checksum mismatch.")
            decision = ReferenceAudioDecision.from_payload(json.loads(payload))
            if (decision.project_id != self.project_id or decision.reference_id != reference_id
                    or decision.source_checksum != source.checksum
                    or manifest.metadata.get("value_id") != decision.id
                    or decision.parent_decision_id in children):
                raise ValueError("Invalid or branching reference-audio approval history.")
            children[decision.parent_decision_id] = decision
        chain, parent = [], None
        while parent in children:
            decision = children.pop(parent)
            chain.append(decision)
            parent = decision.id
        if children:
            raise ValueError("Incomplete reference-audio approval history.")
        return tuple(chain)

    def decision(self, reference_id):
        history = self.decision_history(reference_id)
        return history[-1] if history else None

    def decide(self, reference_id, status, label):
        source = self.source(reference_id)
        previous = self.decision(reference_id)
        decision = ReferenceAudioDecision(new_id("reference_decision"), self.project_id,
                                           reference_id, source.checksum, status, label,
                                           previous.id if previous else None)
        payload = canonical_json(decision.to_payload()).encode("utf-8")
        self._publish("reference-decision.json", payload, "reference_audio_decision", decision.id,
                      {"reference_id": reference_id})
        return decision

    def approve(self, reference_id, label):
        return self.decide(reference_id, "approved", label)

    def reject(self, reference_id, label):
        return self.decide(reference_id, "rejected", label)

    def approved(self):
        result = []
        for source in self.sources():
            decision = self.decision(source.artifact_id)
            if decision is not None and decision.status == "approved":
                result.append(ApprovedReferenceAudioChoice(source.artifact_id, source.source_name,
                                                            source.checksum, decision.label,
                                                            source.duration_seconds))
        return tuple(result)

    def resolve(self, artifact_id):
        source = self.source(artifact_id)
        decision = self.decision(artifact_id)
        if decision is None or decision.status != "approved" or decision.source_checksum != source.checksum:
            return None
        with self.store.open_artifact_id(artifact_id) as stream:
            payload = stream.read(self.limits.max_bytes + 1)
        if len(payload) != source.size_bytes or sha256(payload).hexdigest() != source.checksum:
            raise ValueError("Approved reference audio changed after approval.")
        destination = cached_reference_path(self.runtime_root, artifact_id, source.checksum)
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            if destination.read_bytes() != payload:
                raise ValueError("Controlled reference-audio cache was modified.")
        else:
            temporary = None
            try:
                with tempfile.NamedTemporaryFile(mode="wb", dir=destination.parent,
                                                 prefix="reference-", suffix=".pending", delete=False) as output:
                    temporary = Path(output.name)
                    output.write(payload)
                    output.flush()
                    os.fsync(output.fileno())
                os.rename(temporary, destination)
            finally:
                if temporary is not None:
                    temporary.unlink(missing_ok=True)
        return ApprovedReferenceAudio(
            destination, source.checksum, decision.label, True, source.artifact_id)
