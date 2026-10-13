"""Versioned editorial delivery choices; historical render contracts are untouched."""

from dataclasses import asdict, dataclass
import math
from types import MappingProxyType


@dataclass(frozen=True, slots=True)
class DeliveryPreset:
    id: str
    label: str
    reencode: bool
    video_codec: str = "libx264"
    encoder_preset: str = "medium"
    default_crf: int = 18
    pixel_format: str = "yuv420p"
    video_profile: str = "high"
    audio_codec: str = "aac"
    audio_profile: str = "aac_low"
    sample_rate: int = 48000
    mono_bitrate: int = 128000
    stereo_bitrate: int = 320000
    faststart: bool = True


DELIVERY_PRESETS = MappingProxyType({
    "source": DeliveryPreset("source", "Source / Original", False),
    "youtube_hq": DeliveryPreset("youtube_hq", "YouTube High Quality", True),
})


@dataclass(frozen=True, slots=True)
class DeliveryConfig:
    source: str = "original"
    preset: str = "youtube_hq"
    normalize: bool = True
    target_lufs: float = -16.0
    target_peak: float = -2.0
    target_lra: float = 11.0
    crf: int = 18

    def __post_init__(self):
        if (self.source not in {"original", "branded"} or self.preset not in DELIVERY_PRESETS
                or type(self.normalize) is not bool or type(self.crf) is not int or not 0 <= self.crf <= 51):
            raise ValueError("Invalid delivery source, preset or CRF.")
        for field, low, high in (("target_lufs", -30, -10), ("target_peak", -9, -1), ("target_lra", 1, 50)):
            value = getattr(self, field)
            if type(value) not in (int, float) or not math.isfinite(value) or not low <= value <= high:
                raise ValueError(f"Invalid delivery {field}; expected {low} to {high}.")

    def to_payload(self):
        return {"version": 1, **asdict(self)}

    @classmethod
    def from_payload(cls, payload):
        if type(payload) is not dict or type(payload.get("version")) is not int or payload["version"] != 1 or set(payload) != set(cls().to_payload()):
            raise ValueError("Invalid retained delivery preferences.")
        return cls(**{k: v for k, v in payload.items() if k != "version"})


def delivery_status(before, after, config, normalization_applied):
    reasons = []
    if before["integrated_lufs"] is None or before["integrated_lufs"] < -40:
        reasons.append("Silent or near-silent source: normalization skipped to avoid aggressive noise amplification.")
    if after["integrated_lufs"] is None or after["true_peak_dbtp"] is None:
        reasons.append("Output has no finite loudness/peak measurement; it cannot be treated as valid narration.")
    else:
        if after["true_peak_dbtp"] > -1.0:
            reasons.append("AAC output exceeds the -1.0 dBTP true-peak safety limit.")
        if config.normalize and (not normalization_applied or abs(after["integrated_lufs"] - config.target_lufs) > 1):
            reasons.append("Output integrated loudness is outside the ±1 LU target tolerance or normalization was unavailable.")
    return ("REVIEW" if reasons else "PASS"), reasons
