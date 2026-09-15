"""Offline typed capability checks and reproducible encoded mock output."""

from dataclasses import replace
import subprocess
import sys

import pytest

from app.providers.image_factory import build_image_provider
from app.providers.image_generation import ImageGenerationCapabilities, ImageGenerationRequest, ImageGenerationResult
from app.providers.interfaces import ImageGenerationProvider
from app.storage.image_decoder import ImageLimits, decode_image


def test_mock_contract_generates_valid_deterministic_png():
    provider = build_image_provider()
    assert isinstance(provider, ImageGenerationProvider)
    request = ImageGenerationRequest("A red tree.", 19, 13, seed=42)
    first, second = provider.generate(request), provider.generate(request)
    assert first == second
    measured = decode_image(first.image_bytes, ImageLimits())
    assert (measured["format"], measured["width"], measured["height"]) == ("PNG", 19, 13)
    assert provider.generate(replace(request, seed=43)).image_bytes != first.image_bytes


@pytest.mark.parametrize("settings", [
    {"prompt": "Other prompt"}, {"width": 33}, {"height": 33}, {"seed": 1}, {"negative_prompt": "blur"}, {"format": "JPEG"},
])
def test_every_supported_input_affects_request_fingerprint(settings):
    request = ImageGenerationRequest("Prompt", 32, 32)
    assert replace(request, **settings).fingerprint != request.fingerprint
    assert ImageGenerationRequest(**request.to_payload()) == request


@pytest.mark.parametrize("settings", [
    {"prompt": " "}, {"width": 0}, {"height": -1}, {"width": True}, {"seed": -1}, {"seed": 2**32},
    {"seed": True}, {"format": "GIF"}, {"negative_prompt": {}},
])
def test_invalid_requests_fail_without_provider(settings):
    with pytest.raises(ValueError): replace(ImageGenerationRequest("Prompt", 32, 32), **settings)


@pytest.mark.parametrize("capabilities,image_request", [
    (ImageGenerationCapabilities("p", "m", "1", max_dimension=4), ImageGenerationRequest("p", 5, 1)),
    (ImageGenerationCapabilities("p", "m", "1", max_pixels=4), ImageGenerationRequest("p", 3, 2)),
    (ImageGenerationCapabilities("p", "m", "1"), ImageGenerationRequest("p", 2, 2, format="JPEG")),
    (ImageGenerationCapabilities("p", "m", "1", negative_prompt=False), ImageGenerationRequest("p", 2, 2, negative_prompt="blur")),
    (ImageGenerationCapabilities("p", "m", "1", seeded=False), ImageGenerationRequest("p", 2, 2)),
])
def test_capabilities_reject_unsupported_features(capabilities, image_request):
    with pytest.raises(ValueError): capabilities.validate(image_request)


def test_factory_is_explicit_and_imports_no_decoder_or_mock_until_requested():
    marker = object()
    assert build_image_provider("fixture", provider_factories={"fixture": lambda: marker}) is marker
    with pytest.raises(ValueError): build_image_provider("unknown")
    code = "import sys; import app.application.image_generation; import app.providers.image_factory; assert not any(n.startswith(('PIL','sqlite3','PySide6','app.storage','app.providers.mock_image')) for n in sys.modules)"
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("payload", [b"", "image", None])
def test_result_requires_nonempty_encoded_bytes(payload):
    with pytest.raises(ValueError): ImageGenerationResult(payload, "PNG", 16, 16)
