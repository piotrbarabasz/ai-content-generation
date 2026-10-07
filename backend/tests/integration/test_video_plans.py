import pytest

from app.application.projects import ProjectSession
from app.application.video_planning import VideoPlanningService
from app.domain.video_plan import VideoFormat
from app.storage.local_store import LocalArtifactStore
from app.storage.project_repository import ProjectRepository
from app.storage.video_plans import ProjectVideoPlans


class Provider:
    def generate_structured(self, *_):
        kinds = ["hook", "body", "explanation", "payoff"]
        return {"working_title": "Title", "film_brief": "Brief", "visual_style": "Style",
                "groups": [{"kind": k, "title": k, "purpose": "Purpose", "weight": 1,
                            "sections": [{"title": k, "role": k, "purpose": "Purpose", "weight": 1}]}
                           for k in kinds]}


def test_plan_and_selection_survive_reopen_and_chain_cannot_branch(tmp_path):
    session = ProjectSession.create(tmp_path / "project", repository_factory=ProjectRepository, name="P")
    project_id = session.project.id
    plans = ProjectVideoPlans(session.repository, LocalArtifactStore.for_project(session.repository))
    first = VideoPlanningService(plans, Provider()).generate(project_id=project_id, language="en",
        video_format=VideoFormat.SOCIAL, target_duration_seconds=45, topic="Topic")
    assert session.repository._connection.execute("PRAGMA user_version").fetchone()[0] == 2
    first_selection = plans.selected_id()
    second = VideoPlanningService(plans, Provider()).generate(project_id=project_id, language="en",
        video_format=VideoFormat.SOCIAL, target_duration_seconds=50, topic="Topic updated",
        parent_revision_id=first.id)
    assert plans.selected() == second
    with pytest.raises(ValueError, match="selection changed"):
        plans.select(first, expected_selection_id=first_selection)
    session.close()

    reopened = ProjectSession.open(tmp_path / "project", repository_factory=ProjectRepository)
    try:
        retained = ProjectVideoPlans(reopened.repository, LocalArtifactStore.for_project(reopened.repository))
        assert retained.selected() == second
        assert [p.id for p in retained.history()] == [first.id, second.id]
        # Existing projects keep their optional, empty script state.
        assert reopened.active_script.sections == ()
    finally:
        reopened.close()


def test_wrong_project_and_corrupted_artifact_are_rejected(tmp_path):
    a = ProjectSession.create(tmp_path / "a", repository_factory=ProjectRepository, name="A")
    b = ProjectSession.create(tmp_path / "b", repository_factory=ProjectRepository, name="B")
    plan = VideoPlanningService(ProjectVideoPlans(a.repository, LocalArtifactStore.for_project(a.repository)), Provider()).generate(
        project_id=a.project.id, language="en", video_format="social", target_duration_seconds=45, topic="Topic")
    b_plans = ProjectVideoPlans(b.repository, LocalArtifactStore.for_project(b.repository))
    with pytest.raises(ValueError, match="different project"):
        b_plans.save_revision(plan)
    with pytest.raises(ValueError, match="different project workspace"):
        ProjectVideoPlans(a.repository, LocalArtifactStore.for_project(b.repository))
    a_store = LocalArtifactStore.for_project(a.repository)
    plans = ProjectVideoPlans(a.repository, a_store)
    manifest = next(m for m in a_store.list_artifacts() if m.metadata.get("value_id") == plan.id)
    path = a_store.root / manifest.storage_key
    path.write_bytes(path.read_bytes() + b" ")
    with pytest.raises(ValueError, match="checksum"):
        plans.revision(plan.id)
    a.close(); b.close()


def test_failed_selection_publication_preserves_previous_head(tmp_path, monkeypatch):
    session = ProjectSession.create(tmp_path / "p", repository_factory=ProjectRepository, name="P")
    try:
        store = LocalArtifactStore.for_project(session.repository)
        plans = ProjectVideoPlans(session.repository, store)
        service = VideoPlanningService(plans, Provider())
        first = service.generate(project_id=session.project.id, language="en", video_format="social",
                                 target_duration_seconds=45, topic="First")
        active = plans.selected_id()
        second = service.generate
        original_save = store.save_artifact
        def fail_selection(name, content, metadata=None):
            if metadata and metadata.get("artifact_type") == ProjectVideoPlans.SELECTION:
                raise OSError("controlled publication failure")
            return original_save(name, content, metadata)
        monkeypatch.setattr(store, "save_artifact", fail_selection)
        # Saving a new immutable revision succeeds; only selection publication fails.
        from app.domain.base import new_id
        from dataclasses import replace
        candidate = replace(first, id=new_id("video_plan_revision"), parent_revision_id=first.id)
        plans.save_revision(candidate)
        with pytest.raises(OSError):
            plans.select(candidate, expected_selection_id=active)
        assert plans.selected_id() == active and plans.selected() == first
    finally:
        session.close()
