"""D075 offline contracts, retained source identity and acceptance flow."""
from copy import deepcopy
from dataclasses import replace
import json

import pytest

from app.application.narrative_continuity import NarrativeContinuityService
from app.application.planned_script_generation import PlanScriptGenerationService
from app.application.projects import ProjectSession
from app.application.video_planning import VideoPlanningService
from app.domain.base import new_id
from app.domain.plan_script import GeneratedScriptGroup
from app.domain.video_plan import VideoPlanRevision
from app.domain.video_plan_schema import validate_video_plan_output, video_plan_schema
from app.storage.local_store import LocalArtifactStore
from app.storage.narrative_reviews import ProjectNarrativeRecords
from app.storage.plan_script import ProjectPlanScripts
from app.storage.project_repository import ProjectRepository
from app.storage.video_plans import ProjectVideoPlans
from backend.tests.integration.test_plan_driven_script import env, generate_group
from backend.tests.unit.test_video_planning import semantic, project, FakeProvider as PlanningProvider


@pytest.fixture
def narrative(tmp_path):
    values = env(tmp_path, "standard")
    values[5].start_or_resume(values[3])
    try:
        yield values
    finally:
        values[0].close()


@pytest.mark.parametrize("problem", ["duplicate_introduction", "missing_conclusion", "early_conclusion", "opening", "synonym"])
def test_new_policy_rejects_noncanonical_structure(problem):
    payload = semantic("standard")
    if problem == "duplicate_introduction":
        payload["groups"].insert(2, deepcopy(payload["groups"][1]))
    elif problem == "missing_conclusion":
        payload["groups"].pop()
    elif problem == "early_conclusion":
        payload["groups"][2], payload["groups"][-1] = payload["groups"][-1], payload["groups"][2]
    else:
        payload["groups"][0]["kind"] = "opening" if problem == "opening" else "intro"
    with pytest.raises(ValueError):
        validate_video_plan_output(payload, "standard", narrative_policy=True)


def test_new_schema_requires_metadata_and_disallows_legacy_opening():
    schema = video_plan_schema("standard", narrative_policy=True)
    assert "narrative" in schema["required"]
    assert schema["properties"]["groups"]["minItems"] == 8
    assert "opening" not in schema["properties"]["groups"]["items"]["properties"]["kind"]["enum"]
    payload = semantic("standard")
    payload["narrative"]["chapter_progression"].pop()
    with pytest.raises(ValueError):
        validate_video_plan_output(payload, "standard", narrative_policy=True)


@pytest.mark.parametrize("target", [540, 600, 660])
def test_role_budgets_sum_exactly_and_metadata_survives_storage(tmp_path, target):
    session, plans, _ = project(tmp_path)
    try:
        plan = VideoPlanningService(plans, PlanningProvider("standard")).generate(
            project_id=session.project.id, language="en", video_format="standard", target_duration_seconds=target,
            topic="Worldwide internet outage")
        assert [g.kind for g in plan.groups] == ["cold_open", "introduction", *["chapter"] * 5, "conclusion"]
        assert [plan.groups[i].target_duration_seconds for i in (0, 1, -1)] == [20, 45, 55]
        assert sum(g.target_duration_seconds for g in plan.groups) == target
        assert all(sum(s.target_duration_seconds for s in g.sections) == g.target_duration_seconds for g in plan.groups)
        assert plans.revision(plan.id).narrative == plan.narrative
        assert plan.to_payload()["narrative_policy"] == "standard-narrative-v2"
    finally:
        session.close()


def test_legacy_plan_order_opening_and_cache_payload_remain_readable(narrative):
    _, _, _, plan, _, service, _ = narrative
    groups = list(plan.groups)
    groups[0] = replace(groups[0], kind="opening")
    groups[2], groups[-1] = groups[-1], groups[2]
    historical = replace(plan, groups=tuple(groups))
    assert VideoPlanRevision.from_payload(historical.to_payload()) == historical
    prepared = service.prepare_group(plan, plan.groups[0])
    generated = service.validate_and_cache(plan, plan.groups[0], prepared, service.generate_payload(prepared))
    legacy = generated.to_payload()
    del legacy["policy"], legacy["source_revisions"]
    restored = GeneratedScriptGroup.from_payload(legacy)
    assert restored.policy is None and restored.source_revisions == ()


def test_retained_predecessor_and_manual_revision_change_context_cache(narrative):
    session, _, _, plan, artifacts, service, _ = narrative
    generate_group(service, plan, plan.groups[0])
    hook = session.active_script.sections[0]
    session.edit_section(hook.section_id, text="Internet access disappears. What happens to Payments?")
    edited = session.active_script.sections[0]
    introduction = plan.groups[1]
    prepared = service.prepare_group(plan, introduction)
    prompt = json.loads(prepared["prompt"])
    context = prompt["narrative_context"]
    assert context["source_revisions"] == [edited.id]
    assert context["preceding_ending"] == {"revision_id": edited.id, "text": edited.text}
    assert context["committed_previous_groups"][0]["retained_excerpts"][0]["revision_id"] == edited.id
    assert "Payments" in context["literal_terminology_candidates"]
    assert any("Payments?" in q for q in context["question_excerpts_to_review"])
    assert len(context["full_plan_outline"]) == len(plan.groups)
    result = service.validate_and_cache(plan, introduction, prepared, service.generate_payload(prepared))
    assert result.source_revisions == (edited.id,)
    assert artifacts.cached_group(prepared["cache_key"]) == result
    session.edit_section(hook.section_id, text="A changed retained hook.")
    changed = service.prepare_group(plan, introduction)
    assert changed["cache_key"] != prepared["cache_key"] and changed["cached"] is None
    with pytest.raises(ValueError, match="cache key"):
        service.commit_group(plan, introduction, result, expected_active_revision_id=session.active_script.id)
    assert service.group_state(plan, introduction) == "pending"


def test_conclusion_requires_all_preceding_groups_even_with_gaps(narrative):
    session, _, _, plan, _, service, _ = narrative
    with pytest.raises(ValueError, match="all preceding"):
        service.prepare_group(plan, plan.groups[-1])
    # A retained late chapter is not evidence of the missing earlier groups.
    generate_group(service, plan, plan.groups[-2])
    with pytest.raises(ValueError, match="all preceding"):
        service.prepare_group(plan, plan.groups[-1])


def test_conclusion_context_and_selective_regeneration_preserve_approvals(narrative):
    session, store, _, plan, _, service, provider = narrative
    for group in plan.groups[:-1]:
        generate_group(service, plan, group)
    context = json.loads(service.prepare_group(plan, plan.groups[-1])["prompt"])["narrative_context"]
    assert set(context["opening_material"]) == {"cold_open", "introduction"}
    assert context["source_revisions"] == [s.id for s in session.active_script.sections]
    assert len(context["committed_previous_groups"]) == len(plan.groups) - 1
    generate_group(service, plan, plan.groups[-1])
    review = NarrativeContinuityService(service)
    for group in plan.groups:
        review.approve_group(plan, group)
    before = session.active_script
    before_history = session.repository.script_history()
    review.review(plan)
    service.regenerate_group(plan, plan.groups[-1])
    after = session.active_script
    assert after.sections[:-1] == before.sections[:-1]
    assert after.sections[-1].id != before.sections[-1].id
    assert after.sections[-1].parent_revision_id == before.sections[-1].id
    assert session.repository.get_script(before.id) == before
    assert len(session.repository.script_history()) == len(before_history) + 1
    assert all(review.group_status(plan, g) == "APPROVED" for g in plan.groups[:-1])
    assert review.group_status(plan, plan.groups[-1]) == "REVIEW"
    assert review.latest_review(plan)[2] is True
    assert all(not m.metadata.get("desktop_dependencies") for m in review.records.history("review"))
    calls = len(provider.calls)
    assert list(service.run_next_group(plan)) == [] and len(provider.calls) == calls


def test_hook_edit_marks_context_stale_without_regenerating_approved_chapters(narrative):
    session, _, _, plan, _, service, provider = narrative
    list(service.run_next_group(plan))
    review = NarrativeContinuityService(service)
    review.approve_group(plan, plan.groups[2])
    before = session.active_script
    calls = len(provider.calls)
    session.edit_section(before.sections[0].section_id, text="The hook has been deliberately edited.")
    assert session.active_script.sections[1:] == before.sections[1:]
    assert review.group_status(plan, plan.groups[2]) == "APPROVED / CONTEXT STALE"
    assert any(f["issue_type"] == "context_freshness" for f in review.deterministic_findings(plan))
    assert list(service.run_next_group(plan)) == [] and len(provider.calls) == calls


def test_review_missing_provider_is_safe_and_deterministic_review_is_offline(narrative):
    session, _, plans, plan, artifacts, service, provider = narrative
    review = NarrativeContinuityService(service)
    report = review.records.read(review.review(plan))
    assert any(f["issue_type"] == "missing_group" for f in report["findings"])
    assert provider.calls == []
    absent = NarrativeContinuityService(PlanScriptGenerationService(session, None, plans, artifacts))
    with pytest.raises(ValueError, match="editorial provider"):
        absent.prepare_editorial(plan)
    with pytest.raises(ValueError, match="Complete"):
        review.prepare_editorial(plan)
    assert provider.calls == []


def test_optional_editorial_review_validates_exact_source_excerpts_and_staleness(narrative):
    session, _, _, plan, _, service, provider = narrative
    list(service.run_next_group(plan))
    review = NarrativeContinuityService(service)
    prepared = review.prepare_editorial(plan)
    source = prepared["sources"][0]
    finding = {"issue_type": "repetition", "severity": "REVIEW", "group_id": source["group_id"],
        "section_id": source["section_id"], "revision_id": source["revision_id"],
        "supporting_excerpt": source["text"], "explanation": "Reconsider repetition.",
        "suggested_correction": "Make the hook more specific."}
    calls = len(provider.calls)
    before = session.active_script
    manifest = review.finish_editorial(plan, prepared, {"findings": [finding]})
    value = review.records.read(manifest)
    assert finding in value["findings"] and value["provider_identity"] == {"provider": "fixture", "model": "fake"}
    assert session.active_script == before and len(provider.calls) == calls
    for mutation in ({"severity": "BLOCKED"}, {"revision_id": "invented"}, {"supporting_excerpt": "invented quote"}):
        with pytest.raises(ValueError):
            review.finish_editorial(plan, prepared, {"findings": [{**finding, **mutation}]})
    session.edit_section(before.sections[0].section_id, text="An edited hook.")
    review.finish_editorial(plan, prepared, {"findings": [finding]})
    assert review.latest_review(plan)[2] is True


def test_review_artifact_checksum_and_foreign_store_checks(narrative, tmp_path):
    session, store, _, plan, _, service, _ = narrative
    review = NarrativeContinuityService(service)
    manifest = review.review(plan)
    (store.root / manifest.storage_key).write_bytes(b"corrupt")
    with pytest.raises(ValueError, match="checksum"):
        review.records.read(manifest)
    other = ProjectSession.create(tmp_path / "foreign", repository_factory=ProjectRepository, name="Foreign")
    try:
        with pytest.raises(ValueError, match="owning"):
            ProjectNarrativeRecords(other.repository, store)
    finally:
        other.close()


def test_acceptance_narrative_standard_001_restart_and_selective_correction(tmp_path):
    workspace = tmp_path / "acceptance-narrative-standard-001"
    session = ProjectSession.create(workspace, repository_factory=ProjectRepository,
        name="acceptance-narrative-standard-001")
    provider = PlanningProvider("standard")
    try:
        store = LocalArtifactStore.for_project(session.repository)
        plans = ProjectVideoPlans(session.repository, store)
        plan = VideoPlanningService(plans, provider).generate(project_id=session.project.id, language="en",
            video_format="standard", target_duration_seconds=600,
            topic="What Would Happen If the Internet Shut Down Worldwide for 24 Hours?")
        from backend.tests.integration.test_plan_driven_script import FakeProvider
        scripts_provider = FakeProvider()
        scripts = PlanScriptGenerationService(session, scripts_provider, plans, ProjectPlanScripts(session.repository, store, plans))
        scripts.start_or_resume(plan)
        for group in plan.groups:
            generate_group(scripts, plan, group)
        review = NarrativeContinuityService(scripts)
        for group in plan.groups:
            review.approve_group(plan, group)
        original = session.active_script
        weak = original.sections[0]
        session.edit_section(weak.section_id, text="Hello and subscribe.")
        review.review(plan)
        scripts.regenerate_group(plan, plan.groups[0])
        corrected = session.active_script
        review.approve_group(plan, plan.groups[0])
        assert corrected.sections[1:] == original.sections[1:]
        review.review(plan)
        calls = len(scripts_provider.calls)
    finally:
        session.close()
    reopened = ProjectSession.open(workspace, repository_factory=ProjectRepository)
    try:
        store = LocalArtifactStore.for_project(reopened.repository)
        plans = ProjectVideoPlans(reopened.repository, store)
        scripts = PlanScriptGenerationService(reopened, scripts_provider, plans, ProjectPlanScripts(reopened.repository, store, plans))
        assert reopened.active_script == corrected
        assert list(scripts.run_next_group(plans.selected())) == []
        assert len(scripts_provider.calls) == calls
        review = NarrativeContinuityService(scripts)
        assert review.group_status(plan, plan.groups[0]) == "APPROVED"
        assert review.group_status(plan, plan.groups[1]) == "APPROVED / CONTEXT STALE"
        assert review.latest_review(plan)[2] is False
        assert not any("image" in m.artifact_type for m in store.list_artifacts())
    finally:
        reopened.close()


def test_explicit_editorial_entry_calls_configured_provider_and_retains_usage(narrative):
    _, _, _, plan, _, service, provider = narrative
    list(service.run_next_group(plan))
    class Editorial:
        def generation_identity(self):
            return {"provider": "editorial-fixture", "model": "fake", "api_key": "private"}
        def last_usage(self):
            return {"input_tokens": 100, "output_tokens": 10}
        def generate_structured(self, prompt, schema):
            self.calls += 1
            assert json.loads(prompt)["task"] == "review_narrative_continuity"
            return {"findings": []}
        calls = 0
    service.provider = Editorial()
    review = NarrativeContinuityService(service)
    manifest = review.review_editorial(plan)
    report = review.records.read(manifest)
    assert service.provider.calls == 1
    assert report["usage"]["input_tokens"] == 100
    assert report["provider_identity"] == {"provider": "editorial-fixture", "model": "fake"}
    assert "api_key" not in json.dumps(report)


def test_measured_audio_contract_ignores_editorial_and_channel_branding(narrative):
    from fractions import Fraction
    from app.application.plan_duration import plan_duration_result
    from backend.tests.unit.test_plan_duration import retained_audio
    from app.desktop.pipeline_driver import DesktopPipelineDriver
    _, _, _, plan, _, service, _ = narrative
    list(service.run_next_group(plan))
    editor, config, section, _, _, _ = retained_audio(489)
    editor.video_plan.plans.selected = lambda: plan
    # Real PCM validation measures 489 seconds regardless of the 600-second word budget.
    editor.channel_intro_seconds = 80
    editor.channel_outro_seconds = 80
    result = DesktopPipelineDriver(editor, config).measure_plan_duration(plan, (section,), config)
    assert result.actual == Fraction(489) and result.state == "BLOCKED"
    review = NarrativeContinuityService(service)
    review.review(plan)
    assert result.actual != plan.target_duration_seconds


def test_selective_regeneration_preserves_user_section_order_and_media_inputs(narrative):
    from app.application.invalidation import Freshness, evaluate_freshness
    from app.domain.dependencies import DependencyDeclaration, InputEdge, RequestFingerprint, content_fingerprint
    from app.storage.artifact_index import ProjectArtifactIndex
    from app.storage.dependency_index import ArtifactDependencyIndex
    session, store, _, plan, _, service, provider = narrative
    list(service.run_next_group(plan))
    before = session.active_script
    requests, selected, retained = {}, {}, {}
    for section in before.sections:
        for kind in ("audio", "image"):
            key = f"section:{section.section_id}:{kind}"
            request = RequestFingerprint.create(kind, "fixture-v1", inputs=[InputEdge(
                "text", f"section:{section.section_id}:text", content_fingerprint(section.text))])
            manifest = store.save_artifact(kind + ".bin", b"retained media fixture", DependencyDeclaration(key, request).to_metadata())
            requests[key], selected[key], retained[key] = request, manifest.artifact_id, manifest
    # A deliberate user reorder is preserved by a localized rewrite.
    order = [s.section_id for s in before.sections]
    order[2], order[3] = order[3], order[2]
    session.reorder_sections(order, expected_active_revision_id=before.id)
    original_generate = provider.generate_structured
    def changed_text(prompt, schema):
        result = original_generate(prompt, schema)
        for slot, text in result["sections"].items():
            result["sections"][slot] = text.replace("word0", "changed")
        return result
    provider.generate_structured = changed_text
    service.regenerate_group(plan, plan.groups[-1])
    after = session.active_script
    assert [s.section_id for s in after.sections] == order
    sources = {f"section:{s.section_id}:text": content_fingerprint(s.text) for s in after.sections}
    records = ArtifactDependencyIndex(ProjectArtifactIndex(session.repository)).records()
    freshness = evaluate_freshness(requests=requests, sources=sources, selected=selected, artifacts=records)
    changed_id = before.sections[-1].section_id
    for key, state in freshness.items():
        assert state.state == (Freshness.STALE if changed_id in key else Freshness.FRESH)
        assert store.read_artifact(retained[key].storage_key) == b"retained media fixture"


def test_legacy_early_conclusion_does_not_skip_missing_chapters(narrative):
    session, _, plans, plan, _, service, _ = narrative
    groups = list(plan.groups)
    groups[2], groups[-1] = groups[-1], groups[2]
    legacy = replace(plan, id=new_id("video_plan_revision"), groups=tuple(groups))
    plans.save_revision(legacy)
    plans.select(legacy, expected_selection_id=plans.selected_id())
    service.start_or_resume(legacy, replace_current=True)
    for group in legacy.groups[:2]:
        generate_group(service, legacy, group)
    with pytest.raises(ValueError, match="all preceding"):
        service.prepare_group(legacy, legacy.groups[2])
