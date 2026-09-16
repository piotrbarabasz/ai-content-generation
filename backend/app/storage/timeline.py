"""Read-only bridge from D011/D013/D016 selections to exact timeline inputs."""

from app.domain.timeline import AudioSpan, TimelineMedia
from .scene_images import ProjectSceneImages
from .section_tempo import SectionTempoArtifacts


class ProjectTimelineMedia:
    def boundaries(self, media):
        """Only measured sentence edges; approximate timing permits no new cut."""
        timing = self.plans.timing(media.timing_id)
        accepted = self.plans.acceptance(timing.acceptance_id)
        span = next((s for s in timing.scenes if s.scene_id == media.scene_id), None)
        _, audio, _ = self.audio._read(media.audio.artifact_id)
        if (span is None or media.project_id != self.repository.project().id
                or (media.plan_id, media.acceptance_id, media.section_id, media.section_revision_id)
                != (accepted.plan.id, accepted.id, accepted.plan.section_id, accepted.plan.revision_id)
                or (audio.section_id, audio.revision_id) != (media.section_id, media.section_revision_id)
                or (timing.audio_artifact_id, timing.audio_checksum, timing.sample_rate, timing.frame_count)
                != (audio.artifact_id, audio.checksum, audio.sample_rate, audio.frame_count)
                or (media.audio.checksum, media.audio.sample_rate, media.audio.frame_count)
                != (audio.checksum, audio.sample_rate, audio.frame_count)):
            raise ValueError("Timeline source differs from retained timing/audio.")
        mapping = audio.speech_boundary_map
        if mapping is None or mapping.quality != "measured_sentence_blocks":
            return (span.start_frame, span.end_frame)
        section = self.repository.get_section(media.section_revision_id)
        mapping.validate_source(section.text, audio.checksum, audio.sample_rate, audio.frame_count)
        edges = {edge for block in mapping.blocks for edge in (block.start_frame, block.end_frame)}
        return tuple(sorted(edge for edge in edges if span.start_frame <= edge <= span.end_frame))

    def __init__(self, index, store):
        self.store = store
        self.images = ProjectSceneImages(index.repository, store)
        self.plans = self.images.scenes
        self.audio = SectionTempoArtifacts(index, store)
        self.repository = index.repository

    def resolve(self, source):
        timing = self.plans.timing(source.timing_id)
        accepted = self.plans.acceptance(timing.acceptance_id)
        section = self.repository.get_section(accepted.plan.revision_id)
        self.plans.current(section)
        span = next((span for span in timing.scenes if span.scene_id == source.scene_id), None)
        if span is None:
            raise ValueError("Requested scene does not belong to the explicit timing set.")
        audio = self.audio.selected(section, source.audio_variant)
        if audio is None:
            raise ValueError("Timeline audio selection is missing or stale; no variant fallback is allowed.")
        if (timing.audio_artifact_id, timing.audio_checksum, timing.sample_rate, timing.frame_count) != (
                audio.artifact_id, audio.checksum, audio.sample_rate, audio.frame_count):
            raise ValueError("Timing does not describe the exact selected audio; explicitly retime first.")
        selection = self.images.selected(source.scene_id)
        if selection is None:
            raise ValueError("Timeline scene has no selected image.")
        image = self.images.image(selection.artifact_id)
        return TimelineMedia(self.plans.project_id, section.section_id, section.id, source.scene_id,
                             accepted.plan.id, accepted.id, timing.id, timing.quality, selection.id, image,
                             AudioSpan(audio.artifact_id, audio.checksum, source.audio_variant, audio.sample_rate,
                                       audio.frame_count, span.start_frame, span.end_frame))
