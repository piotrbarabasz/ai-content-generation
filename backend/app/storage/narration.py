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


def attempt_evidence(history, scope, key):
    attempts = [r for r in history('reservation') if (r['scope'], r['chunk_key']) == (scope, key)]
    usage = history('usage')
    values = []
    for r in attempts:
        linked = [u for u in usage if u.get('reservation_id') == r['id']]
        values.append({**r, 'outcomes': linked, 'billing': 'UNKNOWN',
                       'receipt': ('verified audio recorded' if any(u['state'] == 'received' for u in linked) else
                           'complete response received; not verified' if any(u.get('diagnostics', {}).get('response_complete') for u in linked) else
                           'partial bytes received; not verified' if any(u.get('diagnostics', {}).get('received_bytes', 0) for u in linked) else 'not established')})
    legacy = [u for u in usage if (u.get('scope'), u.get('chunk_key')) == (scope, key) and not u.get('reservation_id')]
    grants = [g for g in history('exceptional_retry') if (g['scope'], g['chunk_key']) == (scope, key)]
    grant = grants[-1] if grants else None
    additional = bool(grant and grant['prior_reservation_ids'] == [r['id'] for r in attempts])
    return values, legacy, grant, additional



class ProjectNarration:
    def __init__(self, session, store):
        self.session, self.store = session, store
        self.records = ProjectNarrativeRecords(session.repository, store)

    def history(self, kind, *, scope=None, section_id=None):
        filters = {k: v for k, v in {"scope": scope, "section_id": section_id}.items() if v is not None}
        values = (self.records.read(m) for m in self.records.history("narration_" + kind, metadata=filters))
        # Historical records may lack scope headers; validate/filter their payloads.
        return tuple(v for v in values if all(v[k] == expected for k, expected in filters.items()))

    def save(self, kind, payload):
        value = {"version": 1, "id": new_id("narration_" + kind), **payload}
        self.records.save("narration_" + kind, value)
        return value

    def project(self):
        values = self.history("settings", scope="project")
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
            values = self.history("settings", scope=section_id)
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
            old = self.history("settings", scope=section_id)
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
        _, _, grant, additional = attempt_evidence(self.history, scope, chunk_key)
        exceptional = len(attempts) >= approval["max_attempts"]
        if exceptional and (not additional or len(attempts) < 2 or grant["pricing"] != PRICING):
            raise ValueError("Chunk paid-attempt budget exhausted. Review uncertain billing/retained responses.")
        if exceptional and estimate["high_usd"] > grant["budget_usd"] + 1e-12:
            raise ValueError("Exceptional attempt estimated-spend ceiling exhausted; no request dispatched.")
        used = sum(r["reserved_usd"] for r in reservations if r["approval_id"] == approval["id"])
        amount = estimate["high_usd"]
        project = self.project()
        cap = project.get("project_budget") if project else None
        if used + amount > approval["budget_usd"] + 1e-12 or cap is not None and sum(r["reserved_usd"] for r in reservations) + amount > cap + 1e-12:
            raise ValueError("Approved estimated-spend budget exhausted; no request dispatched.")
        return self.save("reservation", {"approval_id": approval["id"], "scope": scope, "chunk_key": chunk_key,
            "attempt": len(attempts) + 1, "reserved_usd": amount, "pricing_version": PRICING["version"],
            "exceptional_retry_id": grant["id"] if exceptional else None})

    def authorize_exceptional_retry(self, scope, chunk_key, *, operator, reason, budget, evidence_fingerprint, acknowledge):
        import math
        if (not isinstance(operator, str) or not operator.strip() or len(operator) > 100
                or not isinstance(reason, str) or not reason.strip() or len(reason) > 500 or acknowledge is not True):
            raise ValueError("Operator, review reason and explicit uncertain-billing acknowledgement are required.")
        if type(budget) not in (int, float) or not math.isfinite(budget) or not 0 < budget <= 1000:
            raise ValueError("An explicit finite exceptional-attempt estimate ceiling is required.")
        attempts, _, grant, _ = attempt_evidence(self.history, scope, chunk_key)
        if len(attempts) != 2 or grant is not None:
            raise ValueError("Only one reviewed additional attempt is allowed after the original two reservations.")
        if datetime.now(timezone.utc).date().isoformat() >= PRICING["expires"]:
            raise ValueError("Speech pricing expired; update before authorizing another attempt.")
        cap = (self.project() or {}).get('project_budget')
        if cap is not None and sum(r['reserved_usd'] for r in self.history('reservation')) + budget > cap + 1e-12:
            raise ValueError("Project spending ceiling prevents this exceptional attempt. Historical reservations remain counted.")
        return self.save('exceptional_retry', {'scope': scope, 'chunk_key': chunk_key,
            'operator': operator.strip(), 'reason': reason.strip(), 'acknowledge_uncertain_billing': True,
            'prior_reservation_ids': [r['id'] for r in attempts], 'additional_attempts': 1,
            'budget_usd': budget, 'pricing': PRICING, 'evidence_fingerprint': evidence_fingerprint})
