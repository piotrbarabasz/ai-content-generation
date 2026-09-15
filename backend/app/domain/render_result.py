"""Desktop render contract, distinct from the legacy renderer's reference dictionary."""

from dataclasses import asdict, dataclass
from fractions import Fraction

from .dependencies import InputEdge, RequestFingerprint
from .timeline import OutputTimebase, TimelineRevision


OPERATION = "timeline.render"
PROFILE = "static-mp4-720p25-v1"


def render_request(timeline, identity):
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
    return RequestFingerprint.create(OPERATION, "1", inputs=edges,
                                     settings={"profile": PROFILE, "timeline": timeline.to_payload()},
                                     effective_identity=identity)


@dataclass(frozen=True)
class RenderedVideo:
    timeline_id: str
    checksum: str
    size_bytes: int
    frame_count: int
    video_duration: Fraction
    audio_duration: Fraction

    def __post_init__(self):
        if (type(self.timeline_id) is not str or not self.timeline_id.startswith("timeline_")
                or type(self.checksum) is not str or len(self.checksum) != 64
                or any(c not in "0123456789abcdef" for c in self.checksum)
                or any(type(v) is not int or v <= 0 for v in (self.size_bytes, self.frame_count))
                or any(type(v) is not Fraction or v <= 0 for v in (self.video_duration, self.audio_duration))):
            raise ValueError("Rendered video requires measured and decoded media evidence.")

    def to_payload(self):
        data = asdict(self)
        for name in ("video_duration", "audio_duration"):
            value = getattr(self, name)
            data[name] = [value.numerator, value.denominator]
        return {"version": 1, "profile": PROFILE, "width": 1280, "height": 720,
                "fps": 25, "video_codec": "h264", "pixel_format": "yuv420p",
                "audio_codec": "aac", "sample_rate": 48000, "channels": 1,
                "fully_decoded": True, **data}
