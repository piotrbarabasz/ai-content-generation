"""D027 OpenAI Images adapter settings, transport and response validation."""

from __future__ import annotations

import base64
from io import BytesIO
import json
from types import SimpleNamespace

from PIL import Image
import pytest

from app.desktop.image_composition import compose_installed_image
from app.providers.image_factory import build_image_provider
from app.providers.image_generation import ImageGenerationRequest
from app.providers.openai_image import (
    OpenAIImageError,
    OpenAIImageProvider,
    OpenAIImageSettings,
    OpenAIImageTransportError,
    SDKOpenAIImageTransport,
    validate_gpt_image_2_dimensions,
)


def encoded_image(size=(1024, 1024), format="PNG"):
    target = BytesIO()
    Image.new("RGB", size, "navy").save(target, format=format)
    return base64.b64encode(target.getvalue()).decode("ascii")


def response(*, image=None, revised="A revised safe prompt", format="PNG"):
    return {"created": 123, "data": [{"b64_json": image or encoded_image(format=format),
                                       "revised_prompt": revised}],
            "usage": {"input_tokens": 8, "output_tokens": 12, "total_tokens": 20}}


class Transport:
    def __init__(self, *results):
        self.results, self.calls = list(results), []

    def create_image(self, **kwargs):
        self.calls.append(kwargs)
        result = self.results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


def provider(transport, **settings):
    return OpenAIImageProvider(
        OpenAIImageSettings.from_mapping({"model": "gpt-image-2", **settings}),
        transport=transport, environment={"OPENAI_API_KEY": "sk-image-private"},
    )


def test_gpt_image_2_defaults_low_one_webp_and_records_safe_provenance():
    transport = Transport(response(image=encoded_image((1280, 720), "WEBP")))
    image = provider(transport, moderation="low").generate(
        ImageGenerationRequest("A blue house", 1280, 720, format="WEBP")
    )
    assert (image.format, image.width, image.height) == ("WEBP", 1280, 720)
    assert image.metadata == {
        "created": 123, "revisedPrompt": "A revised safe prompt",
        "usage": {"input_tokens": 8, "output_tokens": 12, "total_tokens": 20},
    }
    call = transport.calls[0]
    assert call["api_key"] == "sk-image-private" and call["timeout_seconds"] == 120
    assert call["payload"] == {
        "model": "gpt-image-2", "prompt": "A blue house", "size": "1280x720",
        "quality": "low", "background": "auto", "moderation": "low",
        "output_format": "webp", "output_compression": 90, "n": 1,
    }
    assert "sk-image-private" not in json.dumps(call["payload"])
    capabilities = provider(Transport(response()), quality="high", moderation="low").capabilities()
    assert capabilities.seeded is False and capabilities.negative_prompt is False
    assert capabilities.settings["quality"] == "high"
    assert capabilities.settings["outputCompression"] == 90
    assert "apiKeyEnv" not in capabilities.settings


def test_jpeg_generation_sends_compression_and_verifies_dimensions():
    transport = Transport(response(image=encoded_image(format="JPEG"), format="JPEG"))
    image = provider(transport, outputCompression=72).generate(
        ImageGenerationRequest("A blue house", 1024, 1024, format="JPEG")
    )
    assert image.format == "JPEG"
    assert transport.calls[0]["payload"]["output_compression"] == 72


@pytest.mark.parametrize("settings, message", [
    ({}, "model"),
    ({"model": "other-model"}, "gpt-image"),
    ({"model": "gpt-image-1"}, "exactly gpt-image-2"),
    ({"model": "gpt-image-2", "apiKey": "secret"}, "environment variable"),
    ({"model": "gpt-image-2", "endpoint": "https://example.test"}, "Unknown"),
    ({"model": "gpt-image-2", "quality": "auto"}, "quality"),
    ({"model": "gpt-image-2", "maxRetries": 9}, "maxRetries"),
])
def test_settings_reject_implicit_model_secrets_custom_endpoint_and_bad_values(settings, message):
    with pytest.raises(ValueError, match=message) as captured:
        OpenAIImageSettings.from_mapping(settings)
    assert "secret" not in str(captured.value)


def test_missing_credentials_and_unsupported_requests_fail_before_transport():
    transport = Transport(response())
    missing = OpenAIImageProvider(OpenAIImageSettings.from_mapping({"model": "gpt-image-2"}),
                                  transport=transport, environment={})
    with pytest.raises(OpenAIImageError, match="OPENAI_API_KEY"):
        missing.generate(ImageGenerationRequest("Prompt", 1024, 1024))
    configured = provider(transport)
    for request in (
        ImageGenerationRequest("Prompt", 512, 512),
        ImageGenerationRequest("Prompt", 1024, 1024, seed=1),
        ImageGenerationRequest("Prompt", 1024, 1024, negative_prompt="blur"),
    ):
        with pytest.raises(ValueError, match="outside the supported pixel range|not supported"):
            configured.generate(request)
    assert transport.calls == []


@pytest.mark.parametrize("payload, message", [
    ({"data": []}, "exactly one"),
    ({"data": [{"url": "https://example.test/image.png"}]}, "no base64"),
    ({"data": [{"b64_json": "%%%"}]}, "invalid base64"),
    (response(image=base64.b64encode(b"not-image").decode("ascii")), "invalid PNG/JPEG/WebP"),
    (response(image=encoded_image((1024, 1536))), "differs"),
])
def test_missing_url_only_corrupt_and_wrong_dimension_outputs_fail_closed(payload, message):
    with pytest.raises(OpenAIImageError, match=message):
        provider(Transport(payload)).generate(ImageGenerationRequest("Prompt", 1024, 1024))


def test_rate_limit_retry_and_throttle_are_bounded_and_deterministic():
    clock, sleeps = [0.0], []

    def sleep(seconds):
        sleeps.append(seconds)
        clock[0] += seconds

    transport = Transport(
        OpenAIImageTransportError("limited", status_code=429, retryable=True, retry_after=3),
        response(), response(),
    )
    image_provider = OpenAIImageProvider(
        OpenAIImageSettings.from_mapping({"model": "gpt-image-2", "maxRetries": 2,
                                         "requestIntervalSeconds": 2}),
        transport=transport, environment={"OPENAI_API_KEY": "secret"},
        monotonic=lambda: clock[0], sleep=sleep,
    )
    request = ImageGenerationRequest("Prompt", 1024, 1024)
    image_provider.generate(request)
    image_provider.generate(request)
    assert sleeps == [3, 2] and len(transport.calls) == 3


def test_transient_retries_use_exponential_backoff():
    sleeps = []
    transport = Transport(OpenAIImageTransportError("timeout", retryable=True),
                          OpenAIImageTransportError("timeout", retryable=True),
                          response(image=encoded_image((1280, 720), "WEBP")))
    image_provider = OpenAIImageProvider(
        OpenAIImageSettings.from_mapping({"model": "gpt-image-2", "maxRetries": 2,
                                         "retryDelaySeconds": 1}),
        transport=transport, environment={"OPENAI_API_KEY": "secret"}, sleep=sleeps.append)
    image_provider.generate(ImageGenerationRequest("Prompt", 1280, 720, format="WEBP"))
    assert sleeps == [1, 2] and len(transport.calls) == 3


def test_sdk_transport_uses_official_client_and_sanitizes_sdk_errors():
    class Images:
        def generate(self, **kwargs):
            self.payload = kwargs
            return SimpleNamespace(data=[SimpleNamespace(b64_json="encoded")], created=123)
    class Client:
        def __init__(self, **kwargs):
            self.kwargs, self.images = kwargs, Images()
            self.closed = False
        def close(self):
            self.closed = True
    clients = []
    def factory(**kwargs):
        client = Client(**kwargs)
        clients.append(client)
        return client
    transport = SDKOpenAIImageTransport(client_factory=factory)
    result = transport.create_image(payload={"model": "gpt-image-2", "n": 1},
        api_key="private-token", timeout_seconds=12, max_response_bytes=100)
    assert result == {"data": [{"b64_json": "encoded"}], "created": 123}
    assert clients[0].kwargs == {"api_key": "private-token", "timeout": 12, "max_retries": 0}
    assert clients[0].images.payload == {"model": "gpt-image-2", "n": 1}
    assert clients[0].closed

    class AuthenticationError(Exception):
        status_code = 401
    def broken(**kwargs):
        raise AuthenticationError("contains private-token and response details")
    transport = SDKOpenAIImageTransport(client_factory=lambda **kwargs: SimpleNamespace(
        images=SimpleNamespace(generate=lambda **params: broken(**kwargs))))
    with pytest.raises(OpenAIImageTransportError, match="authentication") as captured:
        transport.create_image(payload={"model": "gpt-image-2"}, api_key="private-token",
                               timeout_seconds=1, max_response_bytes=1024)
    assert "private-token" not in str(captured.value) and "response details" not in str(captured.value)


@pytest.mark.parametrize("error_name,status,expected,retryable", [
    ("AuthenticationError", 401, "authentication", False),
    ("RateLimitError", 429, "rate limited", True),
    ("APITimeoutError", None, "timed out", True),
    ("APIConnectionError", None, "network request failed", True),
    ("InternalServerError", 503, "unavailable", True),
    ("BadRequestError", 400, "rejected", False),
])
def test_sdk_failures_are_safe_and_classified(error_name, status, expected, retryable):
    error_type = type(error_name, (Exception,), {"status_code": status})
    class Response:
        headers = {"retry-after": "2"}
    def generate(**kwargs):
        error = error_type("private-key response body")
        error.response = Response()
        raise error
    transport = SDKOpenAIImageTransport(client_factory=lambda **kwargs: SimpleNamespace(
        images=SimpleNamespace(generate=generate)))
    with pytest.raises(OpenAIImageTransportError, match=expected) as caught:
        transport.create_image(payload={"model": "gpt-image-2"}, api_key="private-key",
                               timeout_seconds=5, max_response_bytes=1024)
    assert caught.value.retryable is retryable
    assert caught.value.retry_after == (2 if retryable else None)
    assert "private-key" not in str(caught.value) and "response body" not in str(caught.value)


def test_factory_and_installed_composition_are_explicit_and_opt_in():
    assert compose_installed_image(environment={}) is None
    transport = Transport(response(image=encoded_image((1280, 720), "WEBP")))
    built = compose_installed_image(environment={
        "AICS_IMAGE_PROVIDER": "openai", "AICS_OPENAI_IMAGE_MODEL": "gpt-image-2",
        "OPENAI_API_KEY": "secret",
    }, transport=transport)
    assert built.capabilities().provider == "openai"
    assert build_image_provider("mock").capabilities().provider == "mock"
    with pytest.raises(ValueError, match="Unsupported"):
        compose_installed_image(environment={"AICS_IMAGE_PROVIDER": "other"})


@pytest.mark.parametrize("size", [
    (1280, 720), (720, 1280), (1024, 1024), (3840, 2160), (2160, 3840), (3840, 1280),
])
def test_gpt_image_2_accepts_valid_resolutions(size):
    validate_gpt_image_2_dimensions(*size)


@pytest.mark.parametrize("size", [
    (0, 720), (1281, 720), (16, 16), (800, 800), (4096, 2048), (3840, 3840),
    (4000, 256), (3840, 2176), (3856, 2128), (3840, 1264),
])
def test_gpt_image_2_rejects_invalid_resolutions_before_transport(size):
    transport = Transport(response())
    with pytest.raises(ValueError):
        provider(transport).generate(ImageGenerationRequest("Prompt", *size))
    assert not transport.calls


def test_prompt_length_is_checked_before_transport():
    transport = Transport(response())
    with pytest.raises(ValueError, match="32000"):
        provider(transport).generate(ImageGenerationRequest("x" * 32001, 1280, 720, format="WEBP"))
    assert not transport.calls


def test_webp_format_and_compression_are_sent():
    transport = Transport(response(image=encoded_image((1280, 720), "WEBP")))
    provider(transport).generate(ImageGenerationRequest("Prompt", 1280, 720, format="WEBP"))
    assert transport.calls[0]["payload"]["output_format"] == "webp"
    assert transport.calls[0]["payload"]["output_compression"] == 90


def test_wrong_returned_format_is_rejected():
    transport = Transport(response(image=encoded_image((1280, 720), "PNG")))
    with pytest.raises(OpenAIImageError, match="differs"):
        provider(transport).generate(ImageGenerationRequest("Prompt", 1280, 720, format="WEBP"))

