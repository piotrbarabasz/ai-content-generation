"""Presentation-boundary catalog for configured image-generation profiles."""

from dataclasses import dataclass
from collections.abc import Mapping

from app.desktop.image_composition import compose_installed_image


@dataclass(frozen=True)
class ImageGeneratorOption:
    id: str
    label: str
    provider: object
    capabilities: object
    generation_sizes: Mapping[str, tuple[int, int]]
    image_format: str

    def __post_init__(self):
        if (not self.id or not self.label or self.provider is None or self.capabilities is None
                or set(self.generation_sizes) != {"landscape", "portrait"}
                or self.image_format not in {"PNG", "JPEG", "WEBP"}):
            raise ValueError("Invalid image generator catalog option.")

    @property
    def model(self):
        return self.capabilities.model

    def dimensions(self, orientation):
        try:
            return self.generation_sizes[orientation]
        except KeyError:
            raise ValueError("Unknown image orientation.") from None

    @property
    def seeded(self):
        return self.capabilities.seeded


def compose_installed_image_generators(*, environment=None, transports=None):
    """Compose independently configured generators without selecting a fallback."""
    import os

    environment = os.environ if environment is None else environment
    transports = transports or {}
    selected = str(environment.get("AICS_IMAGE_PROVIDER", "")).strip().lower()
    if selected not in {"", "local", "openai"}:
        raise ValueError(f"Unsupported installed image provider: {selected}.")

    options = []
    local_root = str(environment.get("AICS_LOCAL_IMAGE_ROOT", "")).strip()
    if local_root:
        local_environment = dict(environment)
        local_environment["AICS_IMAGE_PROVIDER"] = "local"
        try:
            provider = compose_installed_image(environment=local_environment, verify=False)
            capabilities = provider.capabilities()
        except (ValueError, OSError):
            provider = capabilities = None
        if provider is not None and capabilities is not None:
            options.append(ImageGeneratorOption(
                "local-sd15", "Local — Stable Diffusion 1.5", provider, capabilities,
                {"landscape": (640, 360), "portrait": (360, 640)}, "PNG"))

    model = str(environment.get("AICS_OPENAI_IMAGE_MODEL", "")).strip()
    if model:
        openai_environment = dict(environment)
        openai_environment["AICS_IMAGE_PROVIDER"] = "openai"
        try:
            provider = compose_installed_image(environment=openai_environment,
                                                transport=transports.get("openai"))
            capabilities = provider.capabilities()
        except (ValueError, OSError):
            provider = capabilities = None
        if provider is not None and capabilities is not None:
            options.append(ImageGeneratorOption(
                "openai-gpt-image-2", "OpenAI — GPT Image 2", provider, capabilities,
                {"landscape": (1280, 720), "portrait": (720, 1280)}, "WEBP"))
    elif selected == "openai":
        raise ValueError("AICS_IMAGE_PROVIDER=openai requires AICS_OPENAI_IMAGE_MODEL=gpt-image-2.")

    default_id = {"local": "local-sd15", "openai": "openai-gpt-image-2"}.get(selected)
    if default_id is not None and not any(option.id == default_id for option in options):
        default_id = None
    return tuple(options), default_id


__all__ = ["ImageGeneratorOption", "compose_installed_image_generators"]
