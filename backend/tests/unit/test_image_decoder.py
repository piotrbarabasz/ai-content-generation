"""Full decode and pre-allocation limits using small synthetic files only."""

import io

from PIL import Image, ImageFile
import pytest

from app.storage.image_decoder import ImageLimits, decode_image


def fixture(format="PNG", **kwargs):
    buffer = io.BytesIO()
    Image.new("RGB", (12, 8), "red").save(buffer, format=format, **kwargs)
    return buffer.getvalue()


def test_exact_limits_and_progressive_jpeg_decode():
    payload = fixture("JPEG", progressive=True)
    measured = decode_image(payload, ImageLimits(max_bytes=len(payload), max_dimension=12, max_pixels=96))
    assert measured == {"format": "JPEG", "width": 12, "height": 8, "mode": "RGB", "orientation": 1}


def test_oversized_header_is_rejected_before_pixel_loading(monkeypatch):
    payload = fixture()
    def forbidden(*args, **kwargs):
        pytest.fail("An oversized image reached pixel allocation")
    monkeypatch.setattr(Image.Image, "load", forbidden)
    with pytest.raises(ValueError):
        decode_image(payload, ImageLimits(max_pixels=95))


@pytest.mark.parametrize("maximum", [80, 4])
def test_pillow_bomb_warning_and_error_are_rejected_without_disabling_protection(monkeypatch, maximum):
    payload = fixture()
    monkeypatch.setattr(Image, "MAX_IMAGE_PIXELS", maximum)
    with pytest.raises(ValueError): decode_image(payload, ImageLimits())
    assert Image.MAX_IMAGE_PIXELS == maximum


def test_permissive_process_global_truncation_setting_is_not_accepted(monkeypatch):
    payload = fixture()
    monkeypatch.setattr(ImageFile, "LOAD_TRUNCATED_IMAGES", True)
    with pytest.raises(ValueError, match="strict"):
        decode_image(payload, ImageLimits())
    assert ImageFile.LOAD_TRUNCATED_IMAGES is True


def test_invalid_exif_orientation_is_rejected():
    exif = Image.Exif()
    exif[274] = 0
    with pytest.raises(ValueError): decode_image(fixture("JPEG", exif=exif), ImageLimits())


@pytest.mark.parametrize("name", ["max_bytes", "max_dimension", "max_pixels"])
@pytest.mark.parametrize("value", [0, -1, True, 1.5])
def test_limits_require_positive_integers(name, value):
    with pytest.raises(ValueError): ImageLimits(**{name: value})
