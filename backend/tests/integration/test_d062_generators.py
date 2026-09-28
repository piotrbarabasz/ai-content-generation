"""D062 provider catalog composition, independent availability and cache identity."""

from app.desktop import image_generators
from app.providers.image_generation import ImageGenerationCapabilities
from app.runtime.worker_bundle import source_files


class LocalProvider:
    def capabilities(self):
        return ImageGenerationCapabilities("local", "sd15-profile", "profile-v1", seeded=True,
            max_dimension=640, max_pixels=409_600, supported_sizes=((640, 360), (360, 640)))


class OpenAITransport:
    def create_image(self, **kwargs):
        raise AssertionError("Catalog construction must not make an API request.")


def test_local_and_openai_are_catalogued_independently_with_explicit_default(monkeypatch):
    assert "app/providers/image_generation.py" not in source_files()
    original = image_generators.compose_installed_image
    def compose(*, environment, transport=None, verify=True):
        if environment.get("AICS_IMAGE_PROVIDER") == "local":
            return LocalProvider()
        return original(environment=environment, transport=transport, verify=verify)
    monkeypatch.setattr(image_generators, "compose_installed_image", compose)
    environment = {
        "AICS_IMAGE_PROVIDER": "local",
        "AICS_LOCAL_IMAGE_ROOT": "D:/managed/local-image",
        "AICS_OPENAI_IMAGE_MODEL": "gpt-image-2",
        "AICS_OPENAI_IMAGE_QUALITY": "low",
        "AICS_OPENAI_IMAGE_OUTPUT_COMPRESSION": "90",
    }
    options, default_id = image_generators.compose_installed_image_generators(
        environment=environment, transports={"openai": OpenAITransport()})
    assert [option.id for option in options] == ["local-sd15", "openai-gpt-image-2"]
    assert default_id == "local-sd15"
    assert [option.dimensions("landscape") for option in options] == [(640, 360), (1280, 720)]
    assert [option.dimensions("portrait") for option in options] == [(360, 640), (720, 1280)]
    assert options[0].seeded is True and options[1].seeded is False
    assert options[1].model == "gpt-image-2"


def test_openai_only_selection_is_opt_in_and_missing_key_is_lazy():
    options, default_id = image_generators.compose_installed_image_generators(environment={
        "AICS_IMAGE_PROVIDER": "openai", "AICS_OPENAI_IMAGE_MODEL": "gpt-image-2",
        "AICS_OPENAI_IMAGE_QUALITY": "low",
    }, transports={"openai": OpenAITransport()})
    assert [option.id for option in options] == ["openai-gpt-image-2"]
    assert default_id == "openai-gpt-image-2"


def test_missing_local_runtime_does_not_hide_valid_openai(monkeypatch):
    original = image_generators.compose_installed_image
    def compose(*, environment, transport=None, verify=True):
        if environment.get("AICS_IMAGE_PROVIDER") == "local":
            raise ValueError("managed local runtime unavailable")
        return original(environment=environment, transport=transport, verify=verify)
    monkeypatch.setattr(image_generators, "compose_installed_image", compose)
    options, default_id = image_generators.compose_installed_image_generators(environment={
        "AICS_IMAGE_PROVIDER": "local", "AICS_LOCAL_IMAGE_ROOT": "D:/missing",
        "AICS_OPENAI_IMAGE_MODEL": "gpt-image-2",
    }, transports={"openai": OpenAITransport()})
    assert [option.id for option in options] == ["openai-gpt-image-2"]
    assert default_id is None  # No implicit paid fallback from the configured local default.
