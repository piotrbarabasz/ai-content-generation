"""Versioned, deterministic project motion controls and safe camera trajectories."""

from dataclasses import asdict, dataclass
from hashlib import sha256
import math


MOTION_CONTROLS_POLICY = "motion-controls-v2"
INTENSITIES = ("off", "subtle", "medium")
ZOOM_STRENGTH = {"off": 0.0, "subtle": 0.035, "medium": 0.07}
PAN_TRAVEL = {"off": 0.0, "subtle": 0.25, "medium": 0.50}
BASE_ZOOM = 1.25  # The immutable 5:4 master contains the delivery crop.


@dataclass(frozen=True, slots=True)
class MotionConfig:
    zoom_intensity: str = "subtle"
    pan_intensity: str = "off"
    motion_policy: str = MOTION_CONTROLS_POLICY

    def __post_init__(self):
        if (self.motion_policy != MOTION_CONTROLS_POLICY
                or self.zoom_intensity not in INTENSITIES or self.pan_intensity not in INTENSITIES):
            raise ValueError("Unsupported motion policy or intensity.")

    def to_payload(self):
        return asdict(self)

    @classmethod
    def from_payload(cls, payload):
        if type(payload) is not dict or set(payload) != {"motion_policy", "zoom_intensity", "pan_intensity"}:
            raise ValueError("Malformed motion configuration.")
        return cls(**payload)

    @property
    def static(self):
        return self.zoom_intensity == self.pan_intensity == "off"


@dataclass(frozen=True, slots=True)
class SceneMotion:
    config: MotionConfig
    zoom_direction: str | None
    pan_direction: str | None

    def sample(self, width, height, progress):
        """Return zoom and bounded integer crop coordinates in a 4:4:4 raster.

        The optical center follows a monotonic path, independent of zoom. Pan
        uses a fraction of the minimum available crop travel, never its edges.
        """
        t = min(1.0, max(0.0, progress))
        eased = t * t * (3 - 2 * t)
        zt = 1 - eased if self.zoom_direction == "zoom_out" else eased
        zoom = BASE_ZOOM * (1 + ZOOM_STRENGTH[self.config.zoom_intensity] * zt)
        travel = PAN_TRAVEL[self.config.pan_intensity]
        dx = travel * (eased - 0.5) if self.pan_direction in ("left", "right") else 0
        dy = travel * (eased - 0.5) if self.pan_direction in ("up", "down") else 0
        if self.pan_direction == "left":
            dx = -dx
        if self.pan_direction == "up":
            dy = -dy
        x = math.floor((width - width / zoom) / 2 + (width - width / BASE_ZOOM) * dx)
        y = math.floor((height - height / zoom) / 2 + (height - height / BASE_ZOOM) * dy)
        return zoom, x, y

    def expressions(self, frames):
        t = f"min(1,on/{max(1, frames - 1)})"
        eased = f"(({t})*({t})*(3-2*({t})))"
        zt = f"(1-{eased})" if self.zoom_direction == "zoom_out" else eased
        zoom = f"{BASE_ZOOM}*(1+{ZOOM_STRENGTH[self.config.zoom_intensity]}*{zt})"
        travel = PAN_TRAVEL[self.config.pan_intensity]
        coordinates = []
        for dimension, forward, backward in (("iw", "right", "left"), ("ih", "down", "up")):
            sign = 1 if self.pan_direction == forward else -1 if self.pan_direction == backward else 0
            coordinates.append(f"floor(({dimension}-{dimension}/zoom)/2"
                               f"+({dimension}-{dimension}/{BASE_ZOOM})*{sign * travel}*({eased}-0.5))")
        return zoom, *coordinates


def resolve_scene_motion(scene_id, config=MotionConfig()):
    # Independent family hashes: switching pan never reverses the zoom direction.
    def choice(family, values):
        digest = sha256(f"{scene_id}:{config.motion_policy}:{family}".encode()).digest()
        return values[int.from_bytes(digest[:4], "big") % len(values)]
    return SceneMotion(config,
        choice("zoom", ("zoom_in", "zoom_out")) if config.zoom_intensity != "off" else None,
        choice("pan", ("left", "right", "up", "down")) if config.pan_intensity != "off" else None)
