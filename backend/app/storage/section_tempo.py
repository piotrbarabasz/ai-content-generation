"""D011 adapter over the existing project artifact index and FFmpeg processor."""

from contextlib import contextmanager
from hashlib import sha256
import io
import json
import os
import subprocess

from .paths import contained_path

from app.domain.dependencies import content_fingerprint
from app.domain.publication import PublicationSnapshot
from app.domain.section_audio import SectionAudio
from app.tts.assembly import inspect_pcm_wav
from app.tts.post_processing import TEMPO_PROCESSOR_VERSION, process_pcm_wav_tempo, validate_tempo


def _run_ffmpeg(command, **kwargs):
    return subprocess.run(command, timeout=120,
                          creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0, **kwargs)


class SectionTempoArtifacts:
    def __init__(self, index, store, *, process_runner=None, ffmpeg_locator=None):
        if store.root.resolve() != index.root.resolve() or store._index is not index:
            raise ValueError("Tempo publication must use the same project artifact index/store.")
        self.index, self.store = index, store
        self.process_runner = process_runner or _run_ffmpeg
        self.ffmpeg_locator = ffmpeg_locator
        self.work_root = index.repository.workspace / "work" / "tempo"

    def _manifest(self, artifact_id):
        return next((m for m in self.index.manifests() if m.artifact_id == artifact_id), None)

    def _read(self, artifact_id):
        manifest = self._manifest(artifact_id)
        if manifest is None:
            raise ValueError("Audio artifact is not registered in this project.")
        audio = SectionAudio.from_manifest(manifest)
        with self.store.open_artifact(manifest.storage_key) as source:
            payload = source.read()
        parameters, _ = inspect_pcm_wav(payload)
        if (sha256(payload).hexdigest() != audio.checksum
                or parameters.to_payload() != manifest.metadata["section_audio"]["audio_parameters"]):
            raise ValueError("Retained audio bytes differ from their registered measurements.")
        return manifest, audio, payload

    def raw(self, section, artifact_id):
        manifest, audio, _ = self._read(artifact_id)
        key = "section:" + section.section_id + ":audio:raw"
        if ("audio_derivative" in manifest.metadata or audio.section_id != section.section_id
                or audio.revision_id != section.id or section.project_id != self.index.project_id
                or self.index.selected().get(key) != artifact_id
                or self.index.repository.active_script().section(section.section_id) != section):
            raise ValueError("Tempo input must be the selected raw audio for the expected current section.")
        if audio.speech_boundary_map is not None:
            audio.speech_boundary_map.validate_source(section.text, audio.checksum, audio.sample_rate, audio.frame_count)
        return audio

    def settings(self, raw, tempo):
        value = {"raw_checksum": raw.checksum, "tempo": validate_tempo(tempo),
                 "processor_version": TEMPO_PROCESSOR_VERSION}
        return value | {"derivative_key": content_fingerprint(value)}

    @contextmanager
    def processed(self, job):
        snapshot = PublicationSnapshot.from_job(job)
        if len(snapshot.sections) != 1:
            raise ValueError("A tempo derivative requires exactly one section revision.")
        section = snapshot.sections[0]
        edge = next(e for e in job.request.inputs if e.name == "raw_audio")
        manifest, raw, payload = self._read(edge.artifact_id)
        settings = json.loads(job.request.settings_json)
        expected = self.settings(raw, settings["tempo"])
        if ("audio_derivative" in manifest.metadata or raw.section_id != section.section_id or raw.revision_id != section.id
                or snapshot.project_id != self.index.project_id or settings != expected
                or json.loads(job.input_snapshot_json)["inputs"]["section_tempo"] != expected
                or json.loads(job.request.effective_identity_json) != {"processor_version": TEMPO_PROCESSOR_VERSION}):
            raise ValueError("Derivative source or processor identity differs from enqueue.")
        if raw.speech_boundary_map is not None:
            raw.speech_boundary_map.validate_source(section.text, raw.checksum, raw.sample_rate, raw.frame_count)
        contained_path(self.index.repository.workspace, self.work_root.relative_to(self.index.repository.workspace).as_posix())
        self.work_root.mkdir(parents=True, exist_ok=True)
        result = process_pcm_wav_tempo(payload, settings["tempo"], process_runner=self.process_runner,
                                       ffmpeg_locator=self.ffmpeg_locator, work_root=self.work_root)
        measured, _ = inspect_pcm_wav(result.audio_bytes)
        if measured.frame_count <= 0 or measured != result.audio_parameters:
            raise ValueError("Processed audio must contain measured PCM frames.")
        audio = {"version": 1, "section_id": section.section_id, "revision_id": section.id,
                 "checksum": result.output_checksum, "audio_parameters": measured.to_payload(),
                 "duration_seconds": measured.duration_seconds, "request_fingerprint": job.request.fingerprint}
        if raw.speech_boundary_map is not None:
            audio["speech_boundary_map"] = raw.speech_boundary_map.retime(
                checksum=result.output_checksum, sample_rate=measured.sample_rate,
                frame_count=measured.frame_count).to_payload()
        derivative = {"version": 1, **settings, "raw_artifact_id": raw.artifact_id, **result.evidence()}
        with io.BytesIO(result.audio_bytes) as source:
            yield source, {"section_audio": audio, "audio_derivative": derivative}

    def selected(self, section, variant):
        if variant not in ("original", "processed"):
            raise ValueError("Audio variant must be original or processed.")
        if section.project_id != self.index.project_id or self.index.repository.active_script().section(section.section_id) != section:
            raise ValueError("Audio choice requires the expected current section revision.")
        heads = self.index.selected()
        raw_id = heads.get("section:" + section.section_id + ":audio:raw")
        if raw_id is None:
            return None
        _, raw, _ = self._read(raw_id)
        if raw.revision_id != section.id or raw.section_id != section.section_id:
            return None
        if raw.speech_boundary_map is not None:
            raw.speech_boundary_map.validate_source(section.text, raw.checksum, raw.sample_rate, raw.frame_count)
        if variant == "original":
            return raw
        processed_id = heads.get("section:" + section.section_id + ":audio:processed")
        if processed_id is None:
            return None
        manifest, processed, _ = self._read(processed_id)
        derivative = manifest.metadata["audio_derivative"]
        if (processed.revision_id != section.id or processed.section_id != section.section_id
                or derivative["raw_artifact_id"] != raw_id or derivative["raw_checksum"] != raw.checksum
                or derivative["processor_version"] != TEMPO_PROCESSOR_VERSION):
            return None  # Retained historical media is never advertised as current.
        if processed.speech_boundary_map is not None:
            processed.speech_boundary_map.validate_source(section.text, processed.checksum, processed.sample_rate, processed.frame_count)
            if processed.speech_boundary_map.source_audio_checksum != raw.checksum:
                raise ValueError("Processed speech map differs from the selected raw source.")
        return processed
