"""Append-only narration settings, approval, estimated-spend and preview history."""
from dataclasses import asdict
from datetime import datetime, timezone
from math import ceil

from app.domain.base import new_id
from app.domain.narration import NarrationSettings
from app.domain.dependencies import content_fingerprint
from app.storage.narrative_reviews import ProjectNarrativeRecords

# Published token rates; audio tokenization/duration assumptions are estimates.
PRICING = {"version": "openai-speech-2026-10-16-v1", "as_of": "2026-10-16", "expires": "2026-11-16",
           "text_per_million": .60, "audio_per_million": 12.,
           "source": "https://developers.openai.com/api/docs/pricing"}
_UNSET = object()


def estimate_chunk(text, settings):
    seconds = len(text.split()) / (2.2 * settings.speed)
    tokens_upper = len((text + settings.delivery).encode("utf-8"))
    # Deliberately broad duration and token-density assumptions, not a billing promise.
    return {"characters": len(text), "estimated_seconds": seconds,
            "low_usd": seconds * .5 * 12.5 * PRICING["audio_per_million"] / 1e6,
            "high_usd": max(.001, (tokens_upper * PRICING["text_per_million"] + seconds * 2 * 50 * PRICING["audio_per_million"]) / 1e6)}


class ProjectNarration:
    def __init__(self, session, store):
        self.session, self.store = session, store
        self.records = ProjectNarrativeRecords(session.repository, store)

    def history(self, kind):
        return tuple(self.records.read(m) for m in self.records.history("narration_" + kind))

    def save(self, kind, payload):
        value = {"version": 1, "id": new_id("narration_" + kind), **payload}
        self.records.save("narration_" + kind, value)
        return value

    def project(self):
        values = self.history("settings")
        values = [v for v in values if v["scope"] == "project"]
        return values[-1] if values else None

    def migrated(self):
        value = self.project()
        if value:
            return value["migrated"]
        return not any(m.metadata.get("section_audio") for m in self.store.list_artifacts())

    def settings(self, section_id=None):
        value = self.project()
        settings = NarrationSettings.from_payload(value["settings"]) if value else NarrationSettings()
        if section_id:
            values = [v for v in self.history("settings") if v["scope"] == section_id]
            if values:
                settings = settings.overridden(values[-1]["overrides"])
        return settings

    def save_project(self, settings, *, migrate=False, project_budget=_UNSET):
        old = self.project()
        if project_budget is not _UNSET and project_budget is not None and (type(project_budget) not in (int, float) or not 0 < project_budget <= 1000):
            raise ValueError("Project estimated-spend budget must be positive and at most $1000.")
        return self.save("settings", {"scope": "project", "parent_id": old["id"] if old else None,
            "settings": settings.to_payload(), "migrated": self.migrated() or migrate,
            "project_budget": project_budget if project_budget is not _UNSET else old.get("project_budget") if old else None})

    def save_overrides(self, section_ids, overrides):
        active = {s.section_id for s in self.session.active_script.sections}
        if not set(section_ids) <= active:
            raise ValueError("Narration override refers to sections outside the active script.")
        self.settings().overridden(overrides)
        for section_id in section_ids:
            old = [v for v in self.history("settings") if v["scope"] == section_id]
            self.save("settings", {"scope": section_id, "parent_id": old[-1]["id"] if old else None, "overrides": overrides})

    def approve(self, scopes, budget, *, max_attempts=2):
        import math
        if type(budget) not in (int, float) or not math.isfinite(budget) or not 0 < budget <= 1000:
            raise ValueError("Approve an explicit finite operation budget between $0 and $1000.")
        if type(max_attempts) is not int or not 1 <= max_attempts <= 2:
            raise ValueError("At most two paid attempts per chunk may be approved.")
        return self.save("approval", {"scopes": list(scopes), "budget_usd": budget,
            "max_attempts": max_attempts, "pricing": PRICING})

    def reserve(self, scope, chunk_key, estimate):
        if datetime.now(timezone.utc).date().isoformat() >= PRICING["expires"]:
            raise ValueError("Speech pricing estimate expired; update the versioned pricing configuration before paid generation.")
        approvals = [v for v in self.history("approval") if scope in v["scopes"]]
        if not approvals:
            raise ValueError("Paid narration requires a matching operation estimate and explicit budget approval.")
        approval = approvals[-1]
        if approval["pricing"] != PRICING:
            raise ValueError("Speech pricing changed; review a fresh estimate and approve the remaining operation.")
        reservations = self.history("reservation")
        attempts = [r for r in reservations if r["scope"] == scope and r["chunk_key"] == chunk_key]
        # A new approval cannot erase already attempted calls for the same immutable input.
        if len(attempts) >= approval["max_attempts"]:
            raise ValueError("Chunk paid-attempt budget exhausted. Review uncertain billing/retained responses.")
        used = sum(r["reserved_usd"] for r in reservations if r["approval_id"] == approval["id"])
        amount = estimate["high_usd"]
        project = self.project()
        cap = project.get("project_budget") if project else None
        if used + amount > approval["budget_usd"] + 1e-12 or cap is not None and sum(r["reserved_usd"] for r in reservations) + amount > cap + 1e-12:
            raise ValueError("Approved estimated-spend budget exhausted; no request dispatched.")
        return self.save("reservation", {"approval_id": approval["id"], "scope": scope, "chunk_key": chunk_key,
            "attempt": len(attempts) + 1, "reserved_usd": amount, "pricing_version": PRICING["version"]})
