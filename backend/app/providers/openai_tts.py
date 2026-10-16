"""Official-SDK Speech adapter. No credentials, retries or pricing in request identity."""
import array
from dataclasses import asdict
from datetime import date
import io
import os
import subprocess
import threading
import wave

from app.domain.narration import NarrationSettings
from app.providers.tts_capabilities import TTSCapabilities
from app.providers.tts_result import TTSSynthesisResult
from app.tts.assembly import inspect_pcm_wav

LANGUAGES = tuple("en pl de fr es it pt nl sv da fi no cs sk uk ru el tr ar he hi ja ko zh id ro hu bg ca ta th vi".split())
SHUTDOWN = "2027-01-06"


class SpeechError(ValueError):
    def __init__(self, category, *, retryable=False, uncertain=False):
        self.category, self.retryable, self.uncertain = category, retryable, uncertain
        super().__init__(category + (": response uncertain; retry may duplicate billing." if uncertain else ""))


def normalize_speech_wav(payload, ffmpeg=None):
    """Normalize PCM and streaming RIFF headers; reject truncated declared audio."""
    if not isinstance(payload, bytes) or not 44 <= len(payload) <= 32_000_000:
        raise SpeechError("INVALID_OR_INCOMPLETE_WAV")
    if payload[:4] == b"RIFF":
        declared_size = int.from_bytes(payload[4:8], "little")
        if len(payload) - 8 < declared_size < 0x10000000:
            raise SpeechError("INTERRUPTED_AUDIO_DOWNLOAD", uncertain=True)
    try:
        with wave.open(io.BytesIO(payload), "rb") as reader:
            channels, width, rate, count = reader.getnchannels(), reader.getsampwidth(), reader.getframerate(), reader.getnframes()
            frames = reader.readframes(count)
        if len(frames) != count * channels * width and count < 0x10000000:
            raise SpeechError("INTERRUPTED_AUDIO_DOWNLOAD", uncertain=True)
    except (wave.Error, EOFError):
        channels = width = rate = 0
    if (channels, width, rate) != (1, 2, 24000):
        if not ffmpeg:
            raise SpeechError("WAV_CONVERSION_REQUIRES_CONFIGURED_FFMPEG")
        try:
            result = subprocess.run([str(ffmpeg), "-v", "error", "-f", "wav", "-i", "pipe:0", "-ac", "1", "-ar", "24000",
                "-c:a", "pcm_s16le", "-f", "wav", "pipe:1"], input=payload, capture_output=True, timeout=30,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except (OSError, subprocess.TimeoutExpired):
            raise SpeechError("AUDIO_CONVERSION_FAILED") from None
        if result.returncode:
            raise SpeechError("INVALID_SPEECH_WAV")
        return normalize_speech_wav(result.stdout)
    if not frames or len(frames) % 2:
        raise SpeechError("EMPTY_OR_PARTIAL_SPEECH")
    samples = array.array("h", frames)
    import sys
    if sys.byteorder != "little":
        samples.byteswap()
    if sum(abs(x) >= 32760 for x in samples) / len(samples) > .001:
        raise SpeechError("CLIPPED_SPEECH_AUDIO")
    output = io.BytesIO()
    with wave.open(output, "wb") as writer:
        writer.setnchannels(1); writer.setsampwidth(2); writer.setframerate(24000); writer.writeframes(frames)
    normalized = output.getvalue()
    inspect_pcm_wav(normalized)
    return normalized


class OpenAITTSProvider:
    provider_name = "openai"
    provider_type = "tts"

    def __init__(self, *, settings=None, client_factory=None, environment=None, api_key_env="OPENAI_API_KEY", ffmpeg=None, response_sink=None):
        self.settings = settings or NarrationSettings()
        self.client_factory, self.environment = client_factory, os.environ if environment is None else environment
        self.api_key_env, self.ffmpeg = api_key_env, ffmpeg
        self.canceled = threading.Event()
        self.response_sink = response_sink

    def check_configuration(self):
        if date.today().isoformat() >= SHUTDOWN:
            raise SpeechError("MODEL_SHUT_DOWN: configure a validated replacement adapter")
        if not self.environment.get(self.api_key_env, "").strip():
            raise SpeechError("MISSING_API_KEY: configure the named environment credential")

    @staticmethod
    def catalog_capabilities():
        return TTSCapabilities("openai", LANGUAGES, ("builtin",), False, True)

    capabilities = catalog_capabilities

    def effective_synthesis_identity(self, voice_config=None):
        settings = NarrationSettings.from_payload(voice_config) if voice_config else self.settings
        return {"provider": "openai", "adapter": "speech-wav-v1", "model": settings.model, "voice": {"mode": "builtin", "id": settings.voice},
                "speed": settings.speed, "instructions": settings.delivery, "pcm": {"sample_rate": 24000, "channels": 1, "sample_width": 2}}

    def validate_text(self, text, voice_config=None):
        settings = NarrationSettings.from_payload(voice_config) if voice_config else self.settings
        # UTF-8 bytes conservatively upper-bound BPE token count, including delivery instructions.
        if not text.strip() or len(text) > 4096 or len((text + settings.delivery).encode("utf-8")) > 1900:
            raise SpeechError("INPUT_LIMIT: split narration into smaller sentence chunks or shorten instructions")
        return settings

    def synthesize(self, text, voice_config=None):
        settings = self.validate_text(text, voice_config)
        self.check_configuration()
        key = self.environment.get(self.api_key_env, "").strip()
        if not key:
            raise SpeechError("MISSING_API_KEY: configure the named environment credential")
        if self.canceled.is_set():
            raise SpeechError("CANCELED")
        from openai import OpenAI
        factory = self.client_factory or OpenAI
        try:
            # SDK retries are disabled; the durable approved operation owns each attempt.
            with factory(api_key=key, max_retries=0, timeout=30) as client:
                with client.audio.speech.with_streaming_response.create(model=settings.model, voice=settings.voice,
                        speed=float(settings.speed), instructions=settings.delivery, input=text, response_format="wav") as response:
                    parts, size = [], 0
                    for part in response.iter_bytes():
                        size += len(part)
                        if self.canceled.is_set():
                            raise SpeechError("CANCELED_RESPONSE_UNCERTAIN", uncertain=True)
                        if size > 32_000_000:
                            raise SpeechError("OVERSIZED_SPEECH_RESPONSE", uncertain=True)
                        parts.append(part)
            raw = b"".join(parts)
            if self.response_sink:
                try:
                    self.response_sink(raw)
                except OSError:
                    raise SpeechError("RESPONSE_PERSISTENCE_FAILED", uncertain=True) from None
            payload = normalize_speech_wav(raw, self.ffmpeg)
        except SpeechError:
            raise
        except Exception as exc:
            status = getattr(exc, "status_code", None)
            category = {401: "AUTHENTICATION_FAILED", 403: "ACCESS_DENIED", 404: "MODEL_UNAVAILABLE",
                        400: "UNSUPPORTED_SPEECH_REQUEST", 429: "RATE_LIMIT"}.get(status, "SPEECH_NETWORK_OR_PROVIDER_FAILURE")
            raise SpeechError(category, retryable=status in (None, 408, 429) or isinstance(status, int) and status >= 500,
                              uncertain=status is None or isinstance(status, int) and status >= 500) from None
        pcm, _ = inspect_pcm_wav(payload)
        return TTSSynthesisResult(payload, pcm.sample_rate, pcm.duration_seconds, "wav", "openai",
                                  {"adapter": "speech-wav-v1", "measured": True})
