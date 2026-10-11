"""Desktop render contract, distinct from the legacy renderer's reference dictionary."""

from dataclasses import asdict, dataclass
from fractions import Fraction

from .caption_track import PublishedCaptionTrack
from .dependencies import InputEdge, RequestFingerprint
from .timeline import OutputTimebase, TimelineRevision
from .scene_motion import MotionConfig, MOTION_CONTROLS_POLICY


OPERATION = "timeline.render"
PROFILE = "motion-mp4-v1"
LEGACY_PROFILE = "static-mp4-720p25-v1"
MOTION_POLICY_VERSION = "auto-subtle-v1"


def delivery_profile(timeline):
    """Resolve one retained delivery profile; metadata-free histories stay 720p."""
    values = set()
    for clip in timeline.clips:
        image = clip.media.image
        if image.provenance == "motion_master":
            values.add((image.target_profile, image.delivery_width, image.delivery_height))
        elif image.provenance == "final":
            values.add((image.target_profile, image.target_width, image.target_height))
    if not values:
        return "legacy-720p", 1280, 720
    if len(values) != 1:
        raise ValueError("Final timeline clips must use one delivery profile and dimension pair.")
    profile, width, height = next(iter(values))
    return profile, width, height


def resolve_motion(scene_id, policy_version=MOTION_POLICY_VERSION):
    from hashlib import sha256
    modes = ("zoom_in", "zoom_out", "pan_left", "pan_right", "pan_up", "pan_down")
    digest = sha256(f"{scene_id}:{policy_version}".encode("utf-8")).digest()
    return modes[int.from_bytes(digest[:4], "big") % len(modes)]


def render_request(timeline, identity, captions=None, *, algorithm_version=None, motion=None):
    if motion is None and identity.get("motion_policy") == MOTION_CONTROLS_POLICY:
        motion = MotionConfig.from_payload({k: identity[k] for k in
                                           ("motion_policy", "zoom_intensity", "pan_intensity")})
    if algorithm_version is None:
        algorithm_version = "3" if motion is not None else "2"
    if not isinstance(timeline, TimelineRevision) or timeline.timebase != OutputTimebase(1, 25):
        raise ValueError("MP4 v1 requires a D018 timeline at 25 FPS.")
    edges = []
    for i, clip in enumerate(timeline.clips):
        media = clip.media
        audio_head = "raw" if media.audio.variant == "original" else "processed"
        edges.extend((InputEdge.artifact(f"image:{i}", f"scene:{media.scene_id}:image_selected",
                                        media.image.artifact_id, media.image.checksum),
                      InputEdge.artifact(f"audio:{i}", f"section:{media.section_id}:audio:{audio_head}",
                                        media.audio.artifact_id, media.audio.checksum)))
    if algorithm_version == "1":
        if any(c.media.image.provenance == "motion_master" for c in timeline.clips):
            raise ValueError("Historical render requests cannot contain motion masters.")
        settings = {"profile": LEGACY_PROFILE, "timeline": timeline.to_payload(), "captions": None}
    elif algorithm_version in ("2", "3"):
        profile, width, height = delivery_profile(timeline)
        settings = {"profile": PROFILE, "delivery_profile": profile, "width": width, "height": height,
                    "motion_policy": MOTION_POLICY_VERSION, "timeline": timeline.to_payload(), "captions": None}
        if algorithm_version == "3":
            if not isinstance(motion, MotionConfig):
                raise ValueError("Controlled motion requests require a frozen motion configuration.")
            settings.update(motion.to_payload())
    else:
        raise ValueError("Unsupported render request algorithm version.")
    if captions is not None:
        milliseconds = timeline.duration * 1000
        duration_ms = (2 * milliseconds.numerator + milliseconds.denominator) // (
            2 * milliseconds.denominator)
        if (not isinstance(captions, PublishedCaptionTrack)
                or captions.track.project_id != timeline.project_id
                or captions.track.timeline_id != timeline.id
                or captions.track.duration_ms != duration_ms):
            raise ValueError("Burn-in captions belong to another timeline or audio duration.")
        edges.extend((
            InputEdge.artifact("caption_track", f"timeline:{timeline.id}:captions",
                               captions.track_artifact_id, captions.track_checksum),
            InputEdge.artifact("caption_ass", f"timeline:{timeline.id}:captions:ass",
                               captions.ass_artifact_id, captions.ass_checksum),
        ))
        settings["captions"] = captions.to_payload()
    return RequestFingerprint.create(OPERATION, algorithm_version, inputs=edges,
                                     settings=settings,
                                     effective_identity=identity)


@dataclass(frozen=True)
class RenderedVideo:
    timeline_id: str
    checksum: str
    size_bytes: int
    frame_count: int
    video_duration: Fraction
    audio_duration: Fraction
    profile: str = "legacy-720p"
    width: int = 1280
    height: int = 720
    fps: int = 25
    video_codec: str = "h264"
    pixel_format: str = "yuv420p"
    audio_codec: str = "aac"
    sample_rate: int = 48000
    channels: int = 1

    def __post_init__(self):
        if (type(self.timeline_id) is not str or not self.timeline_id.startswith("timeline_")
                or type(self.checksum) is not str or len(self.checksum) != 64
                or any(c not in "0123456789abcdef" for c in self.checksum)
                or any(type(v) is not int or v <= 0 for v in (self.size_bytes, self.frame_count))
                or any(type(v) is not Fraction or v <= 0 for v in (self.video_duration, self.audio_duration))):
            raise ValueError("Rendered video requires measured and decoded media evidence.")
        if (not self.profile or any(type(v) is not int or v <= 0 for v in
                (self.width, self.height, self.fps, self.sample_rate, self.channels))):
            raise ValueError("Rendered video requires measured stream properties.")

    def to_payload(self):
        data = asdict(self)
        for name in ("video_duration", "audio_duration"):
            value = getattr(self, name)
            data[name] = [value.numerator, value.denominator]
        return {"version": 2, "fully_decoded": True, **data}
