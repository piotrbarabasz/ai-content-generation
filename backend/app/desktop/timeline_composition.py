"""Optional local composition, with no provider construction or media generation."""

from app.application.timeline import TimelineSceneInput
from app.application.timeline_editing import TimelineEditingService
from app.jobs.repository import JobRepository
from app.storage.local_store import LocalArtifactStore
from app.storage.result_publication import ResultArtifactIndex
from app.storage.timeline import ProjectTimelineMedia
from app.storage.timeline_edits import ProjectTimelineEdits


class TimelineServices(TimelineEditingService):
    def candidates(self):
        candidates = []
        for manifest in self.media.store.list_artifacts():
            if manifest.artifact_type != "desktop_scene_timing":
                continue
            timing = self.media.plans.timing(manifest.metadata["value_id"])
            for scene in timing.scenes:
                for variant in ("original", "processed"):
                    source = TimelineSceneInput(timing.id, scene.scene_id, variant)
                    try:
                        media = self.media.resolve(source)
                    except ValueError:
                        continue  # Historical/nonselected inputs are not new clip candidates.
                    section = self.media.repository.get_section(media.section_revision_id)
                    candidates.append((f"{section.title} / {scene.scene_id} / {variant}", source))
        return tuple(candidates)


def compose_timeline(session):
    index = ResultArtifactIndex(session.repository, JobRepository(session.repository))
    store = LocalArtifactStore(index.root, index=index)
    store.recovery_report = store.recover()
    return TimelineServices(session.project.id, ProjectTimelineEdits(session.repository, store),
                            ProjectTimelineMedia(index, store))
