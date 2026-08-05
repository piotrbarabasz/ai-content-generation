"""Optional, lazy Piper TTS provider."""

from __future__ import annotations

import hashlib
import io
from pathlib import Path
from typing import Any, Callable

from app.domain.enums import ProviderType
from app.domain.types import JsonDict
from app.tts.assembly import inspect_pcm_wav

from .piper_catalog import PiperCatalogError, PiperVoiceCatalogEntry, get_piper_voice_catalog_entry
from .interfaces import TTSProvider, _coerce_json_dict
from .tts_capabilities import (
    TTSCapabilities,
    request_uses_reference_audio,
    request_uses_speaking_rate,
    resolve_language_id,
    resolve_voice_mode,
)
from .tts_result import TTSSynthesisResult


class PiperError(RuntimeError):
    """Base error for actionable Piper adapter failures."""


class PiperDependencyError(PiperError):
    """The optional Piper runtime is not installed."""


class PiperConfigurationError(PiperError):
    """The configured Piper model reference or settings are invalid."""


class PiperModelLoadError(PiperError):
    """The optional Piper model could not be initialized."""


class PiperGenerationError(PiperError):
    """The Piper backend could not synthesize audio."""


class PiperAudioValidationError(PiperError):
    """The backend output is not a usable mono 16-bit PCM WAV payload."""


def _catalog_identity(entry: PiperVoiceCatalogEntry) -> dict[str, Any]:
    return entry.to_identity_payload()


def _model_identity(
    model_key: str | None,
    model_path: Path | None,
    catalog_entry: PiperVoiceCatalogEntry | None,
) -> dict[str, Any]:
    if model_key is not None:
        if catalog_entry is None:
            raise PiperConfigurationError(
                f"Unknown Piper voice '{model_key}'."
            )
        return {
            "kind": "catalog_voice",
            **_catalog_identity(catalog_entry),
        }
    assert model_path is not None
    try:
        checksum = hashlib.sha256(model_path.read_bytes()).hexdigest()
    except OSError as exc:
        raise PiperConfigurationError("Piper model_path could not be read.") from exc
    return {
        "kind": "local_path",
        "model_name": model_path.name,
        "model_checksum": checksum,
    }


def _runtime_model_reference(model_key: str | None, model_path: Path | None) -> dict[str, Any]:
    reference: dict[str, Any] = {}
    if model_key is not None:
        reference["model_key"] = model_key
    if model_path is not None:
        reference["model_path"] = str(model_path)
    return reference


def _expected_voice_mode(model_key: str | None, model_path: Path | None) -> str:
    if model_key is not None:
        return "catalog"
    assert model_path is not None
    return "local_path"


def _validate_voice_mode(voice_mode: str, *, model_key: str | None, model_path: Path | None) -> None:
    expected_mode = _expected_voice_mode(model_key, model_path)
    if voice_mode != expected_mode:
        raise PiperConfigurationError(
            f"Piper voice_mode must be '{expected_mode}' for the configured voice asset."
        )


def _coerce_wav_bytes(audio: Any, *, output_buffer: io.BytesIO) -> bytes | None:
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
    if audio is None:
        buffered = output_buffer.getvalue()
        return buffered or None
    if hasattr(audio, "read"):
        return bytes(audio.read())
    buffered = output_buffer.getvalue()
    return buffered or None


class _LoadedPiperBackend:
    """Small runtime wrapper that converts Piper output into WAV bytes."""

    def __init__(self, backend: Any) -> None:
        self._backend = backend

    def synthesize(self, text: str, **kwargs: Any) -> bytes:
        synthesize = getattr(self._backend, "synthesize", None)
        if not callable(synthesize):
            raise PiperGenerationError("Piper runtime object does not expose synthesize().")

        attempts = (
            ((text,), kwargs),
            ((text,), {**kwargs, "output_file": io.BytesIO()}),
            ((text,), {**kwargs, "wav_file": io.BytesIO()}),
            ((text, io.BytesIO()), kwargs),
        )
        last_type_error: TypeError | None = None
        for args, call_kwargs in attempts:
            output_buffer = io.BytesIO()
            adjusted_args = list(args)
            if len(adjusted_args) > 1 and isinstance(adjusted_args[1], io.BytesIO):
                adjusted_args[1] = output_buffer
            else:
                call_kwargs = dict(call_kwargs)
                if "output_file" in call_kwargs and isinstance(call_kwargs["output_file"], io.BytesIO):
                    call_kwargs["output_file"] = output_buffer
                if "wav_file" in call_kwargs and isinstance(call_kwargs["wav_file"], io.BytesIO):
                    call_kwargs["wav_file"] = output_buffer
            try:
                result = synthesize(*adjusted_args, **call_kwargs)
            except TypeError as exc:
                last_type_error = exc
                continue
            audio_bytes = _coerce_wav_bytes(result, output_buffer=output_buffer)
            if audio_bytes is not None:
                return audio_bytes
        raise PiperGenerationError("Piper synthesis interface is incompatible.") from last_type_error


def _load_runtime_backend(model_reference: JsonDict) -> _LoadedPiperBackend:
    """Import and initialize the heavy optional runtime only on first use."""

    try:
        from piper.voice import PiperVoice
    except ImportError as exc:
        try:
            import piper  # type: ignore[import-not-found]
        except ImportError as inner_exc:
            raise PiperDependencyError(
                "Piper requires the optional 'piper-tts' dependencies. "
                "Install them with: pip install '.[piper]'"
            ) from inner_exc
        PiperVoice = getattr(getattr(piper, "voice", None), "PiperVoice", None)
        if PiperVoice is None:
            raise PiperDependencyError(
                "Piper runtime is installed but does not expose piper.voice.PiperVoice."
            ) from exc

    loader = getattr(PiperVoice, "load", None) or getattr(PiperVoice, "from_pretrained", None)
    if not callable(loader):
        raise PiperDependencyError(
            "Piper runtime is installed but does not expose a compatible model loader."
        )

    model_key = model_reference.get("model_key")
    model_path = model_reference.get("model_path")
    device = model_reference.get("device", "cpu")
    try:
        if model_path is not None:
            try:
                backend = loader(model_path=model_path, device=device)
            except TypeError:
                backend = loader(model_path, device=device)
        elif model_key is not None:
            try:
                backend = loader(model_key=model_key, device=device)
            except TypeError:
                backend = loader(model_key, device=device)
        else:
            raise PiperConfigurationError("Piper model reference must include model_key or model_path.")
    except PiperError:
        raise
    except Exception as exc:
        raise PiperModelLoadError("Piper model loading failed.") from exc
    return _LoadedPiperBackend(backend)


class PiperTTSProvider(TTSProvider):
    """Provider-neutral Piper adapter with lazy loading."""

    provider_type = ProviderType.TTS

    def __init__(
        self,
        provider_name: str = "piper",
        *,
        device: str = "cpu",
        language_id: str | None = "pl",
        model_key: str | None = None,
        model_path: str | Path | None = None,
        model_loader: Callable[[JsonDict], Any] | None = None,
    ) -> None:
        self.provider_name = provider_name
        self.device = device
        self.language_id = language_id or "pl"
        self.model_key = model_key.strip() if isinstance(model_key, str) and model_key.strip() else None
        self.model_path = Path(model_path) if model_path is not None else None
        self._model_loader = model_loader or _load_runtime_backend
        self._backend: Any | None = None
        self._catalog_entry: PiperVoiceCatalogEntry | None = None

        if not isinstance(self.device, str) or not self.device.strip():
            raise PiperConfigurationError("Piper device must be a non-empty string.")
        if self.language_id is not None and not isinstance(self.language_id, str):
            raise PiperConfigurationError("Piper language_id must be a string or null.")
        if (self.model_key is None) == (self.model_path is None):
            raise PiperConfigurationError(
                "Piper requires exactly one of model_key or model_path."
            )
        if self.model_path is not None and not self.model_path.is_file():
            raise PiperConfigurationError("Piper model_path must point to an existing file.")
        if self.model_key is not None:
            try:
                self._catalog_entry = get_piper_voice_catalog_entry(self.model_key)
            except PiperCatalogError as exc:
                raise PiperConfigurationError(str(exc)) from exc

    def capabilities(self) -> TTSCapabilities:
        return TTSCapabilities(
            provider_name=self.provider_name,
            supported_languages=("pl",),
            voice_modes=("catalog", "local_path"),
            reference_audio_required=False,
            speaking_rate_supported=False,
            usage_policy="production",
        )

    def _effective_language_id(self, voice_config: JsonDict | None) -> str:
        return resolve_language_id(voice_config, default_language_id=self.language_id) or self.language_id

    def _effective_voice_mode(self, voice_config: JsonDict | None) -> str:
        default_voice_mode = _expected_voice_mode(self.model_key, self.model_path)
        voice_mode = resolve_voice_mode(voice_config, default_voice_mode=default_voice_mode)
        _validate_voice_mode(voice_mode, model_key=self.model_key, model_path=self.model_path)
        return voice_mode

    def _model_reference(self) -> JsonDict:
        runtime_reference = _runtime_model_reference(self.model_key, self.model_path)
        runtime_reference["device"] = self.device
        runtime_reference["language_id"] = self.language_id
        runtime_reference["model_identity"] = _model_identity(
            self.model_key,
            self.model_path,
            self._catalog_entry,
        )
        if self._catalog_entry is not None:
            runtime_reference["catalog_identity"] = _catalog_identity(self._catalog_entry)
        return runtime_reference

    def _model_variant(self) -> str:
        if self.model_key is not None:
            return self.model_key
        assert self.model_path is not None
        return self.model_path.name

    def _get_backend(self) -> Any:
        if self._backend is None:
            try:
                self._backend = self._model_loader(self._model_reference())
            except PiperError:
                raise
            except Exception as exc:
                raise PiperModelLoadError("Piper model loading failed.") from exc
        return self._backend

    def effective_synthesis_identity(
        self,
        voice_config: JsonDict | None = None,
    ) -> JsonDict:
        """Return the effective configuration without loading optional runtime code."""

        config = _coerce_json_dict(voice_config)
        language_id = self._effective_language_id(config)
        voice_mode = self._effective_voice_mode(config)
        self.capabilities().validate_request(
            language_id=language_id,
            voice_mode=voice_mode,
            reference_audio_present=request_uses_reference_audio(config),
            speaking_rate_requested=request_uses_speaking_rate(config),
        )
        return {
            "provider": self.provider_name,
            "model_variant": self._model_variant(),
            "device": self.device,
            "language_id": language_id,
            "generation_settings": {},
            "voice": {
                "mode": voice_mode,
                "model": _model_identity(self.model_key, self.model_path, self._catalog_entry),
                **(
                    {"catalog": _catalog_identity(self._catalog_entry)}
                    if self._catalog_entry is not None
                    else {}
                ),
            },
        }

    def synthesize(
        self,
        text: str,
        voice_config: JsonDict | None = None,
    ) -> TTSSynthesisResult:
        if not isinstance(text, str) or not text.strip():
            raise PiperGenerationError("Piper requires non-empty synthesis text.")

        config = _coerce_json_dict(voice_config)
        language_id = self._effective_language_id(config)
        voice_mode = self._effective_voice_mode(config)
        self.capabilities().validate_request(
            language_id=language_id,
            voice_mode=voice_mode,
            reference_audio_present=request_uses_reference_audio(config),
            speaking_rate_requested=request_uses_speaking_rate(config),
        )

        runtime = self._get_backend()
        try:
            audio_bytes = runtime.synthesize(text, language_id=language_id)
        except TypeError:
            audio_bytes = runtime.synthesize(text)
        except PiperError:
            raise
        except Exception as exc:
            raise PiperGenerationError("Piper synthesis failed.") from exc

        if not isinstance(audio_bytes, (bytes, bytearray, memoryview)):
            raise PiperAudioValidationError("Piper returned invalid WAV output.")
        audio_payload = bytes(audio_bytes)
        try:
            parameters, _frames = inspect_pcm_wav(audio_payload)
        except ValueError as exc:
            raise PiperAudioValidationError(str(exc)) from exc
        if parameters.channels != 1:
            raise PiperAudioValidationError("Piper WAV output must be mono.")
        if parameters.sample_width != 2:
            raise PiperAudioValidationError(
                "Piper WAV output must use signed 16-bit PCM samples."
            )

        model_identity = _model_identity(self.model_key, self.model_path, self._catalog_entry)
        catalog_identity = _catalog_identity(self._catalog_entry) if self._catalog_entry is not None else None
        return TTSSynthesisResult(
            audio_bytes=audio_payload,
            sample_rate=parameters.sample_rate,
            duration_seconds=parameters.duration_seconds,
            audio_format="wav",
            provider_name=self.provider_name,
            metadata={
                "device": self.device,
                "language_id": language_id,
                "model_variant": self._model_variant(),
                "voice": {
                    "mode": voice_mode,
                    "model": model_identity,
                    **({"catalog": catalog_identity} if catalog_identity is not None else {}),
                },
            },
        )
