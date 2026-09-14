"""Provider-neutral worker operation and independently revalidated output evidence."""

from contextlib import contextmanager
from hashlib import sha256
import io
import json
import os
from pathlib import Path

from app.storage.paths import contained_path

from app.domain.dependencies import canonical_json
from app.domain.publication import PublicationSnapshot
from app.domain.speech_boundary import SpeechBoundaryMap, SpeechChunkBoundary
from app.tts.assembly import inspect_pcm_wav
from app.tts.chunk_synthesis import ResumableChunkSynthesizer
from app.tts.chunking import chunk_narration, sentence_chunks
from app.tts.manifest import SynthesisManifest, sanitize_synthesis_identity


def inputs(job):
    snapshot = PublicationSnapshot.from_job(job)
    if job.request.operation != "section_audio.synthesize" or job.request.algorithm_version not in ("1", "2") or len(snapshot.sections) != 1:
        raise ValueError("Expected a single-section audio job.")
    prepared = json.loads(job.input_snapshot_json)["inputs"]["section_audio"]
    if prepared != json.loads(job.request.settings_json) or prepared["effective_identity"] != json.loads(job.request.effective_identity_json):
        raise ValueError("Audio inputs differ from the frozen request.")
    return snapshot.sections[0], prepared


def _chunks(job, section, prepared):
    chunker = sentence_chunks if job.request.algorithm_version == "2" else chunk_narration
    return chunker(section.text, max_words=prepared["max_words"])


def workspace(root, job):
    # Opaque job IDs are hashed, never interpreted as user-controlled path parts.
    return contained_path(Path(root).absolute(), sha256(job.id.encode()).hexdigest())


def generate(job, provider, root, report=lambda *args: None, canceled=lambda: False):
    section, prepared = inputs(job)
    config = prepared["voice_config"]
    identity = provider.effective_synthesis_identity(config)
    if canonical_json(identity) != canonical_json(prepared["effective_identity"]["synthesis"]):
        raise ValueError("Effective synthesis identity changed after enqueue.")
    chunks = _chunks(job, section, prepared)
    if not chunks:
        raise ValueError("Section text has no synthesis chunks.")
    directory = workspace(root, job)
    directory.mkdir(parents=True, exist_ok=True)
    marker = contained_path(directory, "request.json")
    expected = canonical_json(job.to_payload())
    if marker.exists() and marker.read_text(encoding="utf-8") != expected:
        raise ValueError("Generation workspace belongs to another request.")
    if not marker.exists():
        pending = contained_path(directory, "request.pending")
        with pending.open("w", encoding="utf-8", newline="\n") as stream:
            stream.write(expected)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(pending, marker)
    result = ResumableChunkSynthesizer(provider, max_attempts=1).synthesize(
        chunks, runtime_dir=directory, voice_config=config, canceled=canceled,
        progress=lambda done, total: report("chunks", done, total))
    if not result.completed:
        raise ValueError("Section synthesis incomplete; valid chunks remain available for retry.")
    return []  # The coordinator resolves a fixed output in its own configured workspace.


@contextmanager
def validated_output(root, job):
    section, prepared = inputs(job)
    directory = workspace(root, job)
    if contained_path(directory, "request.json").read_text(encoding="utf-8") != canonical_json(job.to_payload()):
        raise ValueError("Output workspace request mismatch.")
    path = contained_path(directory, "voiceover.wav")
    payload = path.read_bytes()
    parameters, final_frames = inspect_pcm_wav(payload)
    checksum = sha256(payload).hexdigest()
    manifest = SynthesisManifest.from_payload(json.loads(contained_path(directory, "synthesis-manifest.json").read_text(encoding="utf-8")))
    expected_identity = prepared["effective_identity"]["synthesis"]
    expected_rate = expected_identity.get("voice", {}).get("catalog", {}).get("expected_sample_rate_hz")
    chunks = _chunks(job, section, prepared)
    if (parameters.frame_count <= 0 or parameters.channels != 1 or parameters.sample_width != 2
            or manifest.final_status != "completed" or manifest.final_artifact_ref != "voiceover.wav"
            or manifest.final_checksum != checksum or manifest.final_audio_parameters != parameters
            or manifest.final_duration_seconds != parameters.duration_seconds
            or manifest.effective_synthesis_identity != sanitize_synthesis_identity(expected_identity)
            or expected_rate is not None and parameters.sample_rate != expected_rate
            or set(manifest.chunks) != {c.id for c in chunks}
            or manifest.generated_chunk_count < 0 or manifest.reused_chunk_count < 0
            or manifest.generated_chunk_count + manifest.reused_chunk_count != len(chunks)
            or manifest.failed_chunk_count != 0):
        raise ValueError("WAV completion evidence does not match measured output or request identity.")
    # The provider name is already part of the frozen effective identity.
    from app.tts.manifest import stable_hash
    config_hash = stable_hash({"provider": expected_identity["provider"], "effective_synthesis_identity": expected_identity})
    if manifest.config_hash != config_hash or manifest.schema_version != 1 or manifest.failed_chunk_ids:
        raise ValueError("Synthesis manifest configuration or completion state is invalid.")
    frame_count = 0
    boundaries = []
    chunk_frames_hash = sha256()
    for chunk in chunks:
        record = manifest.chunks[chunk.id]
        chunk_path = contained_path(directory, record.artifact_ref or "")
        chunk_bytes = chunk_path.read_bytes()
        actual, frames = inspect_pcm_wav(chunk_bytes)
        if (record.status != "completed" or record.config_hash != config_hash
                or record.input_hash != sha256(chunk.text.encode()).hexdigest()
                or record.index != chunk.index or record.text_hash != chunk.text_hash
                or record.source_span != chunk.source_span
                or record.wav_checksum != sha256(chunk_bytes).hexdigest() or record.audio_parameters != actual
                or (actual.channels, actual.sample_rate, actual.sample_width) != (parameters.channels, parameters.sample_rate, parameters.sample_width)):
            raise ValueError("Chunk evidence is inconsistent with the enqueued section.")
        if chunk.source_span is not None:
            boundaries.append(SpeechChunkBoundary(chunk.id, chunk.source_span, frame_count, frame_count + actual.frame_count))
        frame_count += actual.frame_count
        chunk_frames_hash.update(frames)
    if frame_count != parameters.frame_count or chunk_frames_hash.digest() != sha256(final_frames).digest():
        raise ValueError("Final frame count does not match validated chunks.")
    boundary_metadata = {}
    if job.request.algorithm_version == "2":
        boundary_map = SpeechBoundaryMap(sha256(section.text.encode("utf-8")).hexdigest(), len(section.text),
                                         checksum, parameters.sample_rate, frame_count, tuple(boundaries))
        boundary_metadata["speech_boundary_map"] = boundary_map.to_payload()
    # The exact validated bytes are the publication source, avoiding a path reopen race.
    with io.BytesIO(payload) as source:
        yield source, {"version": 1, "section_id": section.section_id, "revision_id": section.id,
                       "checksum": checksum, "audio_parameters": parameters.to_payload(),
                       "duration_seconds": parameters.duration_seconds, "request_fingerprint": job.request.fingerprint,
                       "generated_chunks": manifest.generated_chunk_count, "reused_chunks": manifest.reused_chunk_count,
                       **boundary_metadata}
