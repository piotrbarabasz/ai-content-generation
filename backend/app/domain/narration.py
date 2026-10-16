"""D078 versioned creative settings, separate from scripts and post-process tempo."""
from dataclasses import asdict, dataclass, replace
import math

MODELS = ("gpt-4o-mini-tts", "gpt-4o-mini-tts-2025-12-15", "gpt-4o-mini-tts-2025-03-20")
VOICES = ("cedar", "marin", "onyx", "alloy", "ash", "ballad", "coral", "echo", "fable", "nova", "sage", "shimmer", "verse")
STYLES = {
    "Documentary": "Deliver clear, factual documentary narration with restrained emphasis.",
    "Cinematic": "Use cinematic storytelling with controlled intensity and natural phrasing.",
    "Historical": "Narrate history thoughtfully, pronouncing names clearly and distinguishing uncertainty.",
    "Calm": "Speak calmly and reassuringly with gentle emphasis.",
    "Dramatic": "Use dramatic emphasis while keeping every word intelligible.",
    "Neutral": "Use a neutral, clear speaking voice.",
}
PACING = {"Natural": "Use natural sentence pauses.", "Measured": "Use measured, unhurried phrasing.",
          "Energetic": "Use lively, energetic phrasing without rushing words.",
          "Slow and deliberate": "Use deliberate phrasing and thoughtful pauses."}
PRESETS = {"None": "", "Cinematic historical": "Use cinematic historical documentary delivery. "
           "Keep facts measured, counterfactual claims tentative, and transitions natural.",
           "Clear documentary": "Prioritize clarity and complete sentences over theatrical delivery."}


@dataclass(frozen=True)
class NarrationSettings:
    model: str = "gpt-4o-mini-tts"
    voice: str = "cedar"
    speed: float = 1.0
    style: str = "Documentary"
    preset: str = "None"
    instructions: str = ""
    pacing: str = "Natural"

    def __post_init__(self):
        if self.model not in MODELS or self.voice not in VOICES:
            raise ValueError("Unsupported speech model or voice; no model fallback is permitted.")
        if type(self.speed) not in (float, int) or not math.isfinite(self.speed) or not .25 <= self.speed <= 4:
            raise ValueError("Speech speed must be within the supported 0.25–4.00 range.")
        if self.style not in STYLES or self.pacing not in PACING or self.preset not in PRESETS:
            raise ValueError("Unsupported narration style, preset or pacing.")
        if type(self.instructions) is not str or len(self.instructions.encode("utf-8")) > 600:
            raise ValueError("Custom delivery instructions must not exceed 600 UTF-8 bytes.")

    @property
    def delivery(self):
        return " ".join(filter(None, (STYLES[self.style], PRESETS[self.preset], PACING[self.pacing], self.instructions)))

    def to_payload(self):
        return {"version": 1, **asdict(self)}

    @classmethod
    def from_payload(cls, value):
        if type(value) is not dict or set(value) != {"version", *cls.__dataclass_fields__} or type(value["version"]) is not int or value["version"] != 1:
            raise ValueError("Unsupported narration settings revision.")
        return cls(**{k: v for k, v in value.items() if k != "version"})

    def overridden(self, values):
        if type(values) is not dict or set(values) - {"voice", "speed", "instructions", "pacing", "style", "preset"}:
            raise ValueError("Section overrides support voice, speed, instructions, pacing, style and preset only.")
        return replace(self, **values)
