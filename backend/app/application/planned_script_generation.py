"""Durable, plan-group-at-a-time script generation and resume operations."""

from dataclasses import replace
import logging

from app.domain.base import new_id
from app.domain.dependencies import canonical_json, content_fingerprint
from app.domain.narrative_segment import SectionRevision
from app.domain.plan_script import (GeneratedGroupSection, GeneratedScriptGroup, PlanScriptBinding,
                                    planned_section_identity)
from app.domain.planned_script_schema import planned_script_group_schema, validate_planned_script_group
from app.domain.script import ScriptRevision


logger = logging.getLogger("aics.pipeline")
WORD_COUNT_TOLERANCE = 0.25


class PlanScriptGenerationService:
    def __init__(self, session, provider, video_plans, artifacts):
        self.session, self.provider = session, provider
        self.video_plans, self.artifacts = video_plans, artifacts

    def _selected_plan(self, plan):
        selected = self.video_plans.selected()
        if selected is None or selected.id != plan.id:
            raise ValueError("The selected Video Plan changed; refresh before continuing script generation.")
        return selected

    def start_or_resume(self, plan, *, replace_current=False):
        plan = self._selected_plan(plan)
        active = self.session.active_script
        selected_binding = self.artifacts.active_binding()
        if selected_binding is not None:
            if selected_binding.video_plan_revision_id != plan.id:
                if not replace_current:
                    raise ValueError("The active script is bound to an older Video Plan. Start script from the new plan explicitly.")
            elif selected_binding.script_id == active.script_id and not replace_current:
                self._validate_progress(plan, active)
                return selected_binding
            elif not replace_current:
                raise ValueError("The active script no longer matches its plan-script binding; explicitly start again from the plan.")
        elif active.sections and not replace_current:
            raise ValueError("Starting plan-driven generation would replace or conflict with the existing unbound script. Choose explicit Replace current script from plan.")

        if replace_current or (selected_binding is not None and selected_binding.video_plan_revision_id != plan.id):
            empty = ScriptRevision.create(project_id=active.project_id, language=active.language)
            self.session.save_script(empty, expected_active_revision_id=active.id)
            active = empty
        binding = PlanScriptBinding(new_id("plan_script_binding"), plan.project_id, plan.id, active.script_id)
        history = self.artifacts._selection_history()
        expected = history[-1].id if history else None
        self.artifacts.save_binding(binding, expected_selection_id=expected)
        return binding

    def _identity(self):
        builder = getattr(self.provider, "generation_identity", None)
        if callable(builder):
            identity = builder()
        else:
            identity = {"provider": getattr(self.provider, "provider_name", type(self.provider).__module__ + "." + type(self.provider).__qualname__)}
        if type(identity) is not dict:
            raise ValueError("Plan script provider must expose a JSON effective identity.")
        def scrub(value):
            if type(value) is dict:
                return {key: scrub(item) for key, item in value.items()
                        if not any(secret in key.lower() for secret in ("key", "secret", "token", "credential"))}
            if type(value) is list:
                return [scrub(item) for item in value]
            return value
        identity = scrub(identity)
        # Credentials never enter prompts or artifact metadata.
        canonical_json(identity)
        return identity

    @staticmethod
    def _group_payload(group):
        return {"id": group.id, "kind": group.kind, "title": group.title, "purpose": group.purpose,
                "target_duration_seconds": group.target_duration_seconds,
                "sections": [{"id": s.id, "title": s.title, "role": s.role, "purpose": s.purpose,
                              "target_duration_seconds": s.target_duration_seconds,
                              "target_word_count": s.target_word_count} for s in group.sections]}

    def prepare_group(self, plan, group, *, regeneration_id=None):
        self._selected_plan(plan)
        if group not in plan.groups:
            raise ValueError("Group does not belong to the selected plan.")
        from app.domain.video_plan import VideoFormat
        context = self.narrative_context(plan, group) if plan.format is VideoFormat.STANDARD else None
        identity = self._identity()
        plan_fingerprint = content_fingerprint(plan.to_payload())
        group_value = self._group_payload(group)
        key = content_fingerprint({"version": 1, "plan_id": plan.id, "plan": plan_fingerprint,
                                   "group": group_value, "language": plan.language,
                                   "provider_identity": identity, **({"policy": "narrative-context-v2", "context": context} if context else {}),
                                   **({"regeneration_id": regeneration_id} if regeneration_id else {})})
        cached = self.artifacts.cached_group(key)
        expected = tuple(section.id for section in group.sections)
        if cached is not None:
            self._validate_cached(cached, plan, group, key)
            self._validate_context_result(cached, context)
            return {"cache_key": key, "cached": cached, "prompt": None, "schema": None, "regeneration_id": regeneration_id, "source_revisions": tuple(context["source_revisions"]) if context else (), "policy": "narrative-context-v2" if context else None}
        index = next(i for i, item in enumerate(plan.groups) if item.id == group.id)
        neighbors = {"previous": self._neighbor(plan, index - 1), "next": self._neighbor(plan, index + 1)}
        prompt = canonical_json({"task": "generate_planned_script_group", "language": plan.language,
            "video_format": plan.format.value, "target_duration_seconds": plan.target_duration_seconds,
            "working_title": plan.working_title, "topic": plan.topic, "film_brief": plan.film_brief,
            "group": group_value, "neighboring_group_summaries": neighbors,
            **({"narrative_context": context} if context else {}),
            "instruction": ("Return only the planned sections in their supplied order. Target word counts are guidance." if context is None else
                "Return only the planned sections in their supplied order. Target word counts are guidance. "
                "Use natural transitions, consistent terminology and established assumptions. Build on retained text; "
                "avoid repeating definitions or restarting the topic. No artificial chapter announcements, greetings, "
                "channel advertising or subscription requests. Hook: create curiosity without revealing the ending. "
                "Introduction: explain the subject, context, scenario and assumptions, then transition into the chapters. "
                "Conclusion: resolve the original promise, explain implications and deliver the requested takeaway; "
                "do not introduce a major new topic or manufacture certainty. Excerpts are partial evidence, not summaries of omitted claims.")})
        return {"cache_key": key, "cached": None, "prompt": prompt,
                "schema": planned_script_group_schema(len(expected)), "expected_ids": expected,
                "identity": identity, "source_revisions": tuple(context["source_revisions"]) if context else (),
                "policy": "narrative-context-v2" if context else None, "regeneration_id": regeneration_id}

    def narrative_context(self, plan, group):
        """Deterministic extraction only: excerpts and literal tokens, no invented facts."""
        import re
        index = plan.groups.index(group)
        script = self.session.active_script
        by_id = {s.section_id: s for s in script.sections}
        previous, sources, full_opening, tokens, questions = [], [], {}, [], []
        context_groups = (tuple(g for g in plan.groups if g.id != group.id)
                          if group.kind == "conclusion" else plan.groups[:index])
        for prior in context_groups:
            if self.group_state(plan, prior, script) != "complete":
                if group.kind == "conclusion":
                    raise ValueError("Complete all preceding plan groups before generating the conclusion.")
                continue
            extracts = []
            for planned in prior.sections:
                section = by_id[self.section_id(plan.project_id, planned.id)]
                sources.append(section.id)
                words = section.text.split()
                extracts.append({"section_id": section.section_id, "revision_id": section.id,
                    "text_fingerprint": content_fingerprint(section.text),
                    "opening_excerpt": " ".join(words[:60]), "ending_excerpt": " ".join(words[-60:])})
                # Literal candidate names/terms and questions; no semantic assertions.
                tokens.extend(re.findall(r"\b[A-Z][\w-]{2,}\b", section.text))
                questions.extend(re.findall(r"[^.!?]*\?", section.text))
            previous.append({"group_id": prior.id, "kind": prior.kind, "retained_excerpts": extracts})
            if prior.kind in {"cold_open", "opening", "introduction"}:
                full_opening[prior.kind] = [{"revision_id": by_id[self.section_id(plan.project_id, p.id)].id,
                    "text": " ".join(by_id[self.section_id(plan.project_id, p.id)].text.split()[:200])}
                    for p in prior.sections]
        preceding = plan.groups[index - 1] if index else None
        ending = None
        if preceding and self.group_state(plan, preceding, script) == "complete":
            section = by_id[self.section_id(plan.project_id, preceding.sections[-1].id)]
            ending = {"revision_id": section.id, "text": " ".join(section.text.split()[-100:])}
        narrative = plan.narrative.to_payload() if plan.narrative else None
        return {"extraction_policy": "literal-excerpts-v1", "source_revisions": sources,
            "narrative": narrative, "full_plan_outline": [self._group_payload(g) for g in plan.groups],
            "current_objective": group.purpose, "film_brief_assumptions": plan.film_brief,
            "committed_previous_groups": previous, "preceding_ending": ending,
            "opening_material": full_opening, "literal_terminology_candidates": list(dict.fromkeys(tokens))[:80],
            "question_excerpts_to_review": [q.strip()[:300] for q in questions[:20]],
            "next_contribution": self._neighbor(plan, index + 1)}

    @staticmethod
    def _validate_context_result(result, context):
        if context is not None and (result.policy != "narrative-context-v2" or
                                    result.source_revisions != tuple(context["source_revisions"])):
            raise ValueError("Generated group does not match the exact narrative context revisions.")

    @staticmethod
    def _neighbor(plan, index):
        if not 0 <= index < len(plan.groups):
            return None
        group = plan.groups[index]
        return {"kind": group.kind, "title": group.title, "purpose": group.purpose}

    def generate_payload(self, prepared):
        if self.provider is None:
            raise ValueError("A structured script provider is required for plan-driven generation.")
        return self.provider.generate_structured(prepared["prompt"], prepared["schema"])

    def validate_and_cache(self, plan, group, prepared, payload):
        self._selected_plan(plan)
        expected = tuple(section.id for section in group.sections)
        texts = validate_planned_script_group(payload, expected)
        for section, text in zip(group.sections, texts):
            count = len(text.split())
            target = section.target_word_count
            if abs(count - target) > target * WORD_COUNT_TOLERANCE:
                raise ValueError(f"Generated section {section.title!r} has {count} words; target {target} allows ±25%.")
        result = GeneratedScriptGroup(new_id("generated_script_group"), plan.project_id, plan.id, group.id,
            prepared["cache_key"], tuple(GeneratedGroupSection(section_id, text)
                                         for section_id, text in zip(expected, texts)),
            prepared.get("source_revisions", ()), prepared.get("policy"))
        self.artifacts.save_group(result)
        return result

    @staticmethod
    def _validate_cached(result, plan, group, cache_key):
        if (result.project_id, result.video_plan_revision_id, result.group_id, result.cache_key) != (
                plan.project_id, plan.id, group.id, cache_key):
            raise ValueError("Cached script group belongs to different plan inputs.")
        expected = tuple(section.id for section in group.sections)
        texts = tuple(item.text for item in result.sections)
        validate_planned_script_group({"sections": [{"planned_section_id": value.planned_section_id, "text": value.text}
                                                       for value in result.sections]}, expected)
        for section, text in zip(group.sections, texts):
            count = len(text.split())
            if abs(count - section.target_word_count) > section.target_word_count * WORD_COUNT_TOLERANCE:
                raise ValueError("Cached generated section no longer passes its word-budget validation.")

    @staticmethod
    def section_id(project_id, planned_section_id):
        return planned_section_identity(project_id, planned_section_id)

    def group_state(self, plan, group, script=None):
        script = script or self.session.active_script
        actual = {section.section_id for section in script.sections}
        expected = {self.section_id(plan.project_id, section.id) for section in group.sections}
        found = actual & expected
        if not found:
            return "pending"
        if found == expected:
            return "complete"
        return "inconsistent"

    def progress(self, plan):
        script = self.session.active_script
        self._validate_progress(plan, script)
        return tuple(self.group_state(plan, group, script) for group in plan.groups)

    def _validate_progress(self, plan, script):
        planned = {self.section_id(plan.project_id, section.id)
                   for group in plan.groups for section in group.sections}
        active = {section.section_id for section in script.sections}
        if any(self.group_state(plan, group, script) == "inconsistent" for group in plan.groups):
            raise ValueError("Active script contains a partial plan group; resolve it before continuing.")
        # Non-plan manual additions are retained and allowed; they are kept after the ordered plan sections.
        return planned, active

    def commit_group(self, plan, group, result, *, expected_active_revision_id, regeneration_id=None):
        self._selected_plan(plan)
        binding = self.artifacts.active_binding()
        current = self.session.active_script
        if current.id != expected_active_revision_id:
            raise ValueError("Active script changed during group generation; cached output was retained without selection.")
        if binding is None or binding.video_plan_revision_id != plan.id or binding.script_id != current.script_id:
            raise ValueError("Active plan-script binding changed during group generation.")
        prepared = self.prepare_group(plan, group, regeneration_id=regeneration_id)
        if prepared["cache_key"] != result.cache_key:
            raise ValueError("Generated group cache key no longer matches current provider or plan inputs.")
        if result.policy != prepared["policy"] or result.source_revisions != prepared["source_revisions"]:
            raise ValueError("Generated group does not match the exact narrative context revisions.")
        retained = self.artifacts.cached_group(result.cache_key)
        if retained != result:
            raise ValueError("Generated group is not the exact retained cache artifact.")
        self._validate_cached(result, plan, group, prepared["cache_key"])
        state = self.group_state(plan, group, current)
        if state == "complete" and regeneration_id is None:
            return current
        if state not in {"pending", "complete"}:
            raise ValueError("Active script contains an inconsistent partial plan group.")
        by_id = {section.section_id: section for section in current.sections}
        for planned, generated in zip(group.sections, result.sections):
            section = SectionRevision.create_for_plan(project_id=plan.project_id,
                planned_section_id=planned.id, title=planned.title, role=planned.role, text=generated.text)
            if section.section_id in by_id:
                if regeneration_id is None:
                    raise ValueError("Planned section identity already exists with inconsistent progress.")
                section = by_id[section.section_id].revise(text=generated.text)
            by_id[section.section_id] = section
        ordered_ids = [self.section_id(plan.project_id, item.id)
                       for parent in plan.groups for item in parent.sections]
        if regeneration_id is not None and state == "complete":
            selected = [by_id[s.section_id] for s in current.sections]
        else:
            selected = [by_id[key] for key in ordered_ids if key in by_id]
            known = set(ordered_ids)
            selected.extend(section for section in current.sections if section.section_id not in known)
        revision = replace(current, id=new_id("script_revision"), parent_revision_id=current.id,
                           sections=tuple(selected))
        if result.policy:
            from app.storage.narrative_reviews import ProjectNarrativeRecords
            ids = {self.section_id(plan.project_id, p.id) for p in group.sections}
            ProjectNarrativeRecords(self.session.repository, self.artifacts.store).save("generation", {
                "version": 1, "plan_id": plan.id, "group_id": group.id, "generated_group_id": result.id,
                "section_revisions": [s.id for s in revision.sections if s.section_id in ids],
                "source_revisions": list(result.source_revisions)})
        self.session.save_script(revision, expected_active_revision_id=current.id)
        return revision

    def regenerate_group(self, plan, group):
        """Explicit replacement of one group, preserving every other section revision."""
        self.start_or_resume(plan)
        before = self.session.active_script.id
        request = new_id("group_regeneration")
        prepared = self.prepare_group(plan, group, regeneration_id=request)
        result = self.validate_and_cache(plan, group, prepared, self.generate_payload(prepared))
        return self.commit_group(plan, group, result, expected_active_revision_id=before, regeneration_id=request)

    def run_next_group(self, plan):
        """Synchronous application helper for non-UI callers; commits each group."""
        self.start_or_resume(plan)
        for group, state in zip(plan.groups, self.progress(plan)):
            if state == "complete":
                logger.info("[AICS][PIPELINE][AUTO][SCRIPT_GROUP][SKIP] group=%r", group.title)
                continue
            if state != "pending":
                raise ValueError("Active script contains an inconsistent partial plan group.")
            before = self.session.active_script.id
            prepared = self.prepare_group(plan, group)
            if prepared["cached"] is not None:
                result = prepared["cached"]
                logger.info("[AICS][PIPELINE][AUTO][SCRIPT_GROUP][CACHE] group=%r", group.title)
            else:
                logger.info("[AICS][PIPELINE][AUTO][SCRIPT_GROUP][BUILD] group=%r sections=%d", group.title, len(group.sections))
                result = self.validate_and_cache(plan, group, prepared, self.generate_payload(prepared))
            self.commit_group(plan, group, result, expected_active_revision_id=before)
            logger.info("[AICS][PIPELINE][AUTO][SCRIPT_GROUP][OK] group=%r words=%d", group.title,
                        sum(len(item.text.split()) for item in result.sections))
            yield self.session.active_script
