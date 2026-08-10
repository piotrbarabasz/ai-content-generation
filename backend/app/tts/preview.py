"""Provider-neutral synthesis and caching for short TTS previews."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import tempfile
from threading import Lock
from typing import Any

from app.domain.enums import ProviderType
from app.domain.provider_config import ProviderConfig
from app.domain.types import JsonDict
from app.providers.interfaces import TTSProvider
from app.providers.tts_factory import build_tts_provider
from app.providers.tts_settings import TTSSettings, TTSSettingsError

from .assembly import WavAssemblyError, inspect_pcm_wav, persist_pcm_wav_atomically
from .catalog import TTSCatalog, TTSCatalogError
from .manifest import AudioParameters, sanitize_synthesis_identity, stable_hash
from .post_processing import (
    AudioPostProcessingResult,
    process_pcm_wav_tempo,
    validate_tempo,
)


PREVIEW_TEXT_LIMIT = 400
PREVIEW_MANIFEST_VERSION = 1
PREVIEW_POST_PROCESSING_VERSION = "pcm-tempo-v1"

_OPAQUE_REFERENCE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_PREVIEW_ID_RE = re.compile(r"^tts_preview_[0-9a-f]{64}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_PRIVATE_KEY_RE = re.compile(
    r"(?:api[_-]?key|auth|credential|password|passwd|private|secret|token)",
    re.IGNORECASE,
)
_PATH_KEY_RE = re.compile(r"(?:^|_)(?:path|file|dir|directory|location|uri|url)$", re.IGNORECASE)

_MODEL_SETTING_KEYS = {
    "chatterbox_v3": "model_variant",
    "piper": "model_key",
    "xtts_v2_eval": "model_variant",
}
_REFERENCE_SETTING_KEYS = {
    "chatterbox_v3": "audio_prompt_path",
    "mock": "audio_prompt_path",
    "xtts_v2_eval": "reference_audio_path",
}
_RESERVED_SETTINGS = frozenset(
    {
        "provider",
        "usage_policy",
        "language_id",
        "model_variant",
        "model_key",
        "model_path",
        "audio_prompt_path",
        "reference_audio_path",
        "approved_label",
    }
)


class TTSPreviewError(ValueError):
    """Raised when a preview request cannot be safely completed."""


class TTSPreviewNotFoundError(TTSPreviewError):
    """Raised when an opaque preview id has no valid cached audio."""


@dataclass(frozen=True, slots=True)
class ApprovedReferenceAudio:
    """Approved runtime input returned for an opaque artifact identifier."""

    runtime_path: Path
    checksum: str
    approval_label: str = "approved"
    approved: bool = True


@dataclass(frozen=True, slots=True)
class TTSPreviewResult:
    """Path-free metadata for one validated preview WAV."""

    preview_id: str
    duration_seconds: float
    checksum: str
    provider: str
    model: str
    voice: str
    language: str
    tempo: float
    cached: bool

    @property
    def selection(self) -> JsonDict:
        return {
            "provider": self.provider,
            "model": self.model,
            "voice": self.voice,
            "language": self.language,
        }

    def to_payload(self) -> JsonDict:
        return {
            "preview_id": self.preview_id,
            "duration_seconds": self.duration_seconds,
            "checksum": self.checksum,
            **self.selection,
            "tempo": self.tempo,
            "cached": self.cached,
        }


ReferenceArtifactResolver = Callable[[str], ApprovedReferenceAudio | None]
ProviderBuilder = Callable[[ProviderConfig], TTSProvider]
TempoProcessor = Callable[[bytes, object], AudioPostProcessingResult]


@dataclass(frozen=True, slots=True)
class _PreparedRequest:
    text: str
    selection: JsonDict
    tempo: float
    provider_settings: JsonDict
    voice_config: JsonDict
    reference_checksum: str | None


@dataclass(frozen=True, slots=True)
class _PreviewIdentity:
    native_id: str
    preview_id: str
    native_identity: JsonDict


@dataclass(frozen=True, slots=True)
class _CachedPreview:
    result: TTSPreviewResult
    audio_bytes: bytes


class TTSPreviewService:
    """Synthesize and single-flight cache validated preview WAV files."""

    def __init__(
        self,
        *,
        catalog: TTSCatalog,
        preview_root: Path,
        provider_builder: ProviderBuilder = build_tts_provider,
        reference_artifact_resolver: ReferenceArtifactResolver | None = None,
        tempo_processor: TempoProcessor = process_pcm_wav_tempo,
        post_processing_version: str = PREVIEW_POST_PROCESSING_VERSION,
    ) -> None:
        if not isinstance(catalog, TTSCatalog):
            raise TypeError("TTS preview catalog must be a TTSCatalog instance.")
        if not callable(provider_builder):
            raise TypeError("TTS preview provider_builder must be callable.")
        if not callable(tempo_processor):
            raise TypeError("TTS preview tempo_processor must be callable.")
        if reference_artifact_resolver is not None and not callable(reference_artifact_resolver):
            raise TypeError("TTS preview reference_artifact_resolver must be callable.")
        if not isinstance(post_processing_version, str) or not post_processing_version.strip():
            raise ValueError("TTS preview post-processing version is required.")

        self._catalog = catalog
        self._root = Path(preview_root)
        self._audio_root = self._root / "preview-audio"
        self._manifest_root = self._root / "preview-manifests"
        self._provider_builder = provider_builder
        self._reference_resolver = reference_artifact_resolver
        self._tempo_processor = tempo_processor
        self._post_processing_version = post_processing_version.strip()
        self._locks_guard = Lock()
        self._identity_locks: dict[str, Lock] = {}

    def synthesize_preview(
        self,
        *,
        provider: str,
        model: str,
        voice: str,
        language: str,
        tempo: object,
        text: str,
        reference_audio_artifact_id: str | None = None,
        synthesis_settings: Mapping[str, Any] | None = None,
    ) -> TTSPreviewResult:
        """Validate, synthesize and cache one short preview request."""

        prepared = self._prepare_request(
            provider=provider,
            model=model,
            voice=voice,
            language=language,
            tempo=tempo,
            text=text,
            reference_audio_artifact_id=reference_audio_artifact_id,
            synthesis_settings=synthesis_settings,
        )

        provider_config = ProviderConfig.create(
            workflow_config_id="tts-preview",
            provider_type=ProviderType.TTS,
            provider_name=prepared.selection["provider"],
            settings=prepared.provider_settings,
        )
        try:
            composed = self._provider_builder(provider_config)
            effective_identity = sanitize_synthesis_identity(
                composed.effective_synthesis_identity(prepared.voice_config)
            )
        except Exception as exc:
            raise TTSPreviewError("TTS preview provider composition failed.") from exc

        native_identity = _redact_identity(
            {
                "schema_version": PREVIEW_MANIFEST_VERSION,
                "selection": prepared.selection,
                "text_checksum": sha256(prepared.text.encode("utf-8")).hexdigest(),
                "effective_synthesis_identity": effective_identity,
                "reference_checksum": prepared.reference_checksum,
            }
        )
        identity = self._identity(native_identity, prepared.tempo)

        cached = self._load_cached(identity.preview_id, expected_identity=identity)
        if cached is not None:
            return _with_cached(cached.result, True)

        identity_lock = self._lock_for(identity.preview_id)
        with identity_lock:
            cached = self._load_cached(identity.preview_id, expected_identity=identity)
            if cached is not None:
                return _with_cached(cached.result, True)
            return self._generate(composed, prepared, identity)

    def read_audio(self, preview_id: str) -> bytes:
        """Return validated WAV bytes for one known opaque preview id."""

        normalized = _normalize_preview_id(preview_id)
        cached = self._load_cached(normalized)
        if cached is None:
            raise TTSPreviewNotFoundError("TTS preview was not found.")
        return cached.audio_bytes

    def _prepare_request(
        self,
        *,
        provider: str,
        model: str,
        voice: str,
        language: str,
        tempo: object,
        text: str,
        reference_audio_artifact_id: str | None,
        synthesis_settings: Mapping[str, Any] | None,
    ) -> _PreparedRequest:
        normalized_text = _normalize_preview_text(text)
        normalized_tempo = validate_tempo(tempo)
        settings = _normalize_synthesis_settings(synthesis_settings)

        try:
            provider_descriptor = self._catalog.get_provider(provider)
            model_descriptor = provider_descriptor.get_model(model)
            voice_descriptor = provider_descriptor.get_voice(model_descriptor.id, voice)
        except TTSCatalogError as exc:
            raise TTSPreviewError(str(exc)) from exc

        normalized_language = _normalize_language(language)
        if not provider_descriptor.supports_language(normalized_language):
            raise TTSPreviewError("The selected TTS provider does not support the requested language.")
        if not model_descriptor.supports_language(normalized_language):
            raise TTSPreviewError("The selected TTS model does not support the requested language.")
        if not voice_descriptor.supports_language(normalized_language):
            raise TTSPreviewError("The selected TTS voice does not support the requested language.")
        if not voice_descriptor.preview_supported:
            raise TTSPreviewError("The selected TTS voice does not support previews.")

        reference = self._resolve_reference(
            reference_audio_artifact_id,
            required=voice_descriptor.reference_audio_required,
            accepted=voice_descriptor.voice_mode == "reference",
        )
        try:
            provider_descriptor.capabilities.validate_request(
                language_id=normalized_language,
                voice_mode=voice_descriptor.voice_mode,
                reference_audio_present=reference is not None,
                usage_policy=provider_descriptor.usage_policy,
            )
        except ValueError as exc:
            raise TTSPreviewError(str(exc)) from exc

        provider_settings: JsonDict = dict(settings)
        provider_settings.update(
            {
                "provider": provider_descriptor.id,
                "usage_policy": provider_descriptor.usage_policy,
                "language_id": normalized_language,
            }
        )
        model_setting = _MODEL_SETTING_KEYS.get(provider_descriptor.id)
        if model_setting is not None:
            provider_settings[model_setting] = model_descriptor.id
        if reference is not None:
            reference_setting = _REFERENCE_SETTING_KEYS.get(provider_descriptor.id)
            if reference_setting is None:
                raise TTSPreviewError("The selected TTS provider cannot accept reference audio.")
            provider_settings[reference_setting] = reference.runtime_path
            if provider_descriptor.id == "xtts_v2_eval":
                provider_settings["approved_label"] = reference.approval_label

        try:
            TTSSettings.from_mapping(provider_settings, provider=provider_descriptor.id)
        except TTSSettingsError as exc:
            raise TTSPreviewError(str(exc)) from exc

        voice_config: JsonDict = {
            "language_id": normalized_language,
            "voice_mode": voice_descriptor.voice_mode,
            **dict(settings),
        }
        if reference is not None:
            reference_setting = _REFERENCE_SETTING_KEYS[provider_descriptor.id]
            voice_config[reference_setting] = reference.runtime_path
            if provider_descriptor.id == "xtts_v2_eval":
                voice_config["approved_label"] = reference.approval_label

        return _PreparedRequest(
            text=normalized_text,
            selection={
                "provider": provider_descriptor.id,
                "model": model_descriptor.id,
                "voice": voice_descriptor.id,
                "language": normalized_language,
            },
            tempo=normalized_tempo,
            provider_settings=provider_settings,
            voice_config=voice_config,
            reference_checksum=reference.checksum if reference is not None else None,
        )

    def _resolve_reference(
        self,
        artifact_id: str | None,
        *,
        required: bool,
        accepted: bool,
    ) -> ApprovedReferenceAudio | None:
        if artifact_id is None:
            if required:
                raise TTSPreviewError("The selected TTS voice requires approved reference audio.")
            return None
        if not isinstance(artifact_id, str) or _OPAQUE_REFERENCE_RE.fullmatch(artifact_id.strip()) is None:
            raise TTSPreviewError("TTS referenceAudioArtifactId must be an opaque identifier.")
        if not accepted:
            raise TTSPreviewError("The selected TTS voice does not accept reference audio.")
        if self._reference_resolver is None:
            raise TTSPreviewError("Approved TTS reference audio is unavailable.")
        try:
            resolved = self._reference_resolver(artifact_id.strip())
        except Exception as exc:
            raise TTSPreviewError("Approved TTS reference audio could not be resolved.") from exc
        if not isinstance(resolved, ApprovedReferenceAudio) or not resolved.approved:
            raise TTSPreviewError("TTS reference audio is missing or not approved.")
        checksum = str(resolved.checksum).strip().lower()
        label = str(resolved.approval_label).strip()
        path = Path(resolved.runtime_path)
        if _SHA256_RE.fullmatch(checksum) is None or not label:
            raise TTSPreviewError("TTS reference audio is missing approval metadata or checksum.")
        try:
            content = path.read_bytes()
        except OSError as exc:
            raise TTSPreviewError("Approved TTS reference audio is unavailable.") from exc
        if sha256(content).hexdigest() != checksum:
            raise TTSPreviewError("Approved TTS reference audio checksum does not match.")
        return ApprovedReferenceAudio(path, checksum, label, True)

    def _identity(self, native_identity: JsonDict, tempo: float) -> _PreviewIdentity:
        native_id = f"tts_native_{stable_hash(native_identity)}"
        preview_id = "tts_preview_" + stable_hash(
            {
                "native_id": native_id,
                "tempo": tempo,
                "post_processing_version": self._post_processing_version,
            }
        )
        return _PreviewIdentity(native_id, preview_id, native_identity)

    def _lock_for(self, preview_id: str) -> Lock:
        with self._locks_guard:
            return self._identity_locks.setdefault(preview_id, Lock())

    def _generate(
        self,
        provider: TTSProvider,
        request: _PreparedRequest,
        identity: _PreviewIdentity,
    ) -> TTSPreviewResult:
        try:
            synthesis = provider.synthesize(request.text, request.voice_config)
            if synthesis.audio_format != "wav":
                raise TTSPreviewError("TTS preview provider must return WAV audio.")
            native_parameters, _ = inspect_pcm_wav(synthesis.audio_bytes)
            _validate_preview_parameters(native_parameters)
            if native_parameters.sample_rate != synthesis.sample_rate:
                raise TTSPreviewError("TTS preview WAV sample rate does not match synthesis metadata.")
            processed = self._tempo_processor(synthesis.audio_bytes, request.tempo)
            audio_bytes = bytes(processed.audio_bytes)
            final_parameters, _ = inspect_pcm_wav(audio_bytes)
            _validate_preview_parameters(final_parameters)
        except TTSPreviewError:
            raise
        except Exception as exc:
            raise TTSPreviewError("TTS preview synthesis failed.") from exc

        audio_path = self._audio_path(identity.preview_id)
        try:
            persisted = persist_pcm_wav_atomically(audio_bytes, audio_path)
            manifest = {
                "schema_version": PREVIEW_MANIFEST_VERSION,
                "post_processing_version": self._post_processing_version,
                "preview_id": identity.preview_id,
                "native_id": identity.native_id,
                "native_identity": identity.native_identity,
                "selection": request.selection,
                "tempo": request.tempo,
                "audio_ref": f"preview-audio/{identity.preview_id}.wav",
                "checksum": persisted.checksum,
                "duration_seconds": persisted.duration_seconds,
                "audio_parameters": persisted.audio_parameters.to_payload(),
            }
            self._write_manifest(identity.preview_id, manifest)
        except (OSError, ValueError, WavAssemblyError) as exc:
            raise TTSPreviewError("TTS preview could not be persisted.") from exc

        return TTSPreviewResult(
            preview_id=identity.preview_id,
            duration_seconds=persisted.duration_seconds,
            checksum=persisted.checksum,
            provider=request.selection["provider"],
            model=request.selection["model"],
            voice=request.selection["voice"],
            language=request.selection["language"],
            tempo=request.tempo,
            cached=False,
        )

    def _load_cached(
        self,
        preview_id: str,
        *,
        expected_identity: _PreviewIdentity | None = None,
    ) -> _CachedPreview | None:
        manifest_path = self._manifest_path(preview_id)
        audio_path = self._audio_path(preview_id)
        try:
            payload = json.loads(manifest_path.read_text(encoding="utf-8"))
            if not isinstance(payload, Mapping):
                return None
            if payload.get("schema_version") != PREVIEW_MANIFEST_VERSION:
                return None
            if payload.get("post_processing_version") != self._post_processing_version:
                return None
            if payload.get("preview_id") != preview_id:
                return None
            native_identity = payload.get("native_identity")
            if not isinstance(native_identity, Mapping):
                return None
            native_id = f"tts_native_{stable_hash(native_identity)}"
            if payload.get("native_id") != native_id:
                return None
            tempo = validate_tempo(payload.get("tempo"))
            calculated = self._identity(dict(native_identity), tempo)
            if calculated.preview_id != preview_id:
                return None
            if expected_identity is not None and calculated != expected_identity:
                return None
            if payload.get("audio_ref") != f"preview-audio/{preview_id}.wav":
                return None

            selection = payload.get("selection")
            if not isinstance(selection, Mapping) or set(selection) != {
                "provider", "model", "voice", "language"
            }:
                return None
            audio_bytes = audio_path.read_bytes()
            parameters, _ = inspect_pcm_wav(audio_bytes)
            _validate_preview_parameters(parameters)
            checksum = sha256(audio_bytes).hexdigest()
            if payload.get("checksum") != checksum:
                return None
            if AudioParameters.from_payload(payload["audio_parameters"]) != parameters:
                return None
            duration = parameters.duration_seconds
            if abs(float(payload.get("duration_seconds")) - duration) > 1e-9:
                return None
            result = TTSPreviewResult(
                preview_id=preview_id,
                duration_seconds=duration,
                checksum=checksum,
                provider=str(selection["provider"]),
                model=str(selection["model"]),
                voice=str(selection["voice"]),
                language=str(selection["language"]),
                tempo=tempo,
                cached=True,
            )
            return _CachedPreview(result, audio_bytes)
        except (KeyError, OSError, TypeError, ValueError, json.JSONDecodeError, WavAssemblyError):
            return None

    def _audio_path(self, preview_id: str) -> Path:
        normalized = _normalize_preview_id(preview_id)
        return self._audio_root / f"{normalized}.wav"

    def _manifest_path(self, preview_id: str) -> Path:
        normalized = _normalize_preview_id(preview_id)
        return self._manifest_root / f"{normalized}.json"

    def _write_manifest(self, preview_id: str, payload: Mapping[str, Any]) -> None:
        path = self._manifest_path(preview_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
        json.loads(encoded)
        temporary_name: str | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=path.parent,
                prefix=f".{path.name}.",
                suffix=".tmp",
                delete=False,
            ) as temporary:
                temporary_name = temporary.name
                temporary.write(encoded)
                temporary.flush()
                os.fsync(temporary.fileno())
            Path(temporary_name).replace(path)
        finally:
            if temporary_name is not None:
                try:
                    Path(temporary_name).unlink(missing_ok=True)
                except OSError:
                    pass


def _normalize_preview_text(value: object) -> str:
    if not isinstance(value, str):
        raise TTSPreviewError("TTS preview text must be a string.")
    normalized = " ".join(value.split())
    if not normalized:
        raise TTSPreviewError("TTS preview text cannot be empty.")
    if len(normalized) > PREVIEW_TEXT_LIMIT:
        raise TTSPreviewError(
            f"TTS preview text cannot exceed {PREVIEW_TEXT_LIMIT} characters."
        )
    return normalized


def _normalize_language(value: object) -> str:
    if not isinstance(value, str):
        raise TTSPreviewError("TTS preview language must be a string.")
    normalized = value.strip().replace("_", "-").lower()
    if not normalized:
        raise TTSPreviewError("TTS preview language is required.")
    return normalized


def _normalize_synthesis_settings(values: Mapping[str, Any] | None) -> JsonDict:
    if values is None:
        return {}
    if not isinstance(values, Mapping):
        raise TTSPreviewError("TTS synthesis settings must be an object.")
    unknown_reserved = sorted(str(key) for key in values if str(key) in _RESERVED_SETTINGS)
    if unknown_reserved:
        raise TTSPreviewError(
            "TTS synthesis settings cannot override selection or runtime fields: "
            + ", ".join(unknown_reserved)
            + "."
        )
    for key, value in values.items():
        if not isinstance(key, str) or not key.strip():
            raise TTSPreviewError("TTS synthesis setting names must be non-empty strings.")
        if _PRIVATE_KEY_RE.search(key) or _PATH_KEY_RE.search(key):
            raise TTSPreviewError("TTS synthesis settings cannot contain private or path fields.")
        if isinstance(value, (str, Path)) and _looks_like_path(str(value)):
            raise TTSPreviewError("TTS synthesis settings cannot contain filesystem paths.")
    try:
        normalized = json.loads(json.dumps(dict(values), allow_nan=False))
    except (TypeError, ValueError) as exc:
        raise TTSPreviewError("TTS synthesis settings must be finite JSON values.") from exc
    if not isinstance(normalized, dict):
        raise TTSPreviewError("TTS synthesis settings must be an object.")
    return normalized


def _looks_like_path(value: str) -> bool:
    normalized = value.strip()
    return bool(
        normalized.startswith(("/", "\\", "~/"))
        or re.fullmatch(r"[A-Za-z]:[\\/].*", normalized)
        or "/" in normalized
        or "\\" in normalized
    )


def _redact_identity(identity: Mapping[str, Any]) -> JsonDict:
    hidden = object()

    def clean(value: Any, key: str = "") -> Any:
        if _PRIVATE_KEY_RE.search(key) or _PATH_KEY_RE.search(key):
            return hidden
        if isinstance(value, Mapping):
            return {
                str(item_key): item
                for item_key, item_value in value.items()
                if (item := clean(item_value, str(item_key))) is not hidden
            }
        if isinstance(value, (list, tuple)):
            return [item for item_value in value if (item := clean(item_value)) is not hidden]
        if isinstance(value, Path):
            return hidden
        if isinstance(value, str) and _looks_like_path(value):
            return hidden
        if value is None or isinstance(value, (str, bool, int, float)):
            return value
        return str(value)

    redacted = clean(identity)
    if not isinstance(redacted, dict):
        raise TTSPreviewError("TTS preview identity must be an object.")
    json.dumps(redacted, allow_nan=False, sort_keys=True, separators=(",", ":"))
    return redacted


def _validate_preview_parameters(parameters: AudioParameters) -> None:
    if parameters.channels != 1:
        raise TTSPreviewError("TTS preview WAV audio must be mono.")
    if parameters.sample_width != 2:
        raise TTSPreviewError("TTS preview WAV audio must use 16-bit PCM samples.")


def _normalize_preview_id(value: object) -> str:
    if not isinstance(value, str) or _PREVIEW_ID_RE.fullmatch(value.strip()) is None:
        raise TTSPreviewNotFoundError("TTS preview was not found.")
    return value.strip()


def _with_cached(result: TTSPreviewResult, cached: bool) -> TTSPreviewResult:
    return TTSPreviewResult(
        preview_id=result.preview_id,
        duration_seconds=result.duration_seconds,
        checksum=result.checksum,
        provider=result.provider,
        model=result.model,
        voice=result.voice,
        language=result.language,
        tempo=result.tempo,
        cached=cached,
    )


__all__ = [
    "ApprovedReferenceAudio",
    "PREVIEW_MANIFEST_VERSION",
    "PREVIEW_POST_PROCESSING_VERSION",
    "PREVIEW_TEXT_LIMIT",
    "TTSPreviewError",
    "TTSPreviewNotFoundError",
    "TTSPreviewResult",
    "TTSPreviewService",
]
