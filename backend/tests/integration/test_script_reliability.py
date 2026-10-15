"""Offline repair/resume checks use isolated projects and fake structured responses."""
import asyncio
from dataclasses import replace
import json
from types import SimpleNamespace

import pytest

from app.application.automatic_workflow import AutomaticWorkflow, AutomaticWorkflowBlocked, AutomaticWorkflowConfig
from app.application.planned_script_generation import PlanScriptGenerationService
from app.application.projects import ProjectSession
from app.desktop.pipeline_driver import DesktopPipelineDriver
from app.domain.dependencies import content_fingerprint
from app.domain.plan_script import GeneratedScriptGroup, GeneratedGroupSection
from app.domain.base import new_id
from app.domain.llm_errors import StructuredOutputError
from app.storage.local_store import LocalArtifactStore
from app.storage.plan_script import ProjectPlanScripts
from app.storage.project_repository import ProjectRepository
from app.storage.video_plans import ProjectVideoPlans
from backend.tests.integration.test_plan_driven_script import env, generate_group, FakeProvider, AutoEditor


class RepairProvider(FakeProvider):
    def __init__(self, failures=1):
        super().__init__()
        self.failures = failures
        self.prompts = []

    def generate_structured(self, prompt, schema):
        self.prompts.append(json.loads(prompt))
        payload = super().generate_structured(prompt, schema)
        if self.failures:
            self.failures -= 1
            return {"sections": {"invented-id": "Wrong slot"}}
        return payload


def test_provider_exhaustion_is_not_retried_again_by_group_policy(tmp_path):
    from app.providers.openai_llm import OpenAITransportError
    from backend.tests.unit.test_openai_llm import provider as openai_provider, Transport
    errors = [OpenAITransportError("OpenAI request timed out.", retryable=True) for _ in range(2)]
    transport = Transport(*errors)
    provider = openai_provider(transport, maxRetries=1, retryDelaySeconds=0)
    session, _, _, plan, _, service, _ = env(tmp_path, provider=provider)
    try:
        with pytest.raises(RuntimeError, match="NETWORK_TIMEOUT") as captured:
            list(service.run_next_group(plan))
        assert captured.value.__cause__ is errors[-1]
        assert len(transport.calls) == 2
        assert session.active_script.sections == ()
    finally:
        session.close()


def test_malformed_json_uses_one_corrected_replacement(tmp_path):
    from backend.tests.unit.test_openai_llm import provider as openai_provider, Transport, response
    transport = Transport(response("invalid json"), response('{"sections":{"section_1":"The army crossed quietly."}}'))
    provider = openai_provider(transport)
    session, _, _, plan, _, service, _ = env(tmp_path, provider=provider)
    try:
        iterator = service.run_next_group(plan)
        next(iterator)
        iterator.close()
        assert len(transport.calls) == 2
        assert len(session.active_script.sections) == 1
        assert "correction" in json.loads(transport.calls[1]["payload"]["input"][1]["content"])
    finally:
        session.close()


def test_three_completed_groups_fourth_repairs_only_pending_group_and_restarts(tmp_path):
    session, _, _, plan, _, service, _ = env(tmp_path, "standard")
    service.start_or_resume(plan)
    for group in plan.groups[:3]:
        generate_group(service, plan, group)
    retained = session.active_script.sections
    provider = RepairProvider()
    service.provider = provider
    editor = AutoEditor(session, provider, service.video_plans, service, {})
    driver = DesktopPipelineDriver(editor, None)
    asyncio.run(driver.ensure_plan_script(plan))
    assert service.progress(plan) == ("complete",) * 9
    assert session.active_script.sections[:len(retained)] == retained
    assert provider.calls == [plan.groups[3].id, plan.groups[3].id] + [g.id for g in plan.groups[4:]]
    assert "correction" in provider.prompts[1]
    expected = [s.id for g in plan.groups for s in g.sections]
    assert [s.section_id for s in session.active_script.sections] == [service.section_id(plan.project_id, i) for i in expected]
    assert not service.narration_findings(plan)
    before = session.active_script
    session.close()
    reopened = ProjectSession.open(tmp_path / "project", repository_factory=ProjectRepository)
    try:
        store = LocalArtifactStore.for_project(reopened.repository)
        plans = ProjectVideoPlans(reopened.repository, store)
        again = PlanScriptGenerationService(reopened, provider, plans, ProjectPlanScripts(reopened.repository, store, plans))
        calls = len(provider.calls)
        assert list(again.run_next_group(plan)) == []
        assert reopened.active_script == before and len(provider.calls) == calls
    finally:
        reopened.close()


def test_invalid_replacements_stop_without_partial_cache_or_commit(tmp_path):
    session, _, _, plan, artifacts, service, _ = env(tmp_path)
    try:
        service.start_or_resume(plan)
        generate_group(service, plan, plan.groups[0])
        retained = session.active_script
        provider = RepairProvider(10)
        service.provider = provider
        prepared = service.prepare_group(plan, plan.groups[1])
        with pytest.raises(RuntimeError, match="attempt=2.*existing_sections=preserved") as error:
            list(service.run_next_group(plan))
        assert isinstance(error.value.__cause__, StructuredOutputError)
        assert len(provider.calls) == 2
        assert session.active_script == retained
        assert artifacts.cached_group(prepared["cache_key"]) is None
        assert service.progress(plan) == ("complete", "pending", "pending", "pending")
    finally:
        session.close()


def test_new_editorial_output_is_rejected_before_cache(tmp_path):
    session, _, _, plan, artifacts, service, _ = env(tmp_path)
    try:
        service.start_or_resume(plan)
        group = plan.groups[0]
        prepared = service.prepare_group(plan, group)
        with pytest.raises(StructuredOutputError, match="editorial instruction"):
            service.validate_and_cache(plan, group, prepared, {"sections": {"section_1": "Show the audience the stakes."}})
        assert session.active_script.sections == ()
        assert artifacts.cached_group(prepared["cache_key"]) is None
    finally:
        session.close()


def test_historical_instructions_are_read_only_reported_and_block_all_tts(tmp_path):
    session, _, _, plan, _, service, provider = env(tmp_path, "standard")
    try:
        list(service.run_next_group(plan))
        first = session.active_script.sections[0]
        session.edit_section(first.section_id, text="Section 0-0 — Pose the central question for the audience.")
        before = session.active_script
        findings = service.narration_findings(plan)
        assert findings[0]["group"] == plan.groups[0].title
        assert findings[0]["section_id"] == first.section_id
        assert findings[0]["revision_id"] == before.sections[0].id
        assert "explicitly" in findings[0]["action"]
        assert session.active_script == before
        driver = DesktopPipelineDriver(AutoEditor(session, provider, service.video_plans, service, {}), None)
        events = []
        driver.voice_ready = lambda *_: events.append("tts")
        driver.image_ready = lambda *_: events.append("image")
        config = AutomaticWorkflowConfig(SimpleNamespace(provider="fixture"), None, "landscape", "draft")
        with pytest.raises(AutomaticWorkflowBlocked, match="revision=.*explicitly") as error:
            asyncio.run(AutomaticWorkflow(driver).run(config))
        assert error.value.stage == "NARRATION"
        assert not events and session.active_script == before
        service.regenerate_group(plan, plan.groups[0])  # explicit application action
        assert session.active_script.sections[1:] == before.sections[1:]
        assert session.repository.get_script(before.id) == before
        assert not service.narration_findings(plan)
    finally:
        session.close()


def test_v1_cache_key_remains_readable_but_v2_has_distinct_identity(tmp_path):
    session, _, _, plan, artifacts, service, provider = env(tmp_path)
    try:
        service.start_or_resume(plan)
        group = plan.groups[0]
        current = service.prepare_group(plan, group)
        old_key = content_fingerprint({"version": 1, "plan_id": plan.id,
            "plan": content_fingerprint(plan.to_payload()), "group": service._group_payload(group),
            "language": plan.language, "provider_identity": service._identity()})
        assert current["cache_key"] != old_key
        old = GeneratedScriptGroup(new_id("generated_script_group"), plan.project_id, plan.id, group.id,
                                   old_key, (GeneratedGroupSection(group.sections[0].id, "The army crossed quietly."),))
        artifacts.save_group(old)
        cached = service.prepare_group(plan, group)
        assert cached["cached"] == old and cached["cache_key"] == old_key
        service.commit_group(plan, group, old, expected_active_revision_id=session.active_script.id)
        assert not provider.calls
        assert session.active_script.sections[0].text == old.sections[0].text
    finally:
        session.close()


def test_same_plan_id_cannot_mask_altered_source_sections(tmp_path):
    session, _, _, plan, _, service, _ = env(tmp_path)
    try:
        changed_group = replace(plan.groups[0], sections=(replace(plan.groups[0].sections[0], id="invented"),))
        changed_plan = replace(plan, groups=(changed_group, *plan.groups[1:]))
        with pytest.raises(ValueError, match="selected Video Plan changed"):
            service.prepare_group(changed_plan, changed_group)
    finally:
        session.close()
