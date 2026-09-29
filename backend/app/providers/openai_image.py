"""OpenAI Images API adapter for the provider-neutral image contract."""

from __future__ import annotations

import base64
import binascii
from collections.abc import Callable, Mapping
from dataclasses import dataclass
import io
import os
import re
import threading
import time
from typing import Any, Protocol
import warnings

from PIL import Image

from app.domain.types import JsonDict
from app.providers.image_generation import (
    ImageGenerationCapabilities,
    ImageGenerationRequest,
    ImageGenerationResult,
)


IMAGES_ENDPOINT = "/v1/images/generations"
SUPPORTED_SIZES = ((1280, 720), (720, 1280))
GPT_IMAGE_2_MIN_PIXELS = 655_360
GPT_IMAGE_2_MAX_PIXELS = 8_294_400
GPT_IMAGE_2_MAX_EDGE = 3840
GPT_IMAGE_2_MAX_RATIO = 3
GPT_IMAGE_2_MAX_PROMPT_CHARS = 32_000


def validate_gpt_image_2_dimensions(width: int, height: int) -> None:
    """Validate a GPT Image 2 resolution independently of the desktop presets."""
    if (type(width) is not int or type(height) is not int or width <= 0 or height <= 0
            or width % 16 or height % 16):
        raise ValueError("GPT Image 2 dimensions must be positive multiples of 16.")
    pixels = width * height
    if not GPT_IMAGE_2_MIN_PIXELS <= pixels <= GPT_IMAGE_2_MAX_PIXELS:
        raise ValueError("GPT Image 2 dimensions are outside the supported pixel range.")
    if max(width, height) > GPT_IMAGE_2_MAX_EDGE:
        raise ValueError("GPT Image 2 dimensions exceed the maximum edge of 3840 pixels.")
    if max(width, height) > min(width, height) * GPT_IMAGE_2_MAX_RATIO:
        raise ValueError("GPT Image 2 dimensions may not exceed a 3:1 aspect ratio.")
_SECRET_KEYS = {"apiKey", "api_key", "authorization", "bearer", "credential",
                "credentials", "secret", "token"}


class OpenAIImageError(RuntimeError):
    """Credential-safe OpenAI image provider failure."""


class OpenAIImageTransportError(OpenAIImageError):
    def __init__(self, message: str, *, status_code: int | None = None,
                 retryable: bool = False, retry_after: float | None = None) -> None:
        super().__init__(message)
        self.status_code, self.retryable, self.retry_after = status_code, retryable, retry_after


class OpenAIImageTransport(Protocol):
    def create_image(self, *, payload: JsonDict, api_key: str, timeout_seconds: float,
                     max_response_bytes: int) -> Mapping[str, Any]: ...


@dataclass(frozen=True, slots=True)
class OpenAIImageSettings:
    model: str
    api_key_env: str = "OPENAI_API_KEY"
    quality: str = "low"
    background: str = "auto"
    moderation: str = "auto"
    output_compression: int = 90
    timeout_seconds: float = 120.0
    max_image_bytes: int = 16 * 1024 * 1024
    max_retries: int = 2
    request_interval_seconds: float = 0.0
    retry_delay_seconds: float = 1.0

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "OpenAIImageSettings":
        if not isinstance(value, Mapping):
            raise ValueError("OpenAI image settings must be an object.")
        aliases = {
            "api_key_env": "apiKeyEnv", "max_image_bytes": "maxImageBytes",
            "max_retries": "maxRetries", "output_compression": "outputCompression",
            "request_interval_seconds": "requestIntervalSeconds",
            "retry_delay_seconds": "retryDelaySeconds", "timeout_seconds": "timeoutSeconds",
        }
        allowed = {"apiKeyEnv", "background", "maxImageBytes", "maxRetries", "model",
                   "moderation", "outputCompression", "quality", "requestIntervalSeconds",
                   "retryDelaySeconds", "timeoutSeconds"}
        raw = dict(value)
        if set(raw) & _SECRET_KEYS:
            raise ValueError(
                "OpenAI credentials must be supplied through an environment variable, not image settings."
            )
        normalized: dict[str, Any] = {}
        for key, item in raw.items():
            canonical = aliases.get(key, key)
            if canonical in normalized:
                raise ValueError(f"OpenAI image setting {canonical} was supplied more than once.")
            normalized[canonical] = item
        unknown = sorted(set(normalized) - allowed)
        if unknown:
            raise ValueError("Unknown OpenAI image setting(s): " + ", ".join(unknown) + ".")
        model = normalized.get("model")
        if model != "gpt-image-2":
            raise ValueError("OpenAI image model must be exactly gpt-image-2.")
        api_key_env = normalized.get("apiKeyEnv", "OPENAI_API_KEY")
        if not isinstance(api_key_env, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", api_key_env):
            raise ValueError("OpenAI image apiKeyEnv must be an environment variable name.")
        quality, background, moderation = (normalized.get("quality", "low"),
                                            normalized.get("background", "auto"),
                                            normalized.get("moderation", "auto"))
        if not isinstance(quality, str) or quality not in {"low", "medium", "high"}:
            raise ValueError("GPT Image 2 quality must be low, medium or high.")
        if not isinstance(background, str) or background not in {"auto", "opaque", "transparent"}:
            raise ValueError("OpenAI image background must be auto, opaque or transparent.")
        if not isinstance(moderation, str) or moderation not in {"auto", "low"}:
            raise ValueError("OpenAI image moderation must be auto or low.")

        def number(name, default, minimum, maximum):
            item = normalized.get(name, default)
            if isinstance(item, bool) or not isinstance(item, (int, float)) or not minimum <= item <= maximum:
                raise ValueError(f"OpenAI image {name} must be between {minimum:g} and {maximum:g}.")
            return float(item)

        compression = normalized.get("outputCompression", 90)
        image_bytes = normalized.get("maxImageBytes", 16 * 1024 * 1024)
        retries = normalized.get("maxRetries", 2)
        if isinstance(compression, bool) or not isinstance(compression, int) or not 0 <= compression <= 100:
            raise ValueError("OpenAI image outputCompression must be an integer between 0 and 100.")
        if isinstance(image_bytes, bool) or not isinstance(image_bytes, int) or not 1 <= image_bytes <= 16 * 1024 * 1024:
            raise ValueError("OpenAI image maxImageBytes must be between 1 and 16777216.")
        if isinstance(retries, bool) or not isinstance(retries, int) or not 0 <= retries <= 5:
            raise ValueError("OpenAI image maxRetries must be an integer between 0 and 5.")
        return cls(model, api_key_env, quality, background, moderation, compression,
                   number("timeoutSeconds", 120, 1, 300), image_bytes, retries,
                   number("requestIntervalSeconds", 0, 0, 60),
                   number("retryDelaySeconds", 1, 0, 60))

    def public_payload(self) -> JsonDict:
        return {
            "model": self.model, "apiKeyEnv": self.api_key_env, "quality": self.quality,
            "background": self.background, "moderation": self.moderation,
            "outputCompression": self.output_compression, "timeoutSeconds": self.timeout_seconds,
            "maxImageBytes": self.max_image_bytes, "maxRetries": self.max_retries,
            "requestIntervalSeconds": self.request_interval_seconds,
            "retryDelaySeconds": self.retry_delay_seconds,
        }


class SDKOpenAIImageTransport:
    """Official SDK adapter. Client construction and network I/O stay request-lazy."""

    def __init__(self, *, client_factory: Callable[..., Any] | None = None) -> None:
        self._client_factory = client_factory

    def create_image(self, *, payload: JsonDict, api_key: str, timeout_seconds: float,
                     max_response_bytes: int) -> Mapping[str, Any]:
        factory = self._client_factory
        if factory is None:
            try:
                from openai import OpenAI
            except ImportError:
                raise OpenAIImageTransportError("OpenAI SDK is unavailable; install the application dependencies.") from None
            factory = OpenAI
        client = None
        try:
            client = factory(api_key=api_key, timeout=timeout_seconds, max_retries=0)
            response = client.images.generate(**payload)
        except Exception as exc:
            status = getattr(exc, "status_code", None)
            status = status if type(status) is int else None
            name = type(exc).__name__
            retry_after = None
            response_obj = getattr(exc, "response", None)
            headers = getattr(response_obj, "headers", None)
            try:
                retry_after = float(headers.get("retry-after")) if headers else None
            except (TypeError, ValueError):
                pass
            if name == "AuthenticationError" or status in (401, 403):
                message, retryable = "OpenAI image authentication failed.", False
            elif name == "RateLimitError" or status == 429:
                message, retryable = "OpenAI image request was rate limited.", True
            elif name in {"APITimeoutError", "TimeoutError"}:
                message, retryable = "OpenAI image request timed out.", True
            elif name in {"APIConnectionError", "ConnectError", "ReadError"}:
                message, retryable = "OpenAI image network request failed.", True
            elif status is not None and 500 <= status <= 599:
                message, retryable = "OpenAI image service is unavailable.", True
            elif status is not None and 400 <= status <= 499:
                message, retryable = "OpenAI rejected the image request.", False
            else:
                message, retryable = "OpenAI image request failed.", False
            raise OpenAIImageTransportError(message, status_code=status, retryable=retryable,
                                            retry_after=retry_after if retryable else None) from None
        finally:
            close = getattr(client, "close", None)
            if callable(close):
                try:
                    close()
                except Exception:
                    pass
        items = getattr(response, "data", None)
        if items is None and isinstance(response, Mapping):
            items = response.get("data")
        if not isinstance(items, (list, tuple)):
            raise OpenAIImageTransportError("OpenAI returned an invalid image response.")
        data = []
        for item in items:
            encoded = getattr(item, "b64_json", None)
            if encoded is None and isinstance(item, Mapping):
                encoded = item.get("b64_json")
            if isinstance(encoded, str) and len(encoded) > max_response_bytes:
                raise OpenAIImageTransportError("OpenAI image response exceeded the configured byte limit.")
            row = {"b64_json": encoded}
            revised = getattr(item, "revised_prompt", None)
            if revised is None and isinstance(item, Mapping):
                revised = item.get("revised_prompt")
            if revised is not None:
                row["revised_prompt"] = revised
            data.append(row)
        created = getattr(response, "created", None)
        usage = getattr(response, "usage", None)
        result = {"data": data}
        if created is not None:
            result["created"] = created
        if usage is not None:
            result["usage"] = usage.model_dump() if callable(getattr(usage, "model_dump", None)) else usage
        return result


# Preserve the historical import name for callers that injected a transport.
UrllibOpenAIImageTransport = SDKOpenAIImageTransport


class OpenAIImageProvider:
    def __init__(self, settings: OpenAIImageSettings, *, transport: OpenAIImageTransport | None = None,
                 environment: Mapping[str, str] | None = None,
                 monotonic: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], None] = time.sleep) -> None:
        self.settings = settings
        self._transport = transport or UrllibOpenAIImageTransport()
        self._environment = os.environ if environment is None else environment
        self._monotonic, self._sleep = monotonic, sleep
        self._lock = threading.Lock()
        self._last_request_at: float | None = None

    def capabilities(self) -> ImageGenerationCapabilities:
        return ImageGenerationCapabilities(
            "openai", self.settings.model, "gpt-image-2-images-v1", formats=("PNG", "JPEG", "WEBP"),
            max_dimension=GPT_IMAGE_2_MAX_EDGE, max_pixels=GPT_IMAGE_2_MAX_PIXELS,
            negative_prompt=False, seeded=False, supported_sizes=(),
            settings={key: value for key, value in self.settings.public_payload().items()
                      if key not in {"apiKeyEnv", "timeoutSeconds", "maxRetries",
                                     "requestIntervalSeconds", "retryDelaySeconds"}},
        )

    def generate(self, request: ImageGenerationRequest) -> ImageGenerationResult:
        if len(request.prompt) > GPT_IMAGE_2_MAX_PROMPT_CHARS:
            raise ValueError("GPT Image 2 prompts cannot exceed 32000 characters.")
        validate_gpt_image_2_dimensions(request.width, request.height)
        self.capabilities().validate(request)
        output_format = request.format.lower()
        payload = {
            "model": self.settings.model, "prompt": request.prompt,
            "size": f"{request.width}x{request.height}", "quality": self.settings.quality,
            "background": self.settings.background, "moderation": self.settings.moderation,
            "output_format": output_format, "n": 1,
        }
        if output_format in {"jpeg", "webp"}:
            payload["output_compression"] = self.settings.output_compression
        response = self._request(payload)
        image_bytes, response_metadata = self._decode_response(response)
        actual_format, width, height = self._inspect(image_bytes)
        if (actual_format, width, height) != (request.format, request.width, request.height):
            raise OpenAIImageError("OpenAI image differs from the requested format or dimensions.")
        return ImageGenerationResult(image_bytes, actual_format, width, height, response_metadata)

    def _api_key(self) -> str:
        value = self._environment.get(self.settings.api_key_env, "")
        if not isinstance(value, str) or not value.strip():
            raise OpenAIImageError(
                f"OpenAI image credentials are unavailable; set {self.settings.api_key_env}."
            )
        return value.strip()

    def _throttle(self) -> None:
        with self._lock:
            now = self._monotonic()
            if self._last_request_at is not None:
                remaining = self.settings.request_interval_seconds - (now - self._last_request_at)
                if remaining > 0:
                    self._sleep(remaining)
                    now = self._monotonic()
            self._last_request_at = now

    def _request(self, payload: JsonDict) -> Mapping[str, Any]:
        key = self._api_key()
        response_limit = min(24 * 1024 * 1024,
                             ((self.settings.max_image_bytes + 2) // 3) * 4 + 1024 * 1024)
        for attempt in range(self.settings.max_retries + 1):
            self._throttle()
            try:
                return self._transport.create_image(payload=payload, api_key=key,
                                                    timeout_seconds=self.settings.timeout_seconds,
                                                    max_response_bytes=response_limit)
            except OpenAIImageTransportError as exc:
                if not exc.retryable or attempt >= self.settings.max_retries:
                    raise
                delay = (exc.retry_after if exc.retry_after is not None else
                         self.settings.retry_delay_seconds * (2 ** attempt))
                self._sleep(min(max(delay, 0.0), 60.0))
        raise AssertionError("unreachable")

    def _decode_response(self, response: Mapping[str, Any]) -> tuple[bytes, JsonDict]:
        data = response.get("data")
        if not isinstance(data, list) or len(data) != 1 or not isinstance(data[0], Mapping):
            raise OpenAIImageError("OpenAI image response must contain exactly one image.")
        encoded = data[0].get("b64_json")
        if not isinstance(encoded, str) or not encoded:
            raise OpenAIImageError("OpenAI image response contained no base64 image data.")
        if len(encoded) > ((self.settings.max_image_bytes + 2) // 3) * 4:
            raise OpenAIImageError("OpenAI image exceeded the configured byte limit.")
        try:
            image_bytes = base64.b64decode(encoded, validate=True)
        except (ValueError, binascii.Error):
            raise OpenAIImageError("OpenAI image response contained invalid base64 data.") from None
        if not image_bytes or len(image_bytes) > self.settings.max_image_bytes:
            raise OpenAIImageError("OpenAI image exceeded the configured byte limit.")
        metadata: JsonDict = {}
        if isinstance(response.get("created"), int) and not isinstance(response["created"], bool):
            metadata["created"] = response["created"]
        if isinstance(data[0].get("revised_prompt"), str):
            metadata["revisedPrompt"] = data[0]["revised_prompt"]
        usage = response.get("usage")
        if isinstance(usage, Mapping):
            safe_usage = {
                key: usage[key]
                for key in ("input_tokens", "output_tokens", "total_tokens", "image_tokens", "text_tokens")
                if isinstance(usage.get(key), int) and not isinstance(usage[key], bool) and usage[key] >= 0
            }
            if safe_usage:
                metadata["usage"] = safe_usage
        return image_bytes, metadata

    @staticmethod
    def _inspect(payload: bytes) -> tuple[str, int, int]:
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("error")
                with Image.open(io.BytesIO(payload), formats=("PNG", "JPEG", "WEBP")) as image:
                    measured = image.format, *image.size
                    if getattr(image, "n_frames", 1) != 1 or getattr(image, "is_animated", False):
                        raise ValueError
                    image.verify()
                with Image.open(io.BytesIO(payload), formats=("PNG", "JPEG", "WEBP")) as image:
                    image.load()
            return measured
        except (OSError, SyntaxError, ValueError, Warning, Image.DecompressionBombError):
            raise OpenAIImageError("OpenAI returned an invalid PNG/JPEG/WebP image.") from None


__all__ = ["IMAGES_ENDPOINT", "OpenAIImageError", "OpenAIImageProvider", "OpenAIImageSettings",
           "OpenAIImageTransport", "OpenAIImageTransportError", "SUPPORTED_SIZES",
           "UrllibOpenAIImageTransport", "SDKOpenAIImageTransport", "validate_gpt_image_2_dimensions"]
