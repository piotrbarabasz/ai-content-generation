import asyncio
import json
from types import SimpleNamespace

import pytest

from app.application.automatic_workflow import (AutomaticWorkflow, AutomaticWorkflowCanceled,
                                                AutomaticWorkflowConfig)
from app.application.planned_script_generation import PlanScriptGenerationService
from app.application.projects import ProjectSession
from app.application.visual_prompts import VisualPromptService
from app.desktop.pipeline_driver import DesktopPipelineDriver
from app.domain.base import new_id
from app.domain.narrative_segment import SectionRevision
from app.domain.video_plan import PlannedSection, VideoFormat, VideoPlanGroup, VideoPlanRevision
from app.storage.local_store import LocalArtifactStore
from app.storage.plan_script import ProjectPlanScripts
from app.storage.project_repository import ProjectRepository
from app.storage.video_plans import ProjectVideoPlans


def build_plan(project_id, fmt="social"):
    if fmt == "social":
        kinds, durations = ["hook", "body", "explanation", "close"], [11, 11, 11, 12]
        total = 45
    else:
        kinds = ["cold_open", "introduction", *(["chapter"] * 6), "conclusion"]
        durations = [600 // len(kinds) + (i < 600 % len(kinds)) for i in range(len(kinds))]
        total = 600
    groups = []
    for i, (kind, duration) in enumerate(zip(kinds, durations)):
        section_durations = (duration // 2, duration - duration // 2) if fmt == "standard" and kind == "chapter" else (duration,)
        sections = tuple(PlannedSection(f"planned-section-{i}-{j}", f"Section {i}-{j}", kind,
                                        "Explain this beat", section_duration, 4, 1)
                         for j, section_duration in enumerate(section_durations))
        groups.append(VideoPlanGroup(f"group-{i}", kind, kind.title(), "Purpose", duration, sections))
    return VideoPlanRevision(new_id("video_plan_revision"), project_id, "en",
        VideoFormat(fmt), total, "Test topic", "Test title", "Test brief", "Test style", tuple(groups))


class FakeProvider:
    provider_name = "deterministic-fixture"
    def __init__(self): self.calls = []
    def generation_identity(self): return {"provider": "fixture", "model": "fake", "api_key": "never-retain"}
    def generate_structured(self, prompt, schema):
        payload = json.loads(prompt)
        group = payload["group"]
        self.calls.append(group["id"])
        return {"sections": [{"planned_section_id": section["id"],
                               "text": " ".join([f"word{i}" for i in range(section["target_word_count"])])}
                              for section in group["sections"]]}


def env(tmp_path, fmt="social", provider=None):
    session = ProjectSession.create(tmp_path / "project", repository_factory=ProjectRepository, name="Plan project")
    store = LocalArtifactStore.for_project(session.repository)
    plans = ProjectVideoPlans(session.repository, store)
    plan = build_plan(session.project.id, fmt)
    plans.save_revision(plan)
    plans.select(plan, expected_selection_id=None)
    artifacts = ProjectPlanScripts(session.repository, store, plans)
    provider = provider or FakeProvider()
    service = PlanScriptGenerationService(session, provider, plans, artifacts)
    return session, store, plans, plan, artifacts, service, provider


def generate_group(service, plan, group):
    prepared = service.prepare_group(plan, group)
    result = prepared["cached"] or service.validate_and_cache(plan, group, prepared,
                                                              service.generate_payload(prepared))
    return service.commit_group(plan, group, result,
                                expected_active_revision_id=service.session.active_script.id)


def test_social_groups_commit_resume_reopen_and_keep_manual_edits(tmp_path):
    session, store, plans, plan, artifacts, service, provider = env(tmp_path)
    try:
        service.start_or_resume(plan)
        for group in plan.groups:
            generate_group(service, plan, group)
        assert len(provider.calls) == len(plan.groups) == 4
        active = session.active_script
        expected_ids = [service.section_id(plan.project_id, section.id)
                        for group in plan.groups for section in group.sections]
        assert [section.section_id for section in active.sections] == expected_ids
        edited = session.edit_section(expected_ids[0], text="A manual edit survives resume.")
        assert edited.sections[0].text == "A manual edit survives resume."
        assert service.progress(plan) == ("complete",) * 4
        assert list(service.run_next_group(plan)) == []
        assert len(provider.calls) == 4
        script_id = edited.script_id
    finally:
        session.close()

    reopened = ProjectSession.open(tmp_path / "project", repository_factory=ProjectRepository)
    try:
        store2 = LocalArtifactStore.for_project(reopened.repository)
        plans2 = ProjectVideoPlans(reopened.repository, store2)
        artifacts2 = ProjectPlanScripts(reopened.repository, store2, plans2)
        service2 = PlanScriptGenerationService(reopened, provider, plans2, artifacts2)
        assert reopened.active_script.script_id == script_id
        assert reopened.active_script.sections[0].text == "A manual edit survives resume."
        assert service2.progress(plan) == ("complete",) * 4
    finally:
        reopened.close()


def test_cached_valid_group_is_reused_after_reopen_without_provider_call(tmp_path):
    session, store, plans, plan, artifacts, service, provider = env(tmp_path)
    service.start_or_resume(plan)
    group = plan.groups[0]
    prepared = service.prepare_group(plan, group)
    result = service.validate_and_cache(plan, group, prepared, service.generate_payload(prepared))
    assert len(provider.calls) == 1 and service.group_state(plan, group) == "pending"
    session.close()

    reopened = ProjectSession.open(tmp_path / "project", repository_factory=ProjectRepository)
    try:
        store2 = LocalArtifactStore.for_project(reopened.repository)
        plans2 = ProjectVideoPlans(reopened.repository, store2)
        artifacts2 = ProjectPlanScripts(reopened.repository, store2, plans2)
        service2 = PlanScriptGenerationService(reopened, provider, plans2, artifacts2)
        prepared2 = service2.prepare_group(plan, group)
        assert prepared2["cached"] == result
        service2.commit_group(plan, group, prepared2["cached"],
                              expected_active_revision_id=reopened.active_script.id)
        assert len(provider.calls) == 1
    finally:
        reopened.close()


def test_unbound_manual_script_blocks_and_explicit_replace_keeps_old_history(tmp_path):
    session, _, plans, plan, artifacts, service, _ = env(tmp_path)
    manual = session.active_script
    from app.application.script_generation import ScriptGenerationService
    manual = ScriptGenerationService(session).append_text("Manual text", title="Manual",
        expected_active_revision_id=manual.id)
    with pytest.raises(ValueError, match="unbound script"):
        service.start_or_resume(plan)
    binding = service.start_or_resume(plan, replace_current=True)
    assert session.active_script.sections == ()
    assert session.active_script.script_id == binding.script_id
    assert session.repository.get_script(manual.id) == manual
    session.close()


def test_partial_group_blocks_instead_of_filling_unknown_missing_section(tmp_path):
    session, _, plans, plan, artifacts, service, _ = env(tmp_path, "standard")
    service.start_or_resume(plan)
    group = next(group for group in plan.groups if group.kind == "chapter")
    planned = group.sections[0]
    revision = SectionRevision.create_for_plan(project_id=plan.project_id,
        planned_section_id=planned.id, title=planned.title, role=planned.role, text="manual partial")
    from dataclasses import replace
    current = session.active_script
    partial = replace(current, id=new_id("script_revision"), parent_revision_id=current.id, sections=(revision,))
    session.save_script(partial, expected_active_revision_id=current.id)
    with pytest.raises(ValueError, match="partial plan group"):
        service.progress(plan)
    session.close()


def test_out_of_tolerance_provider_output_is_not_cached_or_committed(tmp_path):
    session, _, plans, plan, artifacts, service, _ = env(tmp_path)
    service.start_or_resume(plan)
    group = plan.groups[0]
    prepared = service.prepare_group(plan, group)
    payload = {"sections": [{"planned_section_id": group.sections[0].id,
                              "text": "one two three four five six"}]}
    with pytest.raises(ValueError, match="±25%"):
        service.validate_and_cache(plan, group, prepared, payload)
    assert service.group_state(plan, group) == "pending"
    assert artifacts.cached_group(prepared["cache_key"]) is None
    session.close()


def test_corrupt_cache_selected_plan_change_and_script_conflict_fail_closed(tmp_path):
    session, store, plans, plan, artifacts, service, provider = env(tmp_path)
    service.start_or_resume(plan)
    group = plan.groups[0]
    prepared = service.prepare_group(plan, group)
    result = service.validate_and_cache(plan, group, prepared, service.generate_payload(prepared))
    manifest = next(m for m in store.list_artifacts() if m.metadata.get("value_id") == result.id)
    path = store.root / manifest.storage_key
    path.write_bytes(path.read_bytes() + b" ")
    with pytest.raises(ValueError, match="checksum"):
        service.prepare_group(plan, group)
    # A separate fresh project demonstrates that a changed selected plan fences late results.
    session.close()

    second = ProjectSession.open(tmp_path / "project", repository_factory=ProjectRepository)
    try:
        store2 = LocalArtifactStore.for_project(second.repository)
        plans2 = ProjectVideoPlans(second.repository, store2)
        artifacts2 = ProjectPlanScripts(second.repository, store2, plans2)
        service2 = PlanScriptGenerationService(second, provider, plans2, artifacts2)
        other = build_plan(second.project.id)
        from dataclasses import replace
        other = replace(other, id=new_id("video_plan_revision"), topic="Changed topic")
        plans2.save_revision(other)
        active_selection = plans2.selected_id()
        plans2.select(other, expected_selection_id=active_selection)
        with pytest.raises(ValueError, match="selected Video Plan changed"):
            service2.prepare_group(plan, group)
    finally:
        second.close()


def test_provider_failure_and_active_revision_conflict_leave_committed_progress_unchanged(tmp_path):
    session, _, plans, plan, artifacts, service, provider = env(tmp_path)
    service.start_or_resume(plan)
    group = plan.groups[0]
    prepared = service.prepare_group(plan, group)
    expected = session.active_script.id
    class FailingProvider:
        provider_name = "fixture"
        def generate_structured(self, *_): raise RuntimeError("provider failed")
    failing = PlanScriptGenerationService(session, FailingProvider(), plans, artifacts)
    with pytest.raises(RuntimeError, match="provider failed"):
        failing.generate_payload(prepared)
    assert artifacts.cached_group(prepared["cache_key"]) is None
    payload = provider.generate_structured(prepared["prompt"], prepared["schema"])
    result = service.validate_and_cache(plan, group, prepared, payload)
    from app.application.script_generation import ScriptGenerationService
    ScriptGenerationService(session).append_text("Concurrent", title="Concurrent",
        expected_active_revision_id=expected)
    with pytest.raises(ValueError, match="Active script changed"):
        service.commit_group(plan, group, result, expected_active_revision_id=expected)
    assert service.group_state(plan, group) == "pending"
    assert artifacts.cached_group(prepared["cache_key"]) == result
    session.close()


class AutoEditor:
    def __init__(self, session, provider, plans, service, workflow_holder):
        self.session, self.provider = session, provider
        port = VisualPromptServicePort(session, service.artifacts.store)
        prompt_service = VisualPromptService(port, provider, generation_identity={"provider": "fixture"})
        generation = SimpleNamespace(provider=object())
        scene_services = SimpleNamespace(prompts=prompt_service, _generation_for=lambda _id: generation,
                                         upscale=None)
        self.video_plan = SimpleNamespace(plans=plans, script_service=service,
            _render=lambda: None, _script_progress=lambda: None)
        self.audio = SimpleNamespace(services=SimpleNamespace(attempt=None))
        self.visuals = SimpleNamespace(services=scene_services)
        self.timeline = SimpleNamespace(services=object())
        self.workflow_holder = workflow_holder
        self.dirty, self.worker = False, None
    def _refresh(self): pass


class VisualPromptServicePort:
    def __init__(self, session, store):
        from app.storage.visual_prompts import ProjectVisualPrompts
        self.prompts = ProjectVisualPrompts(session.repository, store)
        self.project_id = session.project.id
    def context(self, revision_id): return self.prompts.context(revision_id)
    def save_context(self, revision): return self.prompts.save_context(revision)
    def current_context(self, kind): return self.prompts.current_context(kind)


def test_standard_automatic_cancel_resume_skips_three_committed_groups(tmp_path):
    provider = FakeProvider()
    session, store, plans, plan, artifacts, service, _ = env(tmp_path, "standard", provider)
    holder = {}
    class CancelOnFourth(FakeProvider):
        def generate_structured(self, prompt, schema):
            result = super().generate_structured(prompt, schema)
            if len(self.calls) == 4:
                holder["workflow"].cancel()
            return result
    provider = CancelOnFourth()
    service = PlanScriptGenerationService(session, provider, plans, artifacts)
    editor = AutoEditor(session, provider, plans, service, holder)
    driver = DesktopPipelineDriver(editor, AutomaticWorkflowConfig(SimpleNamespace(provider="fixture"), None,
                                                                     "landscape", "draft"))
    driver.plan_state = lambda section: SimpleNamespace(state="accepted", acceptance_id="accepted")
    driver.voice_ready = lambda *_: True
    driver.timing_ready = lambda *_: True
    driver.scenes = lambda *_: ()
    driver.timeline_candidates = lambda *_: ()
    driver.timeline_matches = lambda *_: True
    driver.current_timeline = lambda: SimpleNamespace(timeline=SimpleNamespace(clips=(), duration=0))
    editor.visuals.services.prompts.pin_context("film_brief", "User's existing brief")
    first = AutomaticWorkflow(driver)
    holder["workflow"] = first
    with pytest.raises(AutomaticWorkflowCanceled):
        asyncio.run(first.run(driver.config))
    prompts = editor.visuals.services.prompts.prompts
    assert prompts.current_context("film_brief").text == "User's existing brief"
    assert prompts.current_context("visual_style").text == plan.visual_style
    expected = [service.section_id(plan.project_id, section.id)
                for group in plan.groups[:3] for section in group.sections]
    assert [section.section_id for section in session.active_script.sections] == expected
    assert len(provider.calls) == 4
    session.close()

    reopened = ProjectSession.open(tmp_path / "project", repository_factory=ProjectRepository)
    try:
        store2 = LocalArtifactStore.for_project(reopened.repository)
        plans2 = ProjectVideoPlans(reopened.repository, store2)
        artifacts2 = ProjectPlanScripts(reopened.repository, store2, plans2)
        service2 = PlanScriptGenerationService(reopened, provider, plans2, artifacts2)
        editor2 = AutoEditor(reopened, provider, plans2, service2, {})
        driver2 = DesktopPipelineDriver(editor2, driver.config)
        driver2.plan_state = driver.plan_state
        driver2.voice_ready = driver.voice_ready
        driver2.timing_ready = driver.timing_ready
        driver2.scenes = driver.scenes
        driver2.timeline_candidates = driver.timeline_candidates
        driver2.timeline_matches = driver.timeline_matches
        driver2.current_timeline = driver.current_timeline
        workflow = AutomaticWorkflow(driver2)
        result = asyncio.run(workflow.run(driver2.config))
        assert result.plan_script_groups_completed == result.plan_script_groups_total == len(plan.groups)
        assert len(provider.calls) == len(plan.groups)  # group 4 came from its retained cache
        assert [section.section_id for section in reopened.active_script.sections] == [
            service2.section_id(plan.project_id, section.id)
            for group in plan.groups for section in group.sections]
    finally:
        reopened.close()
