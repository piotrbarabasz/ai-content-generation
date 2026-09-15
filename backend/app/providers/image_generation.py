"""Small provider-neutral text-to-image contracts; no decoder/model imports."""

from dataclasses import asdict, dataclass
from typing import Protocol, runtime_checkable

from app.domain.dependencies import content_fingerprint


def _text(value):
    if type(value) is not str or not value.strip():
        raise ValueError("Image prompt/provider identity must be nonempty text.")


@dataclass(frozen=True)
class ImageGenerationRequest:
    prompt: str
    width: int
    height: int
    seed: int = 0
    format: str = "PNG"
    negative_prompt: str = ""

    def __post_init__(self):
        _text(self.prompt)
        if (any(type(n) is not int or n <= 0 for n in (self.width, self.height))
                or type(self.seed) is not int or not 0 <= self.seed < 2**32
                or self.format not in ("PNG", "JPEG") or type(self.negative_prompt) is not str):
            raise ValueError("Invalid image generation settings.")

    def to_payload(self):
        return asdict(self)

    @property
    def fingerprint(self):
        return content_fingerprint(self.to_payload())


@dataclass(frozen=True)
class ImageGenerationCapabilities:
    provider: str
    model: str
    version: str
    formats: tuple[str, ...] = ("PNG",)
    max_dimension: int = 1024
    max_pixels: int = 1024 * 1024
    negative_prompt: bool = True
    seeded: bool = True

    def __post_init__(self):
        for value in (self.provider, self.model, self.version):
            _text(value)
        if (type(self.formats) is not tuple or not self.formats or len(set(self.formats)) != len(self.formats)
                or any(f not in ("PNG", "JPEG") for f in self.formats)
                or any(type(n) is not int or n <= 0 for n in (self.max_dimension, self.max_pixels))
                or type(self.negative_prompt) is not bool or type(self.seeded) is not bool):
            raise ValueError("Invalid image provider capabilities.")

    def validate(self, request: ImageGenerationRequest):
        if (request.format not in self.formats or max(request.width, request.height) > self.max_dimension
                or request.width * request.height > self.max_pixels
                or request.negative_prompt and not self.negative_prompt or not self.seeded):
            raise ValueError("Image request is not supported by the provider capabilities.")

    def to_payload(self):
        return {**asdict(self), "formats": list(self.formats)}


@dataclass(frozen=True)
class ImageGenerationResult:
    image_bytes: bytes
    format: str
    width: int
    height: int

    def __post_init__(self):
        if (type(self.image_bytes) is not bytes or not self.image_bytes
                or self.format not in ("PNG", "JPEG")
                or any(type(n) is not int or n <= 0 for n in (self.width, self.height))):
            raise ValueError("Invalid image generation result; encoded bytes are required.")


@runtime_checkable
class ImageGenerationProvider(Protocol):
    def capabilities(self) -> ImageGenerationCapabilities: ...
    def generate(self, request: ImageGenerationRequest) -> ImageGenerationResult: ...
