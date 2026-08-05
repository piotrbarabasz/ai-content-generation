"""Optional, lazy XTTS-v2 evaluation-only TTS provider."""

from __future__ import annotations

import hashlib
import tempfile
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from app.domain.enums import ProviderType
from app.domain.types import JsonDict
from app.tts.assembly import inspect_pcm_wav

from .interfaces import TTSProvider, _coerce_json_dict
from .tts_capabilities import (
    TTSCapabilities,
    resolve_language_id,
    resolve_voice_mode,
)
from .tts_result import TTSSynthesisResult


_MODEL_NAME = "tts_models/multilingual/multi-dataset/xtts_v2"
_MODEL_VARIANTS = frozenset({"v3", "xtts_v2"})
_REFERENCE_AUDIO_KEYS = (
    "reference_audio_path",
    "referenceAudioPath",
    "audio_prompt_path",
    "audioPromptPath",
    "speaker_wav",
    "speakerWav",
)
_APPROVED_LABEL_KEYS = ("approved_label", "approvedLabel", "reference_label", "referenceLabel")


class XTTSError(RuntimeError):
    """Base error for actionable XTTS adapter failures."""


class XTTSDependencyError(XTTSError):
    """The optional XTTS runtime is not installed."""


class XTTSConfigurationError(XTTSError):
    """The configured XTTS reference audio or settings are invalid."""


class XTTSModelLoadError(XTTSError):
    """The optional XTTS model could not be initialized."""


class XTTSGenerationError(XTTSError):
    """The XTTS backend could not synthesize audio."""


class XTTSAudioValidationError(XTTSError):
    """The backend output is not a usable mono 16-bit PCM WAV payload."""


def _resolve_text_value(
    voice_config: Mapping[str, Any] | None,
    keys: tuple[str, ...],
) -> str | None:
    if voice_config is None:
        return None
    for key in keys:
        if key not in voice_config:
            continue
        value = voice_config[key]
        if not isinstance(value, str):
            continue
        normalized = value.strip()
        if normalized:
            return normalized
    return None


def _resolve_reference_audio_path(
    voice_config: Mapping[str, Any] | None,
    default_reference_audio_path: Path | None,
) -> Path:
    value = _resolve_text_value(voice_config, _REFERENCE_AUDIO_KEYS)
    candidate = Path(value) if value is not None else default_reference_audio_path
    if candidate is None:
        raise XTTSConfigurationError("XTTS requires an approved reference WAV.")
    if not candidate.is_file():
        raise XTTSConfigurationError(
            f"XTTS approved reference WAV '{candidate.name or 'reference.wav'}' does not exist or is not a file."
        )
    return candidate


def _resolve_approved_label(
    voice_config: Mapping[str, Any] | None,
    default_approved_label: str | None,
) -> str:
    value = _resolve_text_value(voice_config, _APPROVED_LABEL_KEYS)
    label = value if value is not None else default_approved_label
    if label is None:
        raise XTTSConfigurationError("XTTS approved_label is required.")
    normalized = label.strip()
    if not normalized:
        raise XTTSConfigurationError("XTTS approved_label is required.")
    return normalized


def _reference_identity(reference_audio_path: Path, approved_label: str) -> JsonDict:
    try:
        reference_bytes = reference_audio_path.read_bytes()
    except OSError as exc:
        raise XTTSConfigurationError("XTTS approved reference WAV could not be read.") from exc
    return {
        "approved_label": approved_label,
        "content_checksum": hashlib.sha256(reference_bytes).hexdigest(),
    }


def _coerce_wav_bytes(audio: Any, *, output_path: Path | None = None) -> bytes | None:
    if isinstance(audio, bytes):
        return audio
    if isinstance(audio, (bytearray, memoryview)):
        return bytes(audio)
    if isinstance(audio, str):
        candidate = Path(audio)
        if candidate.exists():
            return candidate.read_bytes()
        return None
    if isinstance(audio, Path):
        if audio.exists():
            return audio.read_bytes()
        return None
    if audio is None and output_path is not None and output_path.exists():
        return output_path.read_bytes()
    if hasattr(audio, "read"):
        return bytes(audio.read())
    return None


class _LoadedXTTSBackend:
    """Small runtime wrapper that converts XTTS output into WAV bytes."""

    def __init__(self, backend: Any) -> None:
        self._backend = backend

    def _call_backend(self, method_name: str, *args: Any, **kwargs: Any) -> bytes | None:
        method = getattr(self._backend, method_name, None)
        if not callable(method):
            return None
        try:
            result = method(*args, **kwargs)
        except TypeError:
            return None
        output_path = kwargs.get("file_path") or kwargs.get("output_path") or kwargs.get("wav_file")
        return _coerce_wav_bytes(result, output_path=Path(output_path) if output_path else None)

    def synthesize(
        self,
        text: str,
        *,
        language_id: str,
        reference_audio_path: Path,
    ) -> bytes:
        reference_path = str(reference_audio_path)
        common_kwargs = {
            "speaker_wav": reference_path,
            "language": language_id,
        }

        for method_name in ("synthesize", "tts"):
            for call_kwargs in (
                common_kwargs,
                {"speaker_wav": reference_path, "language_id": language_id},
            ):
                audio_bytes = self._call_backend(method_name, text, **call_kwargs)
                if audio_bytes is not None:
                    return audio_bytes

        tts_to_file = getattr(self._backend, "tts_to_file", None)
        if callable(tts_to_file):
            attempts = (
                {"speaker_wav": reference_path, "language": language_id, "file_path": None},
                {"speaker_wav": reference_path, "language_id": language_id, "file_path": None},
                {"speaker_wav": reference_path, "language": language_id, "output_path": None},
                {"speaker_wav": reference_path, "language_id": language_id, "output_path": None},
            )
            for attempt in attempts:
                with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as temporary:
                    temp_path = Path(temporary.name)
                try:
                    attempt = {key: value for key, value in attempt.items() if value is not None}
                    attempt["file_path"] = str(temp_path)
                    try:
                        result = tts_to_file(text=text, **attempt)
                    except TypeError:
                        attempt.pop("file_path", None)
                        attempt["output_path"] = str(temp_path)
                        try:
                            result = tts_to_file(text=text, **attempt)
                        except TypeError:
                            continue
                    audio_bytes = _coerce_wav_bytes(result, output_path=temp_path)
                    if audio_bytes is None and temp_path.exists():
                        audio_bytes = temp_path.read_bytes()
                    if audio_bytes is not None:
                        return audio_bytes
                finally:
                    temp_path.unlink(missing_ok=True)

        raise XTTSGenerationError("XTTS synthesis interface is incompatible.")


def _load_runtime_backend(model_reference: JsonDict) -> _LoadedXTTSBackend:
    """Import and initialize the heavy optional runtime only on first use."""

    try:
        from TTS.api import TTS as CoquiTTS
    except ImportError as exc:
        try:
            from TTS import TTS as CoquiTTS  # type: ignore[no-redef]
        except ImportError as inner_exc:
            raise XTTSDependencyError(
                "XTTS requires the optional 'TTS' package. Install it with: pip install '.[xtts]'"
            ) from inner_exc

    model_name = model_reference.get("model_name", _MODEL_NAME)
    device = str(model_reference.get("device", "cpu"))
    gpu_enabled = device.lower() != "cpu"
    try:
        try:
            backend = CoquiTTS(model_name=model_name, progress_bar=False, gpu=gpu_enabled)
        except TypeError:
            backend = CoquiTTS(model_name=model_name)
    except Exception as exc:
        raise XTTSModelLoadError("XTTS model loading failed.") from exc
    return _LoadedXTTSBackend(backend)


class XTTSV2EvalProvider(TTSProvider):
    """Provider-neutral XTTS-v2 evaluation adapter with lazy loading."""

    provider_type = ProviderType.TTS

    def __init__(
        self,
        provider_name: str = "xtts_v2_eval",
        *,
        device: str = "cpu",
        language_id: str | None = "pl",
        model_variant: str = "xtts_v2",
        reference_audio_path: str | Path | None = None,
        approved_label: str | None = None,
        model_loader: Callable[[JsonDict], Any] | None = None,
    ) -> None:
        if not isinstance(provider_name, str) or not provider_name.strip():
            raise XTTSConfigurationError("XTTS provider_name must be a non-empty string.")
        if not isinstance(device, str) or not device.strip():
            raise XTTSConfigurationError("XTTS device must be a non-empty string.")
        if language_id is not None and not isinstance(language_id, str):
            raise XTTSConfigurationError("XTTS language_id must be a string or null.")
        if not isinstance(model_variant, str):
            raise XTTSConfigurationError("XTTS model_variant must be a string.")
        normalized_variant = model_variant.strip().lower().replace("-", "_")
        if normalized_variant not in _MODEL_VARIANTS:
            raise XTTSConfigurationError("XTTS model_variant must be 'xtts_v2'.")
        if reference_audio_path is not None and not isinstance(reference_audio_path, (str, Path)):
            raise XTTSConfigurationError("XTTS reference_audio_path must be a path string or null.")
        if approved_label is not None and not isinstance(approved_label, str):
            raise XTTSConfigurationError("XTTS approved_label must be a string or null.")
        if approved_label is not None and not approved_label.strip():
            raise XTTSConfigurationError("XTTS approved_label must be a non-empty string.")

        self.provider_name = provider_name.strip()
        self.device = device.strip()
        self.language_id = language_id.strip() if isinstance(language_id, str) and language_id.strip() else "pl"
        self.model_variant = "xtts_v2"
        self.reference_audio_path = Path(reference_audio_path) if reference_audio_path is not None else None
        self.approved_label = approved_label.strip() if isinstance(approved_label, str) else None
        self._model_loader = model_loader or _load_runtime_backend
        self._backend: Any | None = None

    def capabilities(self) -> TTSCapabilities:
        return TTSCapabilities(
            provider_name=self.provider_name,
            supported_languages=("pl",),
            voice_modes=("reference",),
            reference_audio_required=True,
            speaking_rate_supported=False,
            usage_policy="evaluation_only",
        )

    def _effective_language_id(self, voice_config: JsonDict | None) -> str:
        return resolve_language_id(voice_config, default_language_id=self.language_id) or self.language_id

    def _effective_voice_mode(self, voice_config: JsonDict | None) -> str:
        return resolve_voice_mode(voice_config, default_voice_mode="reference")

    def _effective_reference_audio_path(self, voice_config: JsonDict | None) -> Path:
        return _resolve_reference_audio_path(voice_config, self.reference_audio_path)

    def _effective_approved_label(self, voice_config: JsonDict | None) -> str:
        return _resolve_approved_label(voice_config, self.approved_label)

    def _model_reference(
        self,
        *,
        language_id: str,
        reference_audio_path: Path,
        approved_label: str,
    ) -> JsonDict:
        return {
            "provider": self.provider_name,
            "model_name": _MODEL_NAME,
            "model_variant": self.model_variant,
            "device": self.device,
            "language_id": language_id,
            "reference_identity": _reference_identity(reference_audio_path, approved_label),
        }

    def _get_backend(self, model_reference: JsonDict) -> Any:
        if self._backend is None:
            try:
                backend = self._model_loader(model_reference)
                self._backend = backend if isinstance(backend, _LoadedXTTSBackend) else _LoadedXTTSBackend(backend)
            except XTTSError:
                raise
            except Exception as exc:
                raise XTTSModelLoadError("XTTS model loading failed.") from exc
        return self._backend

    def effective_synthesis_identity(
        self,
        voice_config: JsonDict | None = None,
    ) -> JsonDict:
        """Return the effective configuration without loading optional runtime code."""

        config = _coerce_json_dict(voice_config)
        language_id = self._effective_language_id(config)
        voice_mode = self._effective_voice_mode(config)
        reference_audio_path = self._effective_reference_audio_path(config)
        approved_label = self._effective_approved_label(config)
        self.capabilities().validate_request(
            language_id=language_id,
            voice_mode=voice_mode,
            reference_audio_present=True,
            usage_policy="evaluation_only",
        )
        reference_identity = _reference_identity(reference_audio_path, approved_label)
        return {
            "provider": self.provider_name,
            "model_variant": self.model_variant,
            "device": self.device,
            "language_id": language_id,
            "generation_settings": {},
            "voice": {
                "mode": voice_mode,
                **reference_identity,
            },
        }

    def synthesize(
        self,
        text: str,
        voice_config: JsonDict | None = None,
    ) -> TTSSynthesisResult:
        if not isinstance(text, str) or not text.strip():
            raise XTTSGenerationError("XTTS requires non-empty synthesis text.")

        config = _coerce_json_dict(voice_config)
        language_id = self._effective_language_id(config)
        voice_mode = self._effective_voice_mode(config)
        reference_audio_path = self._effective_reference_audio_path(config)
        approved_label = self._effective_approved_label(config)
        self.capabilities().validate_request(
            language_id=language_id,
            voice_mode=voice_mode,
            reference_audio_present=True,
            usage_policy="evaluation_only",
        )

        model_reference = self._model_reference(
            language_id=language_id,
            reference_audio_path=reference_audio_path,
            approved_label=approved_label,
        )
        runtime = self._get_backend(model_reference)
        try:
            audio_bytes = runtime.synthesize(
                text,
                language_id=language_id,
                reference_audio_path=reference_audio_path,
            )
        except TypeError:
            audio_bytes = runtime.synthesize(
                text,
                language=language_id,
                speaker_wav=str(reference_audio_path),
            )
        except XTTSError:
            raise
        except Exception as exc:
            raise XTTSGenerationError("XTTS synthesis failed.") from exc

        if not isinstance(audio_bytes, (bytes, bytearray, memoryview)):
            raise XTTSAudioValidationError("XTTS returned invalid WAV output.")
        audio_payload = bytes(audio_bytes)
        try:
            parameters, _frames = inspect_pcm_wav(audio_payload)
        except ValueError as exc:
            raise XTTSAudioValidationError(str(exc)) from exc
        if parameters.channels != 1:
            raise XTTSAudioValidationError("XTTS WAV output must be mono.")
        if parameters.sample_width != 2:
            raise XTTSAudioValidationError(
                "XTTS WAV output must use signed 16-bit PCM samples."
            )

        reference_identity = _reference_identity(reference_audio_path, approved_label)
        return TTSSynthesisResult(
            audio_bytes=audio_payload,
            sample_rate=parameters.sample_rate,
            duration_seconds=parameters.duration_seconds,
            audio_format="wav",
            provider_name=self.provider_name,
            metadata={
                "device": self.device,
                "language_id": language_id,
                "model_variant": self.model_variant,
                "voice": {
                    "mode": voice_mode,
                    **reference_identity,
                },
            },
        )
