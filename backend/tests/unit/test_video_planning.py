from copy import deepcopy

import pytest

from app.application.projects import ProjectSession
from app.application.video_planning import VideoPlanningService
from app.domain.video_plan import VideoFormat, VideoPlanRevision
from app.domain.video_plan_schema import MissingStandardPlanGroupsError
from app.storage.local_store import LocalArtifactStore
from app.storage.project_repository import ProjectRepository
from app.storage.video_plans import ProjectVideoPlans


def semantic(fmt):
    if fmt == "social":
        kinds = ["hook", "body", "explanation", "payoff", "fact"]
        groups = [{"kind": k, "title": k.title(), "purpose": "Explain a beat", "weight": i + 1,
                   "sections": [{"title": k.title(), "role": k, "purpose": "Cover beat", "weight": 1}]}
                  for i, k in enumerate(kinds)]
    else:
        kinds = ["cold_open", "introduction", "chapter", "chapter", "chapter", "chapter", "chapter", "conclusion"]
        groups = [{"kind": k, "title": k.title(), "purpose": "Explain a beat", "weight": i + 1,
                   "sections": [{"title": k.title(), "role": k, "purpose": "Cover beat", "weight": 1},
                                *([{"title": "Detail", "role": "detail", "purpose": "Add depth", "weight": 1}] if k == "chapter" else [])]}
                  for i, k in enumerate(kinds)]
    return {"working_title": "Working title", "film_brief": "Educational", "visual_style": "Documentary",
            "groups": groups}


class FakeProvider:
    def __init__(self, fmt): self.fmt, self.schemas, self.prompts = fmt, [], []
    def generate_structured(self, prompt, schema):
        self.schemas.append(schema)
        self.prompts.append(prompt)
        return semantic(self.fmt)


def project(tmp_path):
    session = ProjectSession.create(tmp_path / "p", repository_factory=ProjectRepository, name="Test")
    store = LocalArtifactStore.for_project(session.repository)
    return session, ProjectVideoPlans(session.repository, store), store


def test_social_fake_provider_gets_application_budgets_and_application_ids(tmp_path):
    session, plans, _ = project(tmp_path)
    try:
        provider = FakeProvider("social")
        plan = VideoPlanningService(plans, provider).generate(
            project_id=session.project.id, language="en", video_format=VideoFormat.SOCIAL,
            target_duration_seconds=45, topic="Why leaves change color")
        assert sum(g.target_duration_seconds for g in plan.groups) == 45
        assert all(sum(s.target_duration_seconds for s in g.sections) == g.target_duration_seconds for g in plan.groups)
        assert plan.target_word_count == sum(s.target_word_count for g in plan.groups for s in g.sections)
        assert len({g.id for g in plan.groups} | {s.id for g in plan.groups for s in g.sections}) == 10
        assert provider.schemas[0]["additionalProperties"] is False
        assert "exactly one section" in provider.prompts[0]
        assert "4–8 flat groups" in provider.prompts[0]
        assert plans.selected() == plan
    finally:
        session.close()


def test_standard_fake_provider_allocates_nested_chapter_budgets(tmp_path):
    session, plans, _ = project(tmp_path)
    try:
        provider = FakeProvider("standard")
        plan = VideoPlanningService(plans, provider).generate(
            project_id=session.project.id, language="en", video_format="standard",
            target_duration_seconds=600, topic="How a city gets drinking water")
        assert plan.target_duration_seconds == 600 and len(plan.groups) == 8
        assert sum(g.kind == "chapter" for g in plan.groups) == 5
        assert all(len(g.sections) == 2 for g in plan.groups if g.kind == "chapter")
        assert "chapters may contain multiple planned sections" in provider.prompts[0]
        assert "cold_open, opening, introduction, chapter, conclusion" in provider.prompts[0]
        assert "as separate top-level groups, not sections inside a chapter" in provider.prompts[0]
        assert len(provider.prompts) == 1
        assert plans.selected() == plan
    finally:
        session.close()


def test_invalid_provider_payload_writes_nothing(tmp_path):
    session, plans, _ = project(tmp_path)
    try:
        class Bad:
            def generate_structured(self, *_): return {"unexpected": True}
        try:
            VideoPlanningService(plans, Bad()).generate(project_id=session.project.id, language="en",
                video_format="social", target_duration_seconds=45, topic="Topic")
        except ValueError:
            pass
        else:
            assert False, "invalid payload should fail"
        assert plans.history() == () and plans.selected() is None
    finally:
        session.close()


class SequenceProvider:
    def __init__(self, *responses):
        self.responses, self.prompts, self.schemas = responses, [], []

    def generate_structured(self, prompt, schema):
        self.prompts.append(prompt)
        self.schemas.append(schema)
        response = self.responses[len(self.prompts) - 1]
        if isinstance(response, Exception):
            raise response
        return response


def missing_standard_groups(missing):
    payload = semantic("standard")
    if len(missing) == 1:
        payload["groups"] = [g for g in payload["groups"] if g["kind"] not in missing]
    else:
        for group in payload["groups"]:
            if group["kind"] in missing:
                group["kind"] = "chapter"
    return payload


def generate_standard(session, plans, provider):
    return VideoPlanningService(plans, provider).generate(
        project_id=session.project.id, language="en", video_format="standard",
        target_duration_seconds=600, topic="How a city gets drinking water")


@pytest.mark.parametrize("missing", [{"introduction"}, {"conclusion"}, {"introduction", "conclusion"}])
def test_standard_missing_groups_retry_once_and_select_complete_replacement(tmp_path, missing):
    session, plans, _ = project(tmp_path)
    try:
        invalid = missing_standard_groups(missing)
        snapshot = deepcopy(invalid)
        replacement = semantic("standard")
        replacement["working_title"] = "Replacement title"
        provider = SequenceProvider(invalid, replacement)
        plan = generate_standard(session, plans, provider)
        assert len(provider.prompts) == 2
        assert provider.schemas[0] == provider.schemas[1]
        assert "complete replacement plan" in provider.prompts[1]
        assert "not a patch or partial response" in provider.prompts[1]
        assert invalid == snapshot
        assert plan.working_title == "Replacement title"
        assert [g.kind for g in plan.groups] == [g["kind"] for g in replacement["groups"]]
        assert VideoPlanRevision.from_payload(plan.to_payload()) == plan
        assert len(plans.history()) == 1 and plans.selected() == plan
    finally:
        session.close()


@pytest.mark.parametrize("failure", ["missing", "partial", "provider", "allocation"])
def test_standard_corrective_failure_never_retries_again_or_persists(tmp_path, failure):
    session, plans, _ = project(tmp_path)
    try:
        replacement = missing_standard_groups({"conclusion"})
        if failure == "partial":
            replacement = {"groups": []}
        elif failure == "provider":
            replacement = RuntimeError("Provider unavailable")
        elif failure == "allocation":
            replacement = semantic("standard")
            replacement["groups"][0]["weight"] = 1_000_000
        provider = SequenceProvider(missing_standard_groups({"introduction"}), replacement)
        with pytest.raises((ValueError, RuntimeError)):
            generate_standard(session, plans, provider)
        assert len(provider.prompts) == 2
        assert plans.history() == () and plans.selected() is None
    finally:
        session.close()


@pytest.mark.parametrize("failure", ["count", "chapters", "opening", "weight", "malformed",
                                         "provider", "provider_value_error", "provider_missing_error"])
def test_standard_unrelated_failures_do_not_retry(tmp_path, failure):
    session, plans, _ = project(tmp_path)
    try:
        payload = semantic("standard")
        if failure == "count":
            payload["groups"] = payload["groups"][:3]
        elif failure == "chapters":
            payload["groups"][2]["kind"] = "opening"
        elif failure == "opening":
            payload["groups"][0]["kind"] = "chapter"
        elif failure == "weight":
            payload["groups"][0]["weight"] = 0
        elif failure == "malformed":
            del payload["groups"][2]["sections"]
        elif failure == "provider":
            payload = RuntimeError("Provider unavailable")
        elif failure == "provider_value_error":
            payload = ValueError("Standard plan requires an introduction and conclusion.")
        elif failure == "provider_missing_error":
            payload = MissingStandardPlanGroupsError("Provider error")
        provider = SequenceProvider(payload)
        with pytest.raises((ValueError, RuntimeError)):
            generate_standard(session, plans, provider)
        assert len(provider.prompts) == 1
        assert plans.history() == () and plans.selected() is None
    finally:
        session.close()


def test_invalid_social_response_does_not_retry(tmp_path):
    session, plans, _ = project(tmp_path)
    try:
        payload = semantic("social")
        payload["groups"][0]["kind"] = "body"
        provider = SequenceProvider(payload)
        with pytest.raises(ValueError, match="Social plan must begin"):
            VideoPlanningService(plans, provider).generate(
                project_id=session.project.id, language="en", video_format="social",
                target_duration_seconds=45, topic="Topic")
        assert len(provider.prompts) == 1
        assert plans.history() == () and plans.selected() is None
    finally:
        session.close()


def test_failed_standard_correction_preserves_existing_plan_selection(tmp_path):
    session, plans, _ = project(tmp_path)
    try:
        original = generate_standard(session, plans, FakeProvider("standard"))
        history = plans.history()
        selection_id = plans.selected_id()
        provider = SequenceProvider(missing_standard_groups({"introduction"}),
                                    missing_standard_groups({"conclusion"}))
        with pytest.raises(MissingStandardPlanGroupsError):
            generate_standard(session, plans, provider)
        assert len(provider.prompts) == 2
        assert plans.history() == history
        assert plans.selected_id() == selection_id and plans.selected() == original
    finally:
        session.close()
