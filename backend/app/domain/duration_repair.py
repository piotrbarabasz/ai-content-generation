"""D077 measured repair contracts; independent of editorial plan budgets."""

from dataclasses import asdict, dataclass
from fractions import Fraction
import math


@dataclass(frozen=True)
class DurationRepairPolicy:
    enabled: bool = False
    max_rounds: int = 2
    max_groups: int = 3
    max_requests: int = 12

    def __post_init__(self):
        if type(self.enabled) is not bool:
            raise ValueError("Duration repair requires an explicit boolean opt-in.")
        for name, maximum in (("max_rounds", 2), ("max_groups", 3), ("max_requests", 12)):
            if type(getattr(self, name)) is not int or not 1 <= getattr(self, name) <= maximum:
                raise ValueError("Invalid bounded duration repair policy: " + name)

    def to_payload(self):
        return {"version": 1, **asdict(self)}

    @classmethod
    def from_payload(cls, value):
        if (type(value) is not dict or set(value) != {"version", *cls.__dataclass_fields__}
                or type(value["version"]) is not int or value["version"] != 1):
            raise ValueError("Unsupported duration repair policy.")
        return cls(**{k: v for k, v in value.items() if k != "version"})


@dataclass(frozen=True)
class SectionMeasurement:
    group_id: str
    planned_section_id: str
    section_id: str
    revision_id: str
    artifact_id: str
    checksum: str
    frame_count: int
    sample_rate: int
    words: int
    planned_seconds: int
    planned_words: int

    def __post_init__(self):
        for name in ("group_id", "planned_section_id", "section_id", "revision_id", "artifact_id", "checksum"):
            if type(getattr(self, name)) is not str or not getattr(self, name).strip():
                raise ValueError("Missing measured narration identity: " + name)
        for name in ("frame_count", "sample_rate", "words", "planned_seconds", "planned_words"):
            if type(getattr(self, name)) is not int or getattr(self, name) <= 0:
                raise ValueError("Invalid measured narration value: " + name)

    @property
    def seconds(self):
        return Fraction(self.frame_count, self.sample_rate)

    @property
    def words_per_minute(self):
        return float(self.words * 60 / self.seconds)


@dataclass(frozen=True)
class RepairAdjustment:
    group_id: str
    planned_section_id: str
    section_id: str
    revision_id: str
    title: str
    actual_seconds: float
    planned_seconds: int
    expected_delta_seconds: float
    original_words: int
    editorial_words: int
    target_words: int
    justification: str

    def __post_init__(self):
        for name in ("group_id", "planned_section_id", "section_id", "revision_id", "title", "justification"):
            if type(getattr(self, name)) is not str or not getattr(self, name).strip():
                raise ValueError("Missing repair adjustment identity.")
        for name in ("actual_seconds", "expected_delta_seconds"):
            if type(getattr(self, name)) not in (int, float) or not math.isfinite(getattr(self, name)):
                raise ValueError("Non-finite repair duration.")
        if self.actual_seconds <= 0 or self.expected_delta_seconds == 0:
            raise ValueError("Repair requires measured positive narration and a correction.")
        if any(type(getattr(self, name)) is not int or getattr(self, name) <= 0
               for name in ("planned_seconds", "original_words", "editorial_words", "target_words")):
            raise ValueError("Invalid versioned repair word budget.")
        if ((self.target_words - self.original_words) * self.expected_delta_seconds <= 0
                or not -.400001 <= self.expected_delta_seconds / self.actual_seconds <= .800001):
            raise ValueError("Repair budget direction or per-round adjustment exceeds its measured bounds.")


@dataclass(frozen=True)
class DurationRepairProposal:
    id: str
    plan_id: str
    campaign_id: str
    source_script_id: str
    snapshot_fingerprint: str
    round_number: int
    actual_seconds: float
    target_seconds: int
    minimum_seconds: int
    maximum_seconds: int
    overall_words_per_minute: float
    adjustments: tuple[RepairAdjustment, ...]

    def __post_init__(self):
        for name in ("id", "plan_id", "campaign_id", "source_script_id", "snapshot_fingerprint"):
            if type(getattr(self, name)) is not str or not getattr(self, name).strip():
                raise ValueError("Missing duration repair source identity.")
        if type(self.round_number) is not int or not 1 <= self.round_number <= 2:
            raise ValueError("Invalid duration repair round.")
        if (any(type(getattr(self, name)) not in (int, float) for name in ("actual_seconds", "overall_words_per_minute"))
                or any(type(getattr(self, name)) is not int for name in ("minimum_seconds", "target_seconds", "maximum_seconds"))):
            raise ValueError("Invalid duration repair number types.")
        if not (0 < self.actual_seconds and math.isfinite(self.actual_seconds)
                and 0 < self.overall_words_per_minute and math.isfinite(self.overall_words_per_minute)
                and 540 <= self.minimum_seconds <= self.target_seconds <= self.maximum_seconds <= 660):
            raise ValueError("Invalid Standard duration repair bounds.")
        object.__setattr__(self, "adjustments", tuple(self.adjustments))
        if (not self.adjustments or any(type(a) is not RepairAdjustment for a in self.adjustments)
                or len({a.group_id for a in self.adjustments}) > 3
                or len({a.section_id for a in self.adjustments}) != len(self.adjustments)):
            raise ValueError("Invalid selective duration repair adjustments.")

    def to_payload(self):
        return {"version": 1, "budget_policy": "measured-duration-budget-v1", **asdict(self),
                "adjustments": [asdict(a) for a in self.adjustments]}

    @classmethod
    def from_payload(cls, value):
        if (type(value) is not dict or set(value) != {"version", "budget_policy", *cls.__dataclass_fields__}
                or type(value["version"]) is not int or value["version"] != 1 or value["budget_policy"] != "measured-duration-budget-v1"):
            raise ValueError("Unsupported duration repair proposal.")
        fields = {k: v for k, v in value.items() if k in cls.__dataclass_fields__}
        fields["adjustments"] = tuple(RepairAdjustment(**a) for a in fields["adjustments"])
        return cls(**fields)
