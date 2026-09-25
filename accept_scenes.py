from pathlib import Path

from app.application.projects import ProjectSession
from app.application.scene_planning import ScenePlanningService
from app.storage.local_store import LocalArtifactStore
from app.storage.project_repository import ProjectRepository
from app.storage.scene_plans import ProjectScenePlans
from app.tts.scene_sources import sentence_sources


PROJECT = Path(
    r"D:\Projects\ai-content-generation\test-gps-en-2"
)


with ProjectSession.open(
    PROJECT,
    repository_factory=ProjectRepository,
) as session:

    store = LocalArtifactStore.for_project(
        session.repository
    )

    plans = ProjectScenePlans(
        session.repository,
        store,
    )

    service = ScenePlanningService(
        plans,
        sentence_sources,
    )

    for section in session.active_script.sections:

        proposals = [
            proposal
            for proposal in plans.proposals(section.section_id)
            if proposal.revision_id == section.id
        ]

        if not proposals:
            print(f"{section.title}: NO PROPOSAL")
            continue

        proposal = proposals[-1]

        existing = [
            acceptance
            for acceptance in plans.acceptances(section.section_id)
            if acceptance.plan.id == proposal.id
        ]

        if existing:
            print(
                f"{section.title}: already accepted "
                f"({len(proposal.scenes)} scenes)"
            )
            continue

        accepted = service.accept(
            proposal.id,
            reviewer_id="manual-product-test",
        )

        print(
            f"{section.title}: ACCEPTED "
            f"{len(proposal.scenes)} scenes "
            f"({accepted.id})"
        )