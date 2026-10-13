"""Measured narration readiness shared by Automatic and read-only diagnostics."""

from dataclasses import dataclass
from fractions import Fraction

from app.domain.section_audio import SectionAudio
from app.domain.video_plan import VIDEO_FORMATS


def selected_narration(services, section, choice, variant):
    """Use playback's PCM/checksum/voice validation, then resolve its measurements."""
    if services is None:
        raise ValueError("Audio services are not configured.")
    try:
        playback = services.playback(section, variant, choice)
        if playback.stale:
            raise ValueError("Selected narration is stale for the current section revision or voice.")
        suffix = "raw" if variant == "original" else "processed"
        artifact_id = services.index.selected().get(f"section:{section.section_id}:audio:{suffix}")
        manifest = next(item for item in services.index.manifests() if item.artifact_id == artifact_id)
        audio = SectionAudio.from_manifest(manifest)
        if (audio.section_id, audio.revision_id) != (section.section_id, section.id):
            raise ValueError("Selected narration belongs to another section or an older section revision.")
        return audio
    except (KeyError, StopIteration, TypeError) as exc:
        raise ValueError("Selected narration is missing or has invalid measurement metadata.") from exc


def _seconds(value):
    return f"{float(value):.6f}".rstrip("0").rstrip(".")


def _clock(value):
    minutes = int(value // 60)
    seconds = _seconds(value - minutes * 60)
    if value - minutes * 60 < 10:
        seconds = "0" + seconds
    return f"{minutes}:{seconds}"


@dataclass(frozen=True, slots=True)
class PlanDurationResult:
    target: int
    minimum: int
    maximum: int
    actual: Fraction | None
    pending_reason: str | None = None

    @property
    def state(self):
        if self.actual is None:
            return "PENDING"
        return "OK" if self.minimum <= self.actual <= self.maximum else "BLOCKED"

    @property
    def target_difference(self):
        return None if self.actual is None else self.actual - self.target

    @property
    def boundary_difference(self):
        if self.actual is None:
            return None
        boundary = min((self.minimum, self.maximum), key=lambda value: abs(self.actual - value))
        return self.actual - boundary

    @property
    def summary(self):
        text = (f"PLAN_DURATION {self.state}\nTarget: {self.target}s ({_clock(self.target)})\n"
                f"Allowed: {self.minimum}–{self.maximum}s ({_clock(self.minimum)}–{_clock(self.maximum)})")
        if self.actual is None:
            return text + f"\nMeasured narration: pending\n{self.pending_reason or 'Not all current narration artifacts are available.'}"
        delta = self.target_difference
        text += (f"\nMeasured narration: {_seconds(self.actual)}s ({_clock(self.actual)})"
                 f"\nDifference to target: {'+' if delta > 0 else ''}{_seconds(delta)}s")
        boundary = self.boundary_difference
        text += f"\nDifference to nearest allowed boundary: {'+' if boundary > 0 else ''}{_seconds(boundary)}s"
        if self.actual < self.minimum:
            text += f"\nMinimum additional narration needed: {_seconds(self.minimum - self.actual)}s"
        elif self.actual > self.maximum:
            text += f"\nMinimum narration reduction needed: {_seconds(self.actual - self.maximum)}s"
        else:
            text += "\nNarration is within the selected format profile."
        if self.state == "BLOCKED":
            text += "\nReview or adjust script sections, regenerate affected narration, then click Run / Resume."
        return text


def plan_duration_result(plan, measurements, section_count, *, pending_reason=None):
    spec = VIDEO_FORMATS[plan.format]
    actual = (sum((Fraction(audio.frame_count, audio.sample_rate) for audio in measurements), Fraction())
              if section_count and len(measurements) == section_count and pending_reason is None else None)
    return PlanDurationResult(plan.target_duration_seconds, spec.min_duration_seconds,
                              spec.max_duration_seconds, actual, pending_reason)
