"""Optional local composition, with no provider construction or media generation."""

from dataclasses import dataclass

from app.application.timeline import TimelineSceneInput
from app.application.timeline_editing import TimelineEditingService
from app.application.timeline import TimelineCompiler
from app.domain.timeline import OutputTimebase
from app.jobs.repository import JobRepository
from app.storage.local_store import LocalArtifactStore
from app.storage.result_publication import ResultArtifactIndex
from app.storage.timeline import ProjectTimelineMedia
from app.storage.timeline_edits import ProjectTimelineEdits


class TimelineServices(TimelineEditingService):
    @dataclass(frozen=True)
    class CandidateDiagnostic:
        source: TimelineSceneInput | None
        section_id: str | None
        section_title: str
        timing_id: str | None
        scene_id: str
        audio_variant: str
        accepted: bool
        reason: str | None = None
        media: object | None = None

    def _diagnose(self, source, *, section_id=None, section_title=""):
        try:
            media = self.media.resolve(source)
        except (ValueError, OSError, KeyError) as exc:
            return self.CandidateDiagnostic(source, section_id, section_title, source.timing_id,
                                            source.scene_id, source.audio_variant, False, str(exc))
        try:
            if not section_id:
                section = self.media.repository.get_section(media.section_revision_id)
                section_id, section_title = section.section_id, section.title
        except (ValueError, OSError, KeyError) as exc:
            return self.CandidateDiagnostic(source, section_id, section_title, source.timing_id,
                                            source.scene_id, source.audio_variant, False, str(exc))
        return self.CandidateDiagnostic(source, section_id, section_title, source.timing_id,
                                        source.scene_id, source.audio_variant, True, None, media)

    def candidate_diagnostics(self):
        """Diagnose all retained candidates through the same resolver used by compilation."""
        diagnostics = []
        manifests = sorted(self.media.store.list_artifacts(), key=lambda item: (item.created_at, item.artifact_id))
        for manifest in manifests:
            if manifest.artifact_type != "desktop_scene_timing":
                continue
            timing = self.media.plans.timing(manifest.metadata["value_id"])
            for scene in timing.scenes:
                for variant in ("original", "processed"):
                    source = TimelineSceneInput(timing.id, scene.scene_id, variant)
                    diagnostics.append(self._diagnose(source))
        return tuple(diagnostics)

    def current_candidate_diagnostics(self, *, audio_variant="original"):
        """Return current accepted scenes in saved-script and accepted-plan order."""
        if audio_variant not in ("original", "processed"):
            raise ValueError("Choose original or processed audio explicitly.")
        diagnostics = []
        snapshot = self.media.repository.active_script()
        manifests = sorted(self.media.store.list_artifacts(), key=lambda item: (item.created_at, item.artifact_id))
        timings = [self.media.plans.timing(item.metadata["value_id"]) for item in manifests
                   if item.artifact_type == "desktop_scene_timing"]
        for section in snapshot.sections:
            accepted = [value for value in self.media.plans.acceptances(section.section_id)
                        if value.plan.revision_id == section.id]
            if not accepted:
                continue
            plan = accepted[-1]
            current_timings = [value for value in timings if value.acceptance_id == plan.id]
            timing = current_timings[-1] if current_timings else None
            for scene in plan.plan.scenes:
                if timing is None:
                    diagnostics.append(self.CandidateDiagnostic(
                        None, section.section_id, section.title, None, scene.id, audio_variant, False,
                        "No scene timing set for the current accepted plan."))
                    continue
                source = TimelineSceneInput(timing.id, scene.id, audio_variant)
                diagnostics.append(self._diagnose(source, section_id=section.section_id,
                                                  section_title=section.title))
        return tuple(diagnostics)

    def candidates(self):
        return tuple((f"{item.section_title} / {item.scene_id} / {item.audio_variant}", item.source)
                     for item in self.candidate_diagnostics() if item.accepted)

    def rebuild_from_sources(self, sources, *, expected=None, timebase=None, fit_policy="fit"):
        previous = self.current()
        if (previous.id if previous else None) != expected:
            raise ValueError("Timeline changed; refresh before rebuilding.")
        compiled = TimelineCompiler(self.media).compile(self.project_id, tuple(sources),
            timebase=timebase or (previous.timeline.timebase if previous else OutputTimebase()),
            fit_policy=previous.timeline.fit_policy if previous else fit_policy)
        return self.history.save(compiled, previous.id if previous else None)


def compose_timeline(session):
    index = ResultArtifactIndex(session.repository, JobRepository(session.repository))
    store = LocalArtifactStore(index.root, index=index)
    store.recovery_report = store.recover()
    return TimelineServices(session.project.id, ProjectTimelineEdits(session.repository, store),
                            ProjectTimelineMedia(index, store))
