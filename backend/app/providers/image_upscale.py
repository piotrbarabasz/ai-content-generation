"""Provider-neutral contract for retained image derivatives."""

from dataclasses import dataclass, field
import json
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class ImageUpscaleRequest:
    image_bytes: bytes
    format: str
    width: int
    height: int
    factor: int

    def __post_init__(self):
        if (type(self.image_bytes) is not bytes or not self.image_bytes
                or self.format not in ("PNG", "JPEG")
                or type(self.width) is not int or type(self.height) is not int
                or not 0 < self.width <= 8192 or not 0 < self.height <= 8192
                or type(self.factor) is not int or self.factor not in (2, 4)):
            raise ValueError("Invalid image upscale request.")


@dataclass(frozen=True)
class ImageUpscaleCapabilities:
    provider: str
    model: str
    version: str
    runtime: str
    factors: tuple[int, ...] = (2, 4)
    tile: int = 128
    tile_pad: int = 10
    pre_pad: int = 0
    dtype: str = "float32"
    max_dimension: int = 8192
    max_pixels: int = 32_000_000

    def __post_init__(self):
        if (any(type(x) is not str or not x for x in (self.provider, self.model, self.version, self.runtime, self.dtype))
                or self.factors != (2, 4) or self.tile != 128 or self.tile_pad != 10
                or self.pre_pad != 0 or self.dtype != "float32"):
            raise ValueError("Invalid image upscale capabilities.")

    def validate(self, request):
        if (request.factor not in self.factors or request.width * request.factor > self.max_dimension
                or request.height * request.factor > self.max_dimension
                or request.width * request.height * request.factor**2 > self.max_pixels):
            raise ValueError("Upscale output exceeds supported dimensions.")

    def to_payload(self):
        return {**vars(self), "factors": list(self.factors)}


@dataclass(frozen=True)
class ImageUpscaleResult:
    image_bytes: bytes
    format: str
    width: int
    height: int
    metadata: dict = field(default_factory=dict)

    def __post_init__(self):
        if (type(self.image_bytes) is not bytes or not self.image_bytes or self.format != "PNG"
                or type(self.width) is not int or type(self.height) is not int
                or self.width <= 0 or self.height <= 0 or type(self.metadata) is not dict):
            raise ValueError("Invalid image upscale result.")
        try:
            json.dumps(self.metadata)
        except (TypeError, ValueError):
            raise ValueError("Upscale metadata must be JSON-compatible.") from None


@runtime_checkable
class ImageUpscaleProvider(Protocol):
    def capabilities(self) -> ImageUpscaleCapabilities: ...
    def upscale(self, request: ImageUpscaleRequest) -> ImageUpscaleResult: ...
