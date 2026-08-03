"""Independent, resumable technical narration chunk synthesis."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Any

from app.providers.interfaces import TTSProvider
from app.providers.tts_result import TTSSynthesisResult

from .assembly import WavAssemblyError, WavAssemblyResult, assemble_pcm_wav, inspect_pcm_wav
from .chunking import NarrationChunk
from .manifest import AudioParameters, ChunkManifest, SynthesisManifest, relative_reference, stable_hash


@dataclass(frozen=True, slots=True)
class ChunkSynthesisResult:
    manifest: SynthesisManifest
    completed: bool
    final_wav: WavAssemblyResult | None


class ResumableChunkSynthesizer:
    """Persist/reuse validated chunk WAVs while keeping the provider abstract."""

    def __init__(self, provider: TTSProvider, *, max_attempts: int = 2) -> None:
        if not isinstance(max_attempts, int) or isinstance(max_attempts, bool) or max_attempts < 1:
            raise ValueError("max_attempts must be a positive integer.")
        self._provider = provider
        self._max_attempts = max_attempts

    def synthesize(
        self,
        chunks: Sequence[NarrationChunk],
        *,
        runtime_dir: Path,
        voice_config: Mapping[str, Any] | None = None,
        manifest_name: str = "synthesis-manifest.json",
        final_name: str = "voiceover.wav",
    ) -> ChunkSynthesisResult:
        """Synthesize all chunks, then assemble only a fully valid chunk set."""
        root = Path(runtime_dir)
        config = dict(voice_config or {})
        root.mkdir(parents=True, exist_ok=True)
        config_hash = self._config_hash(config)
        manifest_path = root / manifest_name
        manifest = SynthesisManifest.load(manifest_path, config_hash=config_hash)
        ordered_chunks = sorted(chunks, key=lambda item: item.index)
        self._prune_stale_chunks(manifest, ordered_chunks, root)
        # Persist the pruned record set before any synthesis so an interrupted
        # rerun cannot keep stale narration chunks in its manifest.
        manifest.save(manifest_path)
        failed: list[str] = []
        outputs: list[bytes] = []
        baseline: AudioParameters | None = None
        for chunk in ordered_chunks:
            record = self._record_for(chunk, config_hash, manifest)
            payload = self._reuse_if_valid(record, root)
            if payload is None:
                payload = self._synthesize_chunk(chunk, config, record, root)
            if payload is None:
                failed.append(chunk.id)
                manifest.chunks[chunk.id] = record
                manifest.save(manifest_path)
                continue
            try:
                parameters, _ = inspect_pcm_wav(payload)
                if baseline is not None and self._format_key(parameters) != self._format_key(baseline):
                    raise WavAssemblyError("Chunk WAV parameters are incompatible with this narration run.")
            except WavAssemblyError as exc:
                record.status = "failed"
                record.error = str(exc)
                failed.append(chunk.id)
                manifest.chunks[chunk.id] = record
                manifest.save(manifest_path)
                continue
            baseline = baseline or parameters
            outputs.append(payload)
            manifest.chunks[chunk.id] = record
            manifest.save(manifest_path)
        manifest.failed_chunk_ids = failed
        if failed or len(outputs) != len(chunks):
            manifest.final_status = "failed"
            manifest.final_artifact_ref = None
            manifest.final_checksum = None
            manifest.final_duration_seconds = None
            manifest.final_audio_parameters = None
            manifest.save(manifest_path)
            return ChunkSynthesisResult(manifest, False, None)
        try:
            final_path = root / final_name
            final = assemble_pcm_wav(outputs, final_path)
            manifest.final_status = "completed"
            manifest.final_artifact_ref = relative_reference(final_path, root)
            manifest.final_checksum = final.checksum
            manifest.final_duration_seconds = final.duration_seconds
            manifest.final_audio_parameters = final.audio_parameters
        except (OSError, WavAssemblyError) as exc:
            manifest.final_status = "failed"
            manifest.final_artifact_ref = None
            manifest.final_checksum = None
            manifest.final_duration_seconds = None
            manifest.final_audio_parameters = None
            manifest.save(manifest_path)
            return ChunkSynthesisResult(manifest, False, None)
        manifest.save(manifest_path)
        return ChunkSynthesisResult(manifest, True, final)

    def _config_hash(self, config: dict[str, Any]) -> str:
        """Hash only provider-neutral, effective inputs without persisting them."""
        identity = self._provider.effective_synthesis_identity(config)
        if not isinstance(identity, Mapping):
            raise ValueError("TTS provider effective synthesis identity must be a mapping.")
        return stable_hash(
            {
                "provider": self._provider.provider_name,
                "effective_synthesis_identity": dict(identity),
            }
        )

    @staticmethod
    def _prune_stale_chunks(
        manifest: SynthesisManifest,
        chunks: Sequence[NarrationChunk],
        root: Path,
    ) -> None:
        """Keep only current records and controlled orphan WAVs under ``root``."""
        current_ids = {chunk.id for chunk in chunks}
        manifest.chunks = {
            chunk_id: record
            for chunk_id, record in manifest.chunks.items()
            if chunk_id in current_ids
        }

        chunk_directory = root / "chunks"
        try:
            resolved_root = root.resolve()
            resolved_chunk_directory = chunk_directory.resolve()
            resolved_chunk_directory.relative_to(resolved_root)
        except (OSError, ValueError):
            return
        if not chunk_directory.is_dir():
            return

        active_names = {f"{chunk_id}.wav" for chunk_id in current_ids}
        try:
            candidates = tuple(chunk_directory.iterdir())
        except OSError:
            return
        for candidate in candidates:
            if candidate.name in active_names or candidate.suffix != ".wav":
                continue
            try:
                # Refuse a symlinked directory or file that resolves beyond
                # the runtime chunk directory.  Only direct WAV children may
                # be unlinked as stale runtime artifacts.
                if candidate.resolve().parent != resolved_chunk_directory:
                    continue
                candidate.unlink()
            except OSError:
                continue

    def _record_for(self, chunk: NarrationChunk, config_hash: str, manifest: SynthesisManifest) -> ChunkManifest:
        input_hash = sha256(chunk.text.encode("utf-8")).hexdigest()
        existing = manifest.chunks.get(chunk.id)
        if existing and (existing.input_hash, existing.config_hash, existing.text_hash, existing.index) == (input_hash, config_hash, chunk.text_hash, chunk.index):
            return existing
        return ChunkManifest(chunk.id, chunk.index, "pending", input_hash, config_hash, chunk.text_hash)

    def _reuse_if_valid(self, record: ChunkManifest, root: Path) -> bytes | None:
        if record.status != "completed" or not record.artifact_ref or not record.wav_checksum or not record.audio_parameters:
            return None
        path = root / record.artifact_ref
        try:
            if relative_reference(path, root) != record.artifact_ref or not path.is_file():
                return None
            payload = path.read_bytes()
            parameters, _ = inspect_pcm_wav(payload)
            if sha256(payload).hexdigest() != record.wav_checksum or parameters != record.audio_parameters:
                return None
        except (OSError, ValueError, WavAssemblyError):
            return None
        return payload

    def _synthesize_chunk(self, chunk: NarrationChunk, config: dict[str, Any], record: ChunkManifest, root: Path) -> bytes | None:
        record.status = "running"
        record.error = None
        for _ in range(self._max_attempts):
            record.attempts += 1
            try:
                synthesis = self._provider.synthesize(chunk.text, config)
                if not isinstance(synthesis, TTSSynthesisResult) or synthesis.audio_format != "wav":
                    raise ValueError("TTS provider must return a WAV TTSSynthesisResult.")
                parameters, _ = inspect_pcm_wav(synthesis.audio_bytes)
                if parameters.sample_rate != synthesis.sample_rate:
                    raise WavAssemblyError("Chunk WAV sample rate differs from its synthesis result.")
                path = root / "chunks" / f"{chunk.id}.wav"
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(synthesis.audio_bytes)
                record.status = "completed"
                record.wav_checksum = sha256(synthesis.audio_bytes).hexdigest()
                record.duration_seconds = parameters.duration_seconds
                record.audio_parameters = parameters
                record.artifact_ref = relative_reference(path, root)
                record.error = None
                return synthesis.audio_bytes
            # Provider exceptions are intentionally scoped to this chunk so a
            # transient failure can be retried without restarting completed work.
            except Exception as exc:  # noqa: BLE001 - provider boundary
                record.status = "failed"
                record.error = str(exc)
        return None

    @staticmethod
    def _format_key(parameters: AudioParameters) -> tuple[int, int, int, str]:
        return (parameters.channels, parameters.sample_width, parameters.sample_rate, parameters.compression_type)


def synthesize_chunks(*args: Any, **kwargs: Any) -> ChunkSynthesisResult:
    """Convenience wrapper for callers that do not need a service instance."""
    provider = kwargs.pop("provider")
    max_attempts = kwargs.pop("max_attempts", 2)
    return ResumableChunkSynthesizer(provider, max_attempts=max_attempts).synthesize(*args, **kwargs)
