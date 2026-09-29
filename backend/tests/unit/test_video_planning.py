from app.application.projects import ProjectSession
from app.application.video_planning import VideoPlanningService
from app.domain.video_plan import VideoFormat
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
