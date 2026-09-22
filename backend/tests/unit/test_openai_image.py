"""D027 OpenAI Images adapter settings, transport and response validation."""

from __future__ import annotations

import base64
from io import BytesIO
import json
from urllib.error import HTTPError

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
    UrllibOpenAIImageTransport,
)


def encoded_image(size=(1024, 1024), format="PNG"):
    target = BytesIO()
    Image.new("RGB", size, "navy").save(target, format=format)
    return base64.b64encode(target.getvalue()).decode("ascii")


def response(*, image=None, revised="A revised safe prompt"):
    return {"created": 123, "data": [{"b64_json": image or encoded_image(),
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
        OpenAIImageSettings.from_mapping({"model": "gpt-image-fixture", **settings}),
        transport=transport, environment={"OPENAI_API_KEY": "sk-image-private"},
    )


def test_png_generation_maps_exact_api_settings_and_records_safe_provenance():
    transport = Transport(response())
    image = provider(transport, quality="high", moderation="low").generate(
        ImageGenerationRequest("A blue house", 1024, 1024)
    )
    assert (image.format, image.width, image.height) == ("PNG", 1024, 1024)
    assert image.metadata == {
        "created": 123, "revisedPrompt": "A revised safe prompt",
        "usage": {"input_tokens": 8, "output_tokens": 12, "total_tokens": 20},
    }
    call = transport.calls[0]
    assert call["api_key"] == "sk-image-private" and call["timeout_seconds"] == 120
    assert call["payload"] == {
        "model": "gpt-image-fixture", "prompt": "A blue house", "size": "1024x1024",
        "quality": "high", "background": "auto", "moderation": "low",
        "output_format": "png", "n": 1,
    }
    assert "sk-image-private" not in json.dumps(call["payload"])
    capabilities = provider(Transport(response()), quality="high", moderation="low").capabilities()
    assert capabilities.seeded is False and capabilities.negative_prompt is False
    assert capabilities.settings["quality"] == "high"
    assert "apiKeyEnv" not in capabilities.settings


def test_jpeg_generation_sends_compression_and_verifies_dimensions():
    transport = Transport(response(image=encoded_image(format="JPEG")))
    image = provider(transport, outputCompression=72).generate(
        ImageGenerationRequest("A blue house", 1024, 1024, format="JPEG")
    )
    assert image.format == "JPEG"
    assert transport.calls[0]["payload"]["output_compression"] == 72


@pytest.mark.parametrize("settings, message", [
    ({}, "model"),
    ({"model": "other-model"}, "gpt-image"),
    ({"model": "gpt-image-x", "apiKey": "secret"}, "environment variable"),
    ({"model": "gpt-image-x", "endpoint": "https://example.test"}, "Unknown"),
    ({"model": "gpt-image-x", "quality": "ultra"}, "quality"),
    ({"model": "gpt-image-x", "maxRetries": 9}, "maxRetries"),
])
def test_settings_reject_implicit_model_secrets_custom_endpoint_and_bad_values(settings, message):
    with pytest.raises(ValueError, match=message) as captured:
        OpenAIImageSettings.from_mapping(settings)
    assert "secret" not in str(captured.value)


def test_missing_credentials_and_unsupported_requests_fail_before_transport():
    transport = Transport(response())
    missing = OpenAIImageProvider(OpenAIImageSettings.from_mapping({"model": "gpt-image-x"}),
                                  transport=transport, environment={})
    with pytest.raises(OpenAIImageError, match="OPENAI_API_KEY"):
        missing.generate(ImageGenerationRequest("Prompt", 1024, 1024))
    configured = provider(transport)
    for request in (
        ImageGenerationRequest("Prompt", 512, 512),
        ImageGenerationRequest("Prompt", 1024, 1024, seed=1),
        ImageGenerationRequest("Prompt", 1024, 1024, negative_prompt="blur"),
    ):
        with pytest.raises(ValueError, match="not supported"):
            configured.generate(request)
    assert transport.calls == []


@pytest.mark.parametrize("payload, message", [
    ({"data": []}, "exactly one"),
    ({"data": [{"url": "https://example.test/image.png"}]}, "no base64"),
    ({"data": [{"b64_json": "%%%"}]}, "invalid base64"),
    (response(image=base64.b64encode(b"not-image").decode("ascii")), "invalid PNG/JPEG"),
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
        OpenAIImageSettings.from_mapping({"model": "gpt-image-x", "maxRetries": 1,
                                         "requestIntervalSeconds": 2}),
        transport=transport, environment={"OPENAI_API_KEY": "secret"},
        monotonic=lambda: clock[0], sleep=sleep,
    )
    request = ImageGenerationRequest("Prompt", 1024, 1024)
    image_provider.generate(request)
    image_provider.generate(request)
    assert sleeps == [3, 2] and len(transport.calls) == 3


def test_http_error_discards_remote_body_url_and_credentials():
    def opener(*args, **kwargs):
        del args, kwargs
        raise HTTPError("https://api.openai.com/images?key=secret", 401, "secret", {},
                        BytesIO(b'{"error":"sk-image-private"}'))

    transport = UrllibOpenAIImageTransport(opener=opener)
    with pytest.raises(OpenAIImageTransportError, match="authentication") as captured:
        transport.create_image(payload={"model": "x"}, api_key="sk-image-private",
                               timeout_seconds=1, max_response_bytes=1024)
    assert "private" not in str(captured.value) and "key=" not in str(captured.value)


def test_factory_and_installed_composition_are_explicit_and_opt_in():
    assert compose_installed_image(environment={}) is None
    transport = Transport(response())
    built = compose_installed_image(environment={
        "AICS_IMAGE_PROVIDER": "openai", "AICS_OPENAI_IMAGE_MODEL": "gpt-image-x",
        "OPENAI_API_KEY": "secret",
    }, transport=transport)
    assert built.capabilities().provider == "openai"
    assert build_image_provider("mock").capabilities().provider == "mock"
    with pytest.raises(ValueError, match="Unsupported"):
        compose_installed_image(environment={"AICS_IMAGE_PROVIDER": "other"})

