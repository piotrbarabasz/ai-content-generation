"""Optional narrative review. Literary judgments remain REVIEW, never technical errors."""
from app.domain.dependencies import content_fingerprint, canonical_json
from app.domain.video_plan import VideoFormat, WORDS_PER_SECOND, validate_standard_order
from app.storage.narrative_reviews import ProjectNarrativeRecords


ISSUE_TYPES = ("promise_resolution", "topic_introduction", "chapter_progression", "repetition",
               "abrupt_transition", "contradiction", "undefined_reference", "unanswered_question", "weak_ending")


def editorial_schema():
    text = {"type": "string", "minLength": 1}
    fields = {"issue_type": {"type": "string", "enum": list(ISSUE_TYPES)},
        "severity": {"type": "string", "enum": ["REVIEW"]}, "group_id": text,
        "section_id": text, "revision_id": text, "supporting_excerpt": text,
        "explanation": text, "suggested_correction": text}
    return {"$id": "urn:aics:narrative-review:v1", "type": "object", "additionalProperties": False,
        "required": ["findings"], "properties": {"findings": {"type": "array", "maxItems": 40,
            "items": {"type": "object", "additionalProperties": False,
                     "required": list(fields), "properties": fields}}}}


class NarrativeContinuityService:
    def __init__(self, script_service):
        self.scripts = script_service
        self.records = ProjectNarrativeRecords(script_service.session.repository, script_service.artifacts.store)

    def snapshot(self, plan):
        self.scripts._selected_plan(plan)
        script = self.scripts.session.active_script
        binding = self.scripts.artifacts.active_binding()
        if binding is None or binding.video_plan_revision_id != plan.id or binding.script_id != script.script_id:
            raise ValueError("Review requires the current plan-script binding.")
        return {"plan_id": plan.id, "plan_fingerprint": content_fingerprint(plan.to_payload()),
            "script_id": script.id, "section_revisions": [s.id for s in script.sections]}

    def group_status(self, plan, group):
        script = self.scripts.session.active_script
        ids = {self.scripts.section_id(plan.project_id, s.id) for s in group.sections}
        sections = [s for s in script.sections if s.section_id in ids]
        current = [s.id for s in sections]
        for m in reversed(self.records.history("approval")):
            approval = self.records.read(m)
            if approval["plan_id"] == plan.id and approval["group_id"] == group.id and approval["section_revisions"] == current:
                status = "APPROVED"
                break
        else:
            status = "REVIEW" if sections else "PENDING"
        if plan.format is VideoFormat.STANDARD and sections:
            sources = self.scripts.narrative_context(plan, group)["source_revisions"]
            for m in reversed(self.records.history("generation")):
                receipt = self.records.read(m)
                if receipt["plan_id"] == plan.id and receipt["group_id"] == group.id and receipt["section_revisions"] == current:
                    if receipt["source_revisions"] != sources:
                        return status + " / CONTEXT STALE"
                    break
            else:
                return status + " / CONTEXT UNVERIFIED"
        return status

    def deterministic_findings(self, plan):
        findings = []
        from app.storage.duration_repairs import ProjectDurationRepairs
        repaired_budgets = {b["revision_id"]: b["target_words"]
            for record in ProjectDurationRepairs(self.scripts.session.repository,
                self.scripts.artifacts.store).history("commit", plan_id=plan.id) for b in record["budgets"]}
        def add(issue, explanation, group=None, section=None, severity="BLOCKED"):
            findings.append({"issue_type": issue, "severity": severity,
                "group_id": group.id if group else None, "section_id": section.section_id if section else None,
                "revision_id": section.id if section else None, "supporting_excerpt": "",
                "explanation": explanation, "suggested_correction": "Inspect the referenced group and selectively edit or regenerate it."})
        for finding in self.scripts.narration_findings(plan):
            findings.append({"issue_type": "editorial_leakage", "severity": "BLOCKED",
                "group_id": finding["group_id"], "section_id": finding["section_id"],
                "revision_id": finding["revision_id"], "supporting_excerpt": "",
                "explanation": f"{finding['group']} / {finding['section']}: {finding['reason']}",
                "suggested_correction": finding["action"]})
        if plan.format is VideoFormat.STANDARD:
            try:
                validate_standard_order(plan.groups)
            except ValueError as exc:
                add("structure", str(exc), severity="BLOCKED" if plan.narrative else "REVIEW")
        script = self.scripts.session.active_script
        by_id = {s.section_id: s for s in script.sections}
        expected = [self.scripts.section_id(plan.project_id, p.id) for g in plan.groups for p in g.sections]
        if [s.section_id for s in script.sections if s.section_id in expected] != [i for i in expected if i in by_id]:
            add("section_order", "Retained script section order differs from the selected outline.")
        if any(s.section_id not in expected for s in script.sections):
            add("extra_sections", "Manual sections are outside the planned duration budget.", severity="REVIEW")
        if sum(g.target_duration_seconds for g in plan.groups) != plan.target_duration_seconds:
            add("duration_metadata", "Group durations do not sum to the target.")
        for group in plan.groups:
            if self.scripts.group_state(plan, group, script) != "complete":
                add("missing_group", "Expected retained sections are missing or incomplete.", group)
                continue
            for planned in group.sections:
                section = by_id[self.scripts.section_id(plan.project_id, planned.id)]
                count = len(section.text.split())
                repaired_budget = repaired_budgets.get(section.id)
                budget = repaired_budget or planned.target_word_count
                tolerance = .1 if repaired_budget else .25
                if abs(count - budget) > max(2 if repaired_budget else 0, budget * tolerance):
                    add("word_budget", f"{count} words; {'measured repair' if repaired_budget else 'planned'} budget {budget}. Audio duration still requires measured TTS.", group, section, "REVIEW")
                if abs(planned.target_word_count - round(planned.target_duration_seconds * WORDS_PER_SECOND)) > 1:
                    add("estimate_metadata", "Historical word estimate differs from the configured speech-rate estimate.", group, section, "REVIEW")
            try:
                status = self.group_status(plan, group)
                if "STALE" in status or "UNVERIFIED" in status:
                    add("context_freshness", status, group, severity="REVIEW")
            except ValueError as exc:
                add("context_freshness", str(exc), group, severity="REVIEW")
        return findings

    def review(self, plan):
        snapshot = self.snapshot(plan)
        return self.records.save("review", {"version": 1, "snapshot": snapshot,
            "mode": "deterministic", "findings": self.deterministic_findings(plan)})

    def prepare_editorial(self, plan):
        if self.scripts.provider is None:
            raise ValueError("Configure a structured editorial provider before requesting paid review.")
        snapshot = self.snapshot(plan)
        findings = self.deterministic_findings(plan)
        if any(f["severity"] == "BLOCKED" for f in findings):
            raise ValueError("Complete and correct the narrative structure before editorial review.")
        script = self.scripts.session.active_script
        by_id = {s.section_id: s for s in script.sections}
        source = [{"group_id": g.id, "section_id": s.section_id, "revision_id": s.id,
            "text": s.text} for g in plan.groups for p in g.sections
            for s in [by_id[self.scripts.section_id(plan.project_id, p.id)]]]
        prompt = canonical_json({"task": "review_narrative_continuity", "policy": "editorial-review-v1",
            "language": plan.language, "outline": [self.scripts._group_payload(g) for g in plan.groups],
            "narrative": plan.narrative.to_payload() if plan.narrative else None,
            "topic": plan.topic, "film_brief": plan.film_brief, "sources": source,
            "instruction": "Evaluate promise versus conclusion, topic introduction, progression, repetitions, transitions, "
                "contradictions, undefined references, unanswered question and ending. Literary judgments are REVIEW. "
                "Quote a literal supporting excerpt with the exact supplied group, section and revision. "
                "Suggest corrections; never rewrite or approve the script. Return no findings if none are supported."})
        return {"snapshot": snapshot, "sources": source, "prompt": prompt, "schema": editorial_schema(),
            "provider_identity": self.scripts._identity(), "deterministic": findings}

    def finish_editorial(self, plan, prepared, payload):
        if type(payload) is not dict or set(payload) != {"findings"} or type(payload["findings"]) is not list or len(payload["findings"]) > 40:
            raise ValueError("Malformed editorial review response.")
        required = set(editorial_schema()["properties"]["findings"]["items"]["required"])
        for finding in payload["findings"]:
            if type(finding) is not dict or set(finding) != required or any(type(v) is not str or not v.strip() for v in finding.values()):
                raise ValueError("Malformed editorial finding.")
            if finding["severity"] != "REVIEW" or finding["issue_type"] not in ISSUE_TYPES:
                raise ValueError("Editorial judgments must be REVIEW findings.")
            if not any(all(finding[k] == s[k] for k in ("group_id", "section_id", "revision_id")) and
                       finding["supporting_excerpt"] in s["text"] for s in prepared["sources"]):
                raise ValueError("Editorial finding is not supported by its retained source revision.")
        # Preserve the response for inspection even if a user edited during the paid call.
        usage_reader = getattr(self.scripts.provider, "last_usage", None)
        usage = usage_reader() if callable(usage_reader) else None
        # Usage evidence comes from the configured provider; no fabricated cost estimate.
        canonical_json(usage)
        return self.records.save("review", {"version": 1, "snapshot": prepared["snapshot"],
            "mode": "editorial", "provider_identity": prepared["provider_identity"],
            "usage": usage, "cost_evidence": "provider-reported usage" if usage else "unavailable",
            "prompt_fingerprint": content_fingerprint(prepared["prompt"]),
            "findings": prepared["deterministic"] + payload["findings"]})

    def review_editorial(self, plan):
        """Explicit caller action is the only entry that invokes a paid provider."""
        prepared = self.prepare_editorial(plan)
        payload = self.scripts.provider.generate_structured(prepared["prompt"], prepared["schema"])
        return self.finish_editorial(plan, prepared, payload)

    def latest_review(self, plan):
        history = self.records.history("review")
        if not history:
            return None
        manifest = history[-1]
        value = self.records.read(manifest)
        return manifest, value, value["snapshot"] != self.snapshot(plan)

    def approve_group(self, plan, group):
        self.snapshot(plan)
        if group not in plan.groups or self.scripts.group_state(plan, group) != "complete":
            raise ValueError("Only a complete current plan group can be approved.")
        ids = {self.scripts.section_id(plan.project_id, p.id) for p in group.sections}
        return self.records.save("approval", {"version": 1, "plan_id": plan.id, "group_id": group.id,
            "section_revisions": [s.id for s in self.scripts.session.active_script.sections if s.section_id in ids]})
