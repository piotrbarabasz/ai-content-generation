"""Measured, bounded D077 repair. Only provider calls leave the owner thread."""

import asyncio
from dataclasses import asdict, replace
from fractions import Fraction
import json
import math
import re
from types import SimpleNamespace

from app.domain.base import new_id
from app.domain.dependencies import canonical_json, content_fingerprint
from app.domain.duration_repair import (DurationRepairPolicy, DurationRepairProposal,
                                        RepairAdjustment, SectionMeasurement)
from app.domain.narrative_segment import SectionRevision
from app.domain.planned_script_schema import planned_script_group_schema, validate_planned_script_group
from app.domain.script import ScriptRevision
from app.domain.video_plan import VIDEO_FORMATS, VideoFormat
from app.storage.duration_repairs import ProjectDurationRepairs
from .automatic_workflow import AutomaticWorkflowCanceled
from .plan_duration import selected_narration


def _sentences(text):
    return [" ".join(re.findall(r"\w+", s.casefold())) for s in re.split(r"[.!?]+", text) if s.strip()]


class DurationRepairService:
    def __init__(self, scripts):
        self.scripts = scripts
        self.records = ProjectDurationRepairs(scripts.session.repository, scripts.artifacts.store)

    def measure(self, plan, audio, choice, variant="original"):
        self.scripts._selected_plan(plan)
        if plan.format != VideoFormat.STANDARD:
            raise ValueError("Automatic duration repair is available only for Standard plans.")
        script = self.scripts.session.active_script
        binding = self.scripts.artifacts.active_binding()
        expected = [self.scripts.section_id(plan.project_id, p.id) for g in plan.groups for p in g.sections]
        if (binding is None or binding.video_plan_revision_id != plan.id or binding.script_id != script.script_id
                or [s.section_id for s in script.sections] != expected):
            raise ValueError("Duration repair requires the complete ordered plan-bound script; review manual or missing sections.")
        by_id = {s.section_id: s for s in script.sections}
        measured = []
        for group in plan.groups:
            for planned in group.sections:
                section = by_id[self.scripts.section_id(plan.project_id, planned.id)]
                selected = selected_narration(audio, section, choice, variant)
                measured.append(SectionMeasurement(group.id, planned.id, section.section_id, section.id,
                    selected.artifact_id, selected.checksum, selected.frame_count, selected.sample_rate,
                    len(section.text.split()), planned.target_duration_seconds, planned.target_word_count))
        total = sum((m.seconds for m in measured), Fraction())
        spec = VIDEO_FORMATS[plan.format]
        return {"version": 1, "plan_id": plan.id, "plan_fingerprint": content_fingerprint(plan.to_payload()),
            "source_script_id": script.id, "voice": {"selection": audio.selection(choice), "variant": variant},
            "sections": [asdict(m) for m in measured], "actual_seconds": float(total),
            "target_seconds": plan.target_duration_seconds, "minimum_seconds": spec.min_duration_seconds,
            "maximum_seconds": spec.max_duration_seconds,
            "overall_words_per_minute": float(sum(m.words for m in measured) * 60 / total)}

    def _history(self, kind, plan, campaign=None):
        return self.records.history(kind, plan_id=plan.id, campaign_id=campaign)

    def latest(self, plan):
        _, campaign = self.records.policy(plan.id)
        values = self._history("proposal", plan, campaign) if campaign else ()
        return values[-1] if values else None

    def status(self, plan, state, message, **extra):
        _, campaign = self.records.policy(plan.id)
        return self.records.save("status", {"plan_id": plan.id, "campaign_id": campaign,
            "state": state, "message": message, **extra})

    def observe(self, plan, snapshot):
        """Assess a committed round only with newly verified complete PCM."""
        measurements = self._history("measurement", plan)
        if not measurements or measurements[-1]["snapshot"] != snapshot:
            _, campaign = self.records.policy(plan.id)
            self.records.save("measurement", {"plan_id": plan.id, "campaign_id": campaign, "snapshot": snapshot})
        latest = self.latest(plan)
        if latest is None:
            return
        proposal = DurationRepairProposal.from_payload(latest["proposal"])
        intents = [v for v in self._history("commit", plan, proposal.campaign_id) if v["proposal_id"] == proposal.id]
        assessed = [v for v in self._history("assessment", plan, proposal.campaign_id) if v["proposal_id"] == proposal.id]
        if not intents or assessed:
            return
        intent = intents[-1]
        if snapshot["source_script_id"] == proposal.source_script_id:
            return  # A crash before atomic selection; execute can recover the exact intent.
        if snapshot["source_script_id"] != intent["script"]["id"] or snapshot["voice"] != latest["snapshot"]["voice"]:
            raise ValueError("NEEDS REVIEW: script or voice changed during duration repair. Retained output was not reapplied.")
        actual = snapshot["actual_seconds"]
        error_before = abs(proposal.actual_seconds - proposal.target_seconds)
        improvement = error_before - abs(actual - proposal.target_seconds)
        passed = snapshot["minimum_seconds"] <= actual <= snapshot["maximum_seconds"]
        state = "PASS" if passed else "BLOCKED" if improvement >= max(2, error_before * .05) else "NEEDS REVIEW"
        self.records.save("assessment", {"plan_id": plan.id, "campaign_id": proposal.campaign_id,
            "proposal_id": proposal.id, "state": state, "snapshot": snapshot, "improvement_seconds": improvement})
        self.status(plan, state, f"Round {proposal.round_number}: measured {actual:.2f}s; target {proposal.target_seconds}s.",
                    proposal_id=proposal.id)
        if state == "NEEDS REVIEW":
            raise ValueError("NEEDS REVIEW: measured improvement was insufficient or reversed. Review text and voice before further repair.")

    def propose(self, plan, snapshot):
        policy, campaign = self.records.policy(plan.id)
        if campaign is None:
            campaign = self.records.configure(plan.id, policy)
        self.observe(plan, snapshot)
        if snapshot["minimum_seconds"] <= snapshot["actual_seconds"] <= snapshot["maximum_seconds"]:
            return None
        latest = self.latest(plan)
        if latest:
            proposal = DurationRepairProposal.from_payload(latest["proposal"])
            assessed = [v for v in self._history("assessment", plan, campaign) if v["proposal_id"] == proposal.id]
            if assessed and assessed[-1]["state"] == "NEEDS REVIEW":
                raise ValueError("NEEDS REVIEW: previous repair did not measurably improve duration.")
            if not assessed:
                commits = [v for v in self._history("commit", plan, campaign) if v["proposal_id"] == proposal.id]
                allowed = {proposal.source_script_id, *(v["script"]["id"] for v in commits)}
                if snapshot["source_script_id"] not in allowed:
                    raise ValueError("NEEDS REVIEW: retained repair proposal has stale script sources.")
                if snapshot["source_script_id"] == proposal.source_script_id:
                    if content_fingerprint(snapshot) != proposal.snapshot_fingerprint:
                        raise ValueError("NEEDS REVIEW: selected narration or voice changed after repair preview.")
                    return proposal
        requests = self._history("request", plan, campaign)
        round_number = len({r["proposal_id"] for r in requests}) + 1
        if round_number > policy.max_rounds or len(requests) >= policy.max_requests:
            self.status(plan, "BLOCKED", "Duration repair budget exhausted; review retained text and measured narration.")
            raise ValueError("Duration repair exhausted: maximum rounds/paid calls reached. Review sections manually; estimates cannot pass PLAN_DURATION.")
        delta = snapshot["target_seconds"] - snapshot["actual_seconds"]
        measurements = [SectionMeasurement(**v) for v in snapshot["sections"]]
        chapters = [g for g in plan.groups if g.kind == "chapter"]
        def rank(group):
            values = [m for m in measurements if m.group_id == group.id]
            gap = sum(m.planned_seconds - float(m.seconds) for m in values)
            relevance = sum(word in (group.title + " " + group.purpose).casefold()
                            for word in ("military", "politic", "govern", "geopolit", "consequence", "econom"))
            return (gap if delta > 0 else -gap, relevance, group.target_duration_seconds)
        selected = sorted(chapters, key=rank, reverse=True)[:policy.max_groups]
        selected_ids = {g.id for g in selected}
        values = [m for m in measurements if m.group_id in selected_ids]
        if not values:
            raise ValueError("NEEDS REVIEW: no substantive chapters can be safely adjusted.")
        # Water-fill measured corrections; no single section grows by more than
        # 80% or loses more than 40% in a round. Remaining deficit is auditable.
        allocations = {m.section_id: 0.0 for m in values}
        remaining = abs(delta)
        for _ in range(len(values) + 1):
            available = [m for m in values if allocations[m.section_id] < float(m.seconds) * (.8 if delta > 0 else .4) - .001]
            if not available or remaining < .001:
                break
            weights = {m.section_id: max(1., (m.planned_seconds - float(m.seconds)) * (1 if delta > 0 else -1))
                       + float(m.seconds) * .25 for m in available}
            total = sum(weights.values())
            spent = 0.
            for m in available:
                addition = min(remaining * weights[m.section_id] / total,
                               float(m.seconds) * (.8 if delta > 0 else .4) - allocations[m.section_id])
                allocations[m.section_id] += addition
                spent += addition
            remaining -= spent
        script = self.scripts.session.active_script
        adjustments = []
        for m in values:
            amount = allocations[m.section_id] * (1 if delta > 0 else -1)
            target_words = max(1, round(m.words * (float(m.seconds) + amount) / float(m.seconds)))
            if target_words == m.words:
                continue
            section = script.section(m.section_id)
            adjustments.append(RepairAdjustment(m.group_id, m.planned_section_id, m.section_id, m.revision_id,
                section.title, float(m.seconds), m.planned_seconds, amount, m.words, m.planned_words, target_words,
                ("Expand causal explanation, historical context and plausible consequences" if delta > 0 else
                 "Consolidate repeated exposition while retaining essential causal claims")
                + f" in a substantive chapter; calibrated at {m.words_per_minute:.1f} words/min. Preserve chronology and adjacent transitions."))
        proposal = DurationRepairProposal(new_id("duration_proposal"), plan.id, campaign, script.id,
            content_fingerprint(snapshot), round_number, snapshot["actual_seconds"], snapshot["target_seconds"],
            snapshot["minimum_seconds"], snapshot["maximum_seconds"], snapshot["overall_words_per_minute"], tuple(adjustments))
        self.records.save("proposal", {"plan_id": plan.id, "campaign_id": campaign,
            "proposal": proposal.to_payload(), "snapshot": snapshot, "provider_identity": self.scripts._identity()})
        self.status(plan, "BLOCKED", "Repair proposal ready for preview; only measured audio can pass.", proposal_id=proposal.id)
        return proposal

    def _validate_texts(self, plan, proposal, group, payload, retained):
        adjustments = [a for a in proposal.adjustments if a.group_id == group.id]
        texts = validate_planned_script_group(payload, tuple(a.planned_section_id for a in adjustments), version=2)
        planned = {p.id: p for p in group.sections}
        self.scripts._validate_narration(SimpleNamespace(
            sections=tuple(planned[a.planned_section_id] for a in adjustments), purpose=group.purpose), texts)
        script = self.scripts.session.active_script
        other = {s for section in script.sections if section.section_id not in {a.section_id for a in adjustments}
                 for s in _sentences(section.text) if len(s.split()) >= 6}
        other.update(s for text in retained for s in _sentences(text) if len(s.split()) >= 6)
        seen = set()
        for a, text in zip(adjustments, texts):
            words = text.split()
            if re.search(r"(?i)\b(?:film|narration|documentary)\s+(?:invites you|asks you|should|will explain)|\b(?:insert narration|in this section we)\b", text):
                raise ValueError("Editorial commentary or instructions are not final spoken chapter narration.")
            if len(text) > 20_000 or abs(len(words) - a.target_words) > max(2, a.target_words * .1):
                raise ValueError(f"{a.title}: narration must be within 10% of measured repair budget {a.target_words} words.")
            if (len(words) - a.original_words) * a.expected_delta_seconds <= 0:
                raise ValueError("Repair must change word count in the measured correction direction.")
            if len({w.casefold().strip('.,!?;:') for w in words}) < min(12, len(words) // 4):
                raise ValueError("Repetitive word padding is not meaningful narration.")
            sentences = _sentences(text)
            for sentence in sentences:
                if len(sentence.split()) >= 6 and (sentence in seen or sentence in other):
                    raise ValueError("Repeated narration within or across sections; add distinct substantive explanation.")
                seen.add(sentence)
            original = set(_sentences(script.section(a.section_id).text))
            novel_words = sum(len(s.split()) for s in sentences if s not in original)
            if a.expected_delta_seconds > 0 and novel_words < max(6, (a.target_words - a.original_words) * .5):
                raise ValueError("Expansion lacks substantive new spoken content.")
        return texts

    @staticmethod
    def _script(value):
        return ScriptRevision(**{**value, "sections": tuple(SectionRevision(**s) for s in value["sections"])})

    async def execute(self, plan, proposal, capture, *, canceled=lambda: False, progress=lambda message: None):
        policy, campaign = self.records.policy(plan.id)
        if not policy.enabled or campaign != proposal.campaign_id:
            raise ValueError("Enable automatic duration repair in Plan to authorize bounded paid LLM calls.")
        if proposal.round_number > policy.max_rounds or len({a.group_id for a in proposal.adjustments}) > policy.max_groups:
            raise ValueError("Retained proposal exceeds the configured duration repair limits; review before continuing.")
        if self.scripts.provider is None:
            raise ValueError("Configure a structured LLM provider for duration repair.")
        def check_cancel():
            if canceled():
                raise AutomaticWorkflowCanceled("DURATION_REPAIR")
        check_cancel()
        self.scripts._selected_plan(plan)
        latest = self.latest(plan)
        if latest is None or DurationRepairProposal.from_payload(latest["proposal"]) != proposal:
            raise ValueError("Duration repair proposal is no longer current.")
        if latest["provider_identity"] != self.scripts._identity():
            raise ValueError("NEEDS REVIEW: configured LLM identity changed after repair preview.")
        commits = [v for v in self._history("commit", plan, campaign) if v["proposal_id"] == proposal.id]
        if commits:
            intent = commits[-1]
            revision = self._script(intent["script"])
            current = self.scripts.session.active_script
            if current.id == proposal.source_script_id:
                if content_fingerprint(capture()) != proposal.snapshot_fingerprint:
                    raise ValueError("NEEDS REVIEW: repair source audio changed before selection recovery.")
                self.scripts.session.save_script(revision, expected_active_revision_id=current.id)
            elif current != revision:
                raise ValueError("NEEDS REVIEW: a manual revision superseded the retained duration repair.")
            return revision
        if content_fingerprint(capture()) != proposal.snapshot_fingerprint:
            raise ValueError("NEEDS REVIEW: narration or script changed after repair preview.")
        reset = getattr(self.scripts.provider, "reset_cancel", None)
        if callable(reset):
            reset()
        retries = getattr(getattr(self.scripts.provider, "settings", None), "max_retries", 0)
        if type(retries) is not int or not 0 <= retries <= 2:
            raise ValueError("Duration repair permits at most two configured transport retries per structured call.")
        accepted = {v["group_id"]: v for v in self._history("output", plan, campaign) if v["proposal_id"] == proposal.id}
        for group in plan.groups:
            adjustments = [a for a in proposal.adjustments if a.group_id == group.id]
            if not adjustments or group.id in accepted:
                continue
            prompt = {"task": "repair_measured_narration_duration", "policy": "measured-duration-budget-v1",
                "language": plan.language, "topic": plan.topic, "film_brief": plan.film_brief,
                "narrative": plan.narrative.to_payload() if plan.narrative else None,
                "outline": [self.scripts._group_payload(g) for g in plan.groups],
                "retained_script": [{"title": s.title, "text": s.text, "section_id": s.section_id} for s in self.scripts.session.active_script.sections],
                "group": {"id": group.id, "title": group.title, "purpose": group.purpose},
                "section_slots": {f"section_{i+1}": {"planned_section_id": a.planned_section_id, "title": a.title,
                    "target_word_count": a.target_words, "minimum_word_count": max(1, math.ceil(a.target_words - max(2, a.target_words * .1))),
                    "maximum_word_count": int(a.target_words + max(2, a.target_words * .1)),
                    "measured_seconds": a.actual_seconds, "requested_duration_change_seconds": a.expected_delta_seconds,
                    "justification": a.justification, "source_text": self.scripts.session.active_script.section(a.section_id).text}
                                  for i, a in enumerate(adjustments)},
                "instruction": "Return final spoken documentary narration only in the supplied fixed slots. "
                    "The section_slots word limits are authoritative repair budgets and OVERRIDE all original outline word counts. "
                    "Meet EVERY slot's minimum_word_count and maximum_word_count, aiming at target_word_count. "
                    "Count whitespace-separated words in each completed slot and revise internally before returning; do not omit or shorten a slot. "
                    "Preserve each section's premise, order and chronology. "
                    "Preserve historical facts; distinguish established facts from counterfactual assumptions and plausible consequences. "
                    "Add meaningful causal reasoning, historical context, military/political/governance or geopolitical consequences where relevant. "
                    "Use adjacent retained text for continuity; never repeat another section's contribution or introduce contradictions. "
                    "For reduction, consolidate exposition without losing essential facts. No filler, duplicate sentences, generic rhetorical questions, "
                    "headings, stage directions, instructions or new section identities. The immutable Video Plan is not being regenerated."}
            schema = planned_script_group_schema(len(adjustments))
            for field in schema["properties"]["sections"]["properties"].values():
                field["maxLength"] = 20_000
            while True:
                check_cancel()
                requests = self._history("request", plan, campaign)
                attempts = sum(r["proposal_id"] == proposal.id and r["group_id"] == group.id for r in requests)
                if len(requests) >= policy.max_requests or attempts >= 2:
                    self.status(plan, "NEEDS REVIEW", "Duration repair request/quality retry budget exhausted; review retained responses and text.",
                                proposal_id=proposal.id)
                    raise ValueError("NEEDS REVIEW: duration repair request/quality retry budget exhausted; retained script preserved.")
                request_id = new_id("duration_request")
                self.records.save("request", {"plan_id": plan.id, "campaign_id": campaign,
                    "proposal_id": proposal.id, "group_id": group.id, "request_id": request_id,
                    "attempt": attempts + 1, "transport_attempts_max": retries + 1,
                    "prompt_fingerprint": content_fingerprint(prompt), "prompt": prompt})
                message = f"Duration repair round {proposal.round_number}/{policy.max_rounds}: {group.title} (call {len(requests)+1}/{policy.max_requests})"
                self.status(plan, "BLOCKED", message, proposal_id=proposal.id)
                progress(message)
                try:
                    payload = await asyncio.to_thread(self.scripts.provider.generate_structured, canonical_json(prompt), schema)
                except Exception as exc:
                    self.status(plan, "BLOCKED", "LLM request failed; reservation retained for bounded Resume. " + str(exc)[:400], proposal_id=proposal.id)
                    check_cancel()
                    raise
                usage = getattr(self.scripts.provider, "last_usage", lambda: None)()
                self.records.save("response", {"plan_id": plan.id, "campaign_id": campaign,
                    "proposal_id": proposal.id, "request_id": request_id, "payload": payload, "usage": usage})
                # Reject stale completions, but retain their paid response for inspection.
                if self.scripts.session.active_script.id != proposal.source_script_id:
                    raise ValueError("NEEDS REVIEW: script changed during LLM request; response retained without selection.")
                try:
                    texts = self._validate_texts(plan, proposal, group, payload, [t for v in accepted.values() for t in v["texts"]])
                except ValueError as exc:
                    self.status(plan, "NEEDS REVIEW", str(exc), proposal_id=proposal.id)
                    prompt["correction"] = {"validation_error": str(exc),
                        "observed_word_counts": {key: len(text.split()) for key, text in payload.get("sections", {}).items()
                                                 if isinstance(text, str)} if isinstance(payload, dict) and isinstance(payload.get("sections"), dict) else {},
                        "instruction": "Return ALL corrected spoken slots. Check every minimum and maximum, not just the first failing slot. "
                                       "Add substantive historical/causal explanation where short; condense where long. Do not pad."}
                    check_cancel()
                    continue
                value = {"plan_id": plan.id, "campaign_id": campaign, "proposal_id": proposal.id,
                         "group_id": group.id, "texts": list(texts), "request_id": request_id}
                self.records.save("output", value)
                accepted[group.id] = value
                check_cancel()
                break
        check_cancel()
        if content_fingerprint(capture()) != proposal.snapshot_fingerprint:
            raise ValueError("NEEDS REVIEW: verified repair source snapshot changed; outputs retained without selection.")
        if latest["provider_identity"] != self.scripts._identity():
            raise ValueError("NEEDS REVIEW: LLM identity changed while repair output was being generated.")
        current = self.scripts.session.active_script
        revised = {}
        budgets = []
        for group in plan.groups:
            adjustments = [a for a in proposal.adjustments if a.group_id == group.id]
            if not adjustments:
                continue
            texts = self._validate_texts(plan, proposal, group,
                {"sections": {f"section_{i+1}": t for i, t in enumerate(accepted[group.id]["texts"])}},
                [t for gid, output in accepted.items() if gid != group.id for t in output["texts"]])
            for adjustment, text in zip(adjustments, texts):
                section = current.section(adjustment.section_id).revise(text=text)
                revised[section.section_id] = section
                budgets.append({"revision_id": section.id, "section_id": section.section_id,
                    "target_words": adjustment.target_words, "editorial_words": adjustment.editorial_words,
                    "budget_policy": "measured-duration-budget-v1"})
        revision = replace(current, id=new_id("script_revision"), parent_revision_id=current.id,
                           sections=tuple(revised.get(s.section_id, s) for s in current.sections))
        # Durable selection intent closes the crash window between artifact and DB
        # publication; replay uses the same immutable revision IDs, never new calls.
        self.records.save("commit", {"plan_id": plan.id, "campaign_id": campaign,
            "proposal_id": proposal.id, "script": json.loads(json.dumps(asdict(revision))), "budgets": budgets})
        check_cancel()
        self.scripts.session.save_script(revision, expected_active_revision_id=current.id)
        self.status(plan, "BLOCKED", "Text revised selectively; waiting for verified regenerated narration.", proposal_id=proposal.id)
        progress("Duration repair text saved; regenerating only changed narration.")
        return revision
