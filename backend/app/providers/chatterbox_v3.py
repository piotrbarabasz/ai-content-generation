"""Optional, lazy Chatterbox Multilingual V3 TTS provider."""

from __future__ import annotations

import inspect
import io
import hashlib
import wave
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from app.domain.enums import ProviderType
from app.domain.types import JsonDict

from .interfaces import TTSProvider, _coerce_json_dict
from .tts_result import TTSSynthesisResult


_GENERATION_FIELDS = (
    "exaggeration",
    "cfg_weight",
    "temperature",
    "repetition_penalty",
    "min_p",
    "top_p",
)


class ChatterboxV3Error(RuntimeError):
    """Base error for actionable Chatterbox V3 adapter failures."""


class ChatterboxDependencyError(ChatterboxV3Error):
    """The optional Chatterbox runtime is not installed."""


class ChatterboxDeviceError(ChatterboxV3Error):
    """The requested device cannot be used by the optional runtime."""


class ChatterboxModelLoadError(ChatterboxV3Error):
    """The optional Chatterbox model could not be initialized."""


class ChatterboxCompatibilityError(ChatterboxV3Error):
    """The installed Chatterbox runtime does not match the validated V3 API."""


class ChatterboxGenerationError(ChatterboxV3Error):
    """The optional Chatterbox backend could not generate audio."""


class ChatterboxAudioPromptError(ChatterboxV3Error):
    """A configured optional speaker reference is not usable."""


class ChatterboxAudioValidationError(ChatterboxV3Error):
    """The backend output is not a usable 24 kHz WAV payload."""


def _validate_wav(audio_bytes: bytes) -> tuple[int, float]:
    if not audio_bytes:
        raise ChatterboxAudioValidationError("Chatterbox returned an empty WAV payload.")
    try:
        with wave.open(io.BytesIO(audio_bytes), "rb") as wav_file:
            channels = wav_file.getnchannels()
            sample_width = wav_file.getsampwidth()
            sample_rate = wav_file.getframerate()
            compression_type = wav_file.getcomptype()
            frame_count = wav_file.getnframes()
            if channels != 1:
                raise ChatterboxAudioValidationError("Chatterbox WAV output must be mono.")
            if sample_width != 2:
                raise ChatterboxAudioValidationError(
                    "Chatterbox WAV output must use signed 16-bit PCM samples."
                )
            if sample_rate != 24_000:
                raise ChatterboxAudioValidationError(
                    "Chatterbox WAV output must use a 24000 Hz sample rate."
                )
            if compression_type != "NONE":
                raise ChatterboxAudioValidationError(
                    "Chatterbox WAV output must use uncompressed PCM audio."
                )
            if frame_count <= 0:
                raise ChatterboxAudioValidationError("Chatterbox returned an empty WAV payload.")
            return sample_rate, frame_count / sample_rate
    except ChatterboxAudioValidationError:
        raise
    except (EOFError, wave.Error) as exc:
        raise ChatterboxAudioValidationError(
            "Chatterbox returned invalid WAV output."
        ) from exc


class _RuntimeBackend:
    """Small runtime wrapper that converts Chatterbox tensors into WAV bytes."""

    def __init__(self, model: Any, torchaudio: Any) -> None:
        self._model = model
        self._torchaudio = torchaudio

    def generate(self, text: str, **kwargs: Any) -> bytes:
        audio = self._model.generate(text, **kwargs)
        buffer = io.BytesIO()
        self._torchaudio.save(
            buffer,
            audio,
            24_000,
            format="wav",
            encoding="PCM_S",
            bits_per_sample=16,
        )
        return buffer.getvalue()


def _load_runtime_backend(device: str) -> _RuntimeBackend:
    """Import and initialize the heavy optional runtime only on first use."""

    try:
        import torch
        import torchaudio
        from chatterbox.mtl_tts import ChatterboxMultilingualTTS
    except ImportError as exc:
        raise ChatterboxDependencyError(
            "Chatterbox V3 requires the optional 'chatterbox-v3' dependencies. "
            "Install them with: pip install '.[chatterbox-v3]'"
        ) from exc

    _validate_from_pretrained_contract(ChatterboxMultilingualTTS.from_pretrained)

    normalized_device = device.lower()
    if normalized_device.startswith("cuda") and not torch.cuda.is_available():
        raise ChatterboxDeviceError("CUDA was requested but is unavailable.")
    if not (normalized_device == "cpu" or normalized_device.startswith("cuda")):
        raise ChatterboxDeviceError("Unsupported Chatterbox device; use 'cpu' or a CUDA device.")
    try:
        model = ChatterboxMultilingualTTS.from_pretrained(
            device=torch.device(device), t3_model="v3"
        )
    except Exception as exc:
        raise ChatterboxModelLoadError("Chatterbox V3 model loading failed.") from exc
    return _RuntimeBackend(model, torchaudio)


def _validate_from_pretrained_contract(from_pretrained: Any) -> None:
    """Require the validated Chatterbox V3 callable contract before loading weights."""

    try:
        signature = inspect.signature(from_pretrained)
    except (TypeError, ValueError) as exc:
        raise ChatterboxCompatibilityError(
            "Chatterbox V3 runtime is incompatible: from_pretrained must expose "
            "device and t3_model keyword parameters."
        ) from exc
    parameters = signature.parameters
    required_keywords = ("device", "t3_model")
    missing = [
        name
        for name in required_keywords
        if name not in parameters or parameters[name].kind == inspect.Parameter.POSITIONAL_ONLY
    ]
    if missing:
        joined = ", ".join(missing)
        raise ChatterboxCompatibilityError(
            "Chatterbox V3 runtime is incompatible: from_pretrained must accept "
            f"{joined} as keyword arguments before weights are loaded."
        )


class ChatterboxV3Provider(TTSProvider):
    """Provider-neutral Chatterbox Multilingual V3 adapter with lazy loading."""

    provider_type = ProviderType.TTS

    def __init__(
        self,
        provider_name: str = "chatterbox_v3",
        *,
        device: str = "cpu",
        language_id: str | None = "pl",
        audio_prompt_path: str | Path | None = None,
        exaggeration: float | None = None,
        cfg_weight: float | None = None,
        temperature: float | None = None,
        repetition_penalty: float | None = None,
        min_p: float | None = None,
        top_p: float | None = None,
        model_loader: Callable[[str], Any] | None = None,
    ) -> None:
        self.provider_name = provider_name
        self.device = device
        self.t3_model = "v3"
        self.language_id = language_id or "pl"
        self.audio_prompt_path = audio_prompt_path
        self._generation_defaults = {
            "exaggeration": exaggeration,
            "cfg_weight": cfg_weight,
            "temperature": temperature,
            "repetition_penalty": repetition_penalty,
            "min_p": min_p,
            "top_p": top_p,
        }
        self._model_loader = model_loader or _load_runtime_backend
        self._backend: Any | None = None

    def _get_backend(self) -> Any:
        normalized_device = self.device.lower()
        if not (normalized_device == "cpu" or normalized_device.startswith("cuda")):
            raise ChatterboxDeviceError(
                "Unsupported Chatterbox device; use 'cpu' or a CUDA device."
            )
        if self._backend is None:
            try:
                self._backend = self._model_loader(self.device)
            except ChatterboxV3Error:
                raise
            except Exception as exc:
                raise ChatterboxModelLoadError("Chatterbox V3 model loading failed.") from exc
        return self._backend

    @staticmethod
    def _audio_prompt(value: Any) -> str | None:
        if value is None:
            return None
        path = Path(value)
        if not path.is_file():
            raise ChatterboxAudioPromptError(
                "The configured optional Chatterbox audio prompt does not exist or is not a file."
            )
        return str(path)

    def _effective_configuration(
        self,
        voice_config: JsonDict | None,
    ) -> tuple[str, dict[str, Any], str | None]:
        config: Mapping[str, Any] = _coerce_json_dict(voice_config)
        language_id = config.get("language_id", self.language_id) or self.language_id
        generation_settings = {
            field: config[field] if field in config else self._generation_defaults[field]
            for field in _GENERATION_FIELDS
        }
        audio_prompt_path = self._audio_prompt(
            config.get("audio_prompt_path", self.audio_prompt_path)
        )
        return language_id, generation_settings, audio_prompt_path

    def effective_synthesis_identity(
        self,
        voice_config: JsonDict | None = None,
    ) -> JsonDict:
        """Return the effective configuration without loading optional runtime code."""

        language_id, generation_settings, audio_prompt_path = self._effective_configuration(
            voice_config
        )
        voice: JsonDict = {"mode": "builtin"}
        if audio_prompt_path is not None:
            try:
                with open(audio_prompt_path, "rb") as reference_file:
                    voice = {
                        "mode": "reference",
                        "content_checksum": hashlib.sha256(reference_file.read()).hexdigest(),
                    }
            except OSError as exc:
                raise ChatterboxAudioPromptError(
                    "Unable to read the configured Chatterbox audio prompt."
                ) from exc
        return {
            "provider": self.provider_name,
            "model_variant": self.t3_model,
            "device": self.device,
            "language_id": language_id,
            "generation_settings": generation_settings,
            "voice": voice,
        }

    def synthesize(
        self, text: str, voice_config: JsonDict | None = None
    ) -> TTSSynthesisResult:
        if not isinstance(text, str) or not text.strip():
            raise ChatterboxGenerationError("Chatterbox requires non-empty synthesis text.")
        language_id, generation_settings, audio_prompt_path = self._effective_configuration(
            voice_config
        )
        generation_kwargs = {
            key: value
            for key, value in generation_settings.items()
            if value is not None
        }
        generation_kwargs["language_id"] = language_id
        if audio_prompt_path is not None:
            generation_kwargs["audio_prompt_path"] = audio_prompt_path
        try:
            output = self._get_backend().generate(text, **generation_kwargs)
        except ChatterboxV3Error:
            raise
        except Exception as exc:
            raise ChatterboxGenerationError("Chatterbox V3 generation failed.") from exc
        if not isinstance(output, (bytes, bytearray, memoryview)):
            raise ChatterboxAudioValidationError("Chatterbox returned invalid WAV output.")
        audio_bytes = bytes(output)
        sample_rate, duration_seconds = _validate_wav(audio_bytes)
        return TTSSynthesisResult(
            audio_bytes=audio_bytes,
            sample_rate=sample_rate,
            duration_seconds=duration_seconds,
            audio_format="wav",
            provider_name=self.provider_name,
            metadata={
                "model_variant": "v3",
                "device": self.device,
                "language_id": language_id,
                "voice": "reference" if audio_prompt_path else "builtin",
            },
        )
