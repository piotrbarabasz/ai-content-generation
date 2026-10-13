"""Versioned local production QA contracts; measurements and reviews stay separate."""
from dataclasses import asdict, dataclass, field
import math
from pathlib import Path
from typing import Protocol

from app.domain.dependencies import content_fingerprint

SCHEMA_VERSION = 1
ENGINE_VERSION = "production-qa-v1"
STATES = {"NOT_RUN", "RUNNING", "READY", "REVIEW_REQUIRED", "BLOCKED", "STALE", "CANCELED"}
OUTCOMES = {"PASS", "FAIL", "UNAVAILABLE", "NOT_REQUESTED", "SKIPPED", "CANCELED"}


def json_values(value):
    if value is None or type(value) in (str, bool, int):
        return
    if type(value) is float and math.isfinite(value):
        return
    if type(value) in (list, tuple):
        for item in value: json_values(item)
        return
    if type(value) is dict and all(type(k) is str for k in value):
        for item in value.values(): json_values(item)
        return
    raise ValueError("QA values must be finite JSON data.")


@dataclass(frozen=True, slots=True)
class QARunConfiguration:
    mode: str = "quick"
    target_lufs: float = -16.0
    true_peak: float = -1.0
    lufs_tolerance: float = 1.0
    inherit_delivery: bool = True
    silence_db: float = -40.0
    silence_seconds: float = 3.0
    black_seconds: float = .4
    black_pixel: float = .1
    freeze_seconds: float = 12.0
    short_shot: float = .8
    long_shot: float = 20.0
    hash_distance: int = 6
    max_frames: int = 36
    max_evidence: int = 12
    max_intervals: int = 300
    validator_timeout: float = 900.0
    caption_chars: int = 42
    caption_lines: int = 2
    caption_min_coverage: float = .9
    av_tolerance: float = .1
    evidence: bool = True
    semantic: bool = False

    def __post_init__(self):
        if self.mode not in {"quick", "deep"}: raise ValueError("Choose Quick or Deep QA.")
        for name in ("inherit_delivery", "evidence", "semantic"):
            if type(getattr(self, name)) is not bool: raise ValueError("Invalid QA boolean setting.")
        ranges = {"target_lufs": (-70, 0), "true_peak": (-20, 0), "lufs_tolerance": (.1, 15),
                  "silence_db": (-100, -1), "silence_seconds": (.1, 120), "black_seconds": (.04, 120),
                  "black_pixel": (0, 1), "freeze_seconds": (.1, 600), "short_shot": (.04, 30),
                  "long_shot": (1, 600), "validator_timeout": (1, 7200),
                  "caption_min_coverage": (0, 1), "av_tolerance": (.001, 5)}
        for name, (lo, hi) in ranges.items():
            value = getattr(self, name)
            if type(value) not in (int, float) or not math.isfinite(value) or not lo <= value <= hi:
                raise ValueError("Invalid QA threshold: " + name)
        for name, lo, hi in (("hash_distance", 0, 64), ("max_frames", 1, 120), ("max_evidence", 0, 24),
                             ("max_intervals", 1, 2000), ("caption_chars", 8, 200), ("caption_lines", 1, 5)):
            if type(getattr(self, name)) is not int or not lo <= getattr(self, name) <= hi:
                raise ValueError("Invalid QA sampling/readability limit: " + name)
        if self.short_shot >= self.long_shot: raise ValueError("Shot thresholds must be ordered.")

    def to_payload(self): return asdict(self)


@dataclass(frozen=True)
class QAContext:
    project_id: str
    source_id: str
    source_checksum: str
    size_bytes: int
    source_kind: str
    path: Path
    facts: dict = field(default_factory=dict)

    def __post_init__(self):
        if not self.project_id or not self.source_id or len(self.source_checksum) != 64 or any(c not in "0123456789abcdef" for c in self.source_checksum):
            raise ValueError("QA requires exact source identity/checksum.")
        if type(self.size_bytes) is not int or self.size_bytes < 0: raise ValueError("Invalid source size.")
        json_values(self.facts)

    def identity(self):
        return {"project_id": self.project_id, "source_id": self.source_id, "source_checksum": self.source_checksum,
                "size_bytes": self.size_bytes, "source_kind": self.source_kind, "mapping": self.facts}


@dataclass(frozen=True)
class QAFinding:
    rule_id: str
    severity: str
    category: str
    source_id: str
    source_checksum: str
    explanation: str
    action: str
    actual: object = None
    expected: object = None
    start: float | None = None
    end: float | None = None
    references: tuple[str, ...] = ()
    evidence: tuple[str, ...] = ()
    validator_version: str = "1"
    review_status: str = "unreviewed"

    def __post_init__(self):
        if self.severity not in {"BLOCKED", "REVIEW", "INFO"} or not all((self.rule_id, self.category, self.source_id, self.explanation, self.action)):
            raise ValueError("Invalid QA finding.")
        if self.category == "semantic" and self.severity != "REVIEW": raise ValueError("Semantic opinions must remain REVIEW.")
        if self.review_status != "unreviewed": raise ValueError("Review decisions cannot rewrite validator results.")
        if (self.start is None) != (self.end is None) or self.start is not None and not 0 <= self.start < self.end:
            raise ValueError("Invalid finding interval.")
        json_values(asdict(self))

    @property
    def id(self):
        value = asdict(self); value.pop("evidence")
        value["references"] = list(self.references)
        return "finding_" + content_fingerprint(value)

    def to_payload(self): return {"id": self.id, **asdict(self), "references": list(self.references), "evidence": list(self.evidence)}


class QAValidator(Protocol):
    id: str
    version: str
    def validate(self, context: QAContext, config: QARunConfiguration, measurements: dict) -> tuple[QAFinding, ...]: ...


@dataclass(frozen=True)
class QAReport:
    run_id: str
    input_fingerprint: str
    source: dict
    configuration: dict
    outcomes: tuple[dict, ...]
    measurements: dict
    findings: tuple[QAFinding, ...]
    canceled: bool = False
    schema_version: int = SCHEMA_VERSION
    engine_version: str = ENGINE_VERSION

    def __post_init__(self):
        if self.schema_version != SCHEMA_VERSION or self.engine_version != ENGINE_VERSION: raise ValueError("Unsupported QA report version.")
        if not self.run_id or len(self.input_fingerprint) != 64 or any(c not in "0123456789abcdef" for c in self.input_fingerprint):
            raise ValueError("Invalid QA run/fingerprint identity.")
        QAContext(self.source["project_id"], self.source["source_id"], self.source["source_checksum"], self.source["size_bytes"], self.source["source_kind"], Path(), self.source["mapping"])
        if type(self.canceled) is not bool: raise ValueError("Invalid QA cancellation flag.")
        if len({v["validator_id"] for v in self.outcomes}) != len(self.outcomes): raise ValueError("Duplicate validator outcomes.")
        QARunConfiguration(**self.configuration)
        if any(v.get("state") not in OUTCOMES or not v.get("validator_id") or not v.get("version") for v in self.outcomes):
            raise ValueError("Invalid validator outcomes.")
        if len({f.id for f in self.findings}) != len(self.findings): raise ValueError("Duplicate QA findings.")
        if any((f.source_id, f.source_checksum) != (self.source["source_id"], self.source["source_checksum"]) for f in self.findings):
            raise ValueError("Finding belongs to another source.")
        json_values(self.to_payload())

    def state(self, decisions=(), *, stale=False):
        if self.canceled: return "CANCELED"
        if stale or self.source.get("mapping", {}).get("stale"): return "STALE"
        if any(f.severity == "BLOCKED" for f in self.findings): return "BLOCKED"
        resolved = {d["finding_id"] for d in decisions if d["status"] in {"intentional", "resolved"}
                    and (d["source_id"], d["source_checksum"]) == (self.source["source_id"], self.source["source_checksum"])}
        if any(f.severity == "REVIEW" and f.id not in resolved for f in self.findings): return "REVIEW_REQUIRED"
        if any(v.get("mandatory") and v["state"] != "PASS" for v in self.outcomes): return "REVIEW_REQUIRED"
        return "READY"

    def to_payload(self):
        return {**asdict(self), "outcomes": list(self.outcomes), "findings": [f.to_payload() for f in self.findings]}

    @classmethod
    def from_payload(cls, value):
        data = dict(value); findings = []
        for raw in data.pop("findings"):
            row = dict(raw); identity = row.pop("id"); row["references"] = tuple(row["references"]); row["evidence"] = tuple(row["evidence"])
            finding = QAFinding(**row)
            if finding.id != identity: raise ValueError("Finding identity changed.")
            findings.append(finding)
        data["findings"] = tuple(findings); data["outcomes"] = tuple(data["outcomes"])
        return cls(**data)
