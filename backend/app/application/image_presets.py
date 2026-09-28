"""Fixed orientation and final-resolution choices for the desktop Visuals flow."""

from math import gcd

ORIENTATIONS = {
    "landscape": {"label": "Landscape (16:9)", "generation": (640, 360)},
    "portrait": {"label": "Portrait (9:16)", "generation": (360, 640)},
}

RESOLUTIONS = {
    "draft": "Draft / Source",
    "fhd": "Full HD",
    "qhd": "QHD / 1440p",
    "uhd4k": "4K UHD",
}

FINAL_DIMENSIONS = {
    ("landscape", "draft"): (640, 360),
    ("landscape", "fhd"): (1920, 1080),
    ("landscape", "qhd"): (2560, 1440),
    ("landscape", "uhd4k"): (3840, 2160),
    ("portrait", "draft"): (360, 640),
    ("portrait", "fhd"): (1080, 1920),
    ("portrait", "qhd"): (1440, 2560),
    ("portrait", "uhd4k"): (2160, 3840),
}


def generation_dimensions(orientation):
    try:
        return ORIENTATIONS[orientation]["generation"]
    except (KeyError, TypeError):
        raise ValueError("Choose Landscape (16:9) or Portrait (9:16).") from None


def final_dimensions(orientation, resolution):
    try:
        return FINAL_DIMENSIONS[(orientation, resolution)]
    except (KeyError, TypeError):
        raise ValueError("Choose Draft / Source, Full HD, QHD / 1440p or 4K UHD.") from None


def compatible_aspect(width, height, target_width, target_height):
    # Permit at most one source-pixel rounding from an exact target ratio.
    return abs(width * target_height - height * target_width) <= target_height


def orientation_compatible(width, height, orientation):
    target_width, target_height = generation_dimensions(orientation)
    return compatible_aspect(width, height, target_width, target_height)


def aspect_label(width, height):
    divisor = gcd(width, height)
    return f"{width // divisor}:{height // divisor}"
