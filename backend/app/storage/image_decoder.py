"""Bounded, single-frame PNG/JPEG decoding behind the local intake adapter."""

from dataclasses import dataclass
import io
import warnings


@dataclass(frozen=True)
class ImageLimits:
    max_bytes: int = 16 * 1024 * 1024
    max_dimension: int = 8192
    max_pixels: int = 32_000_000

    def __post_init__(self):
        if any(type(n) is not int or n <= 0 for n in (self.max_bytes, self.max_dimension, self.max_pixels)):
            raise ValueError("Image limits must be positive integers.")


def decode_image(payload: bytes, limits: ImageLimits):
    from PIL import Image, ImageFile

    if not payload or len(payload) > limits.max_bytes:
        raise ValueError("Image exceeds the compressed byte limit or is empty.")
    # Do not silently accept a process-global opt-in to permissive decoding.
    if ImageFile.LOAD_TRUNCATED_IMAGES:
        raise ValueError("Image intake requires strict truncated-image handling.")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error")
            with Image.open(io.BytesIO(payload), formats=("PNG", "JPEG")) as image:
                width, height = image.size
                if (not 0 < width <= limits.max_dimension or not 0 < height <= limits.max_dimension
                        or width * height > limits.max_pixels):
                    raise ValueError("Image exceeds decoded dimension/pixel limits.")
                if getattr(image, "n_frames", 1) != 1 or getattr(image, "is_animated", False):
                    raise ValueError("Only single-frame images can be imported.")
                measured = {"format": image.format, "width": width, "height": height, "mode": image.mode}
                image.verify()
            with Image.open(io.BytesIO(payload), formats=("PNG", "JPEG")) as image:
                image.load()  # Header recognition alone is not validation.
                orientation = image.getexif().get(274, 1)
                if type(orientation) is not int or orientation not in range(1, 9):
                    raise ValueError("Unsupported EXIF orientation.")
                return {**measured, "orientation": orientation}
    except (OSError, ValueError, SyntaxError, Warning, Image.DecompressionBombError) as exc:
        raise ValueError("Invalid, unsupported or oversized PNG/JPEG image.") from exc
