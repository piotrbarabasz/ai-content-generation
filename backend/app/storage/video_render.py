"""Verified render inputs and immutable publication of selected D018 snapshots."""

from contextlib import contextmanager
from dataclasses import replace
from hashlib import file_digest
import json
import shutil
from uuid import uuid4

from PIL import Image, ImageOps

from app.application.timeline import TimelineCompiler, TimelineSceneInput
from app.domain.dependencies import artifact_fingerprint
from app.domain.publication import PUBLICATION_KEY, PublicationConflictError
from app.domain.render_result import OPERATION, RenderedVideo, render_request
from app.domain.timeline import AudioSpan, TimelineMedia, TimelineRevision
from .local_store import LocalArtifactStore
from .paths import contained_path
from .result_publication import ResultArtifactIndex
from .timeline import ProjectTimelineMedia


def timeline_inputs(timeline):
    return tuple(TimelineSceneInput(c.media.timing_id, c.media.scene_id, c.media.audio.variant) for c in timeline.clips)


class RenderResultIndex(ResultArtifactIndex):
    def __init__(self, repository, jobs, **kwargs):
        super().__init__(repository, jobs, **kwargs)
        self.media = ProjectTimelineMedia(self, LocalArtifactStore(self.root, index=self))

    def register(self, manifest):
        if PUBLICATION_KEY in manifest.metadata and manifest.artifact_type == "video_render":
            evidence = manifest.metadata["render"]
            if evidence["checksum"] != manifest.checksum or evidence["size_bytes"] != manifest.size_bytes:
                raise PublicationConflictError("Published MP4 bytes differ from decoded render evidence.")
        return super().register(manifest)

    def _artifact_inputs_match(self, connection, request, visiting=frozenset()):
        if request.operation != OPERATION:
            return super()._artifact_inputs_match(connection, request, visiting)
        timeline = TimelineRevision.from_payload(json.loads(request.settings_json)["timeline"])
        expected = render_request(timeline, json.loads(request.effective_identity_json))
        artifact_edges = tuple(e for e in request.inputs if e.artifact_id is not None)
        if (artifact_edges != expected.inputs or request.algorithm_version != expected.algorithm_version
                or request.settings_json != expected.settings_json):
            raise PublicationConflictError("Render inputs differ from the exact timeline.")
        for edge in artifact_edges:
            row = connection.execute("SELECT checksum FROM artifacts WHERE artifact_id=?", (edge.artifact_id,)).fetchone()
            if row is None or artifact_fingerprint(edge.artifact_id, row[0]) != edge.fingerprint:
                raise PublicationConflictError("Timeline artifact is missing or changed in the catalog.")
        # An explicit image choice authorizes a retained variant, independent of
        # newer candidate heads or old prompt freshness. D018 choices are the gate.
        remaining = replace(request, inputs=tuple(e for e in request.inputs if e.artifact_id is None))
        if not super()._artifact_inputs_match(connection, remaining, visiting):
            return False
        try:
            for source, clip in zip(timeline_inputs(timeline), timeline.clips):
                selected = self.media.resolve(source)
                full = selected.audio
                normalized = replace(clip.media, audio=replace(clip.media.audio,
                                                               start_sample=full.start_sample,
                                                               end_sample=full.end_sample))
                boundaries = self.media.boundaries(clip.media)
                if (normalized != selected or clip.media.audio.start_sample not in boundaries
                        or clip.media.audio.end_sample not in boundaries):
                    return False
            return True
        except (ValueError, FileNotFoundError):
            return False


class ProjectVideoRender:
    def __init__(self, index, store):
        if not isinstance(index, RenderResultIndex) or store._index is not index:
            raise ValueError("Render publication requires the owning render-aware index/store.")
        self.index, self.store, self.media = index, store, index.media

    def current(self, timeline):
        compiled = TimelineCompiler(self.media).compile(self.index.project_id, timeline_inputs(timeline),
                                                       timebase=timeline.timebase, fit_policy=timeline.fit_policy)
        if len(compiled.clips) != len(timeline.clips):
            raise ValueError("Render requires the exact currently selected timeline inputs.")
        for selected, clip in zip(compiled.clips, timeline.clips):
            full = selected.media.audio
            normalized = replace(clip.media, audio=replace(clip.media.audio,
                                                           start_sample=full.start_sample,
                                                           end_sample=full.end_sample))
            boundaries = self.media.boundaries(clip.media)
            if (normalized != selected.media or clip.media.audio.start_sample not in boundaries
                    or clip.media.audio.end_sample not in boundaries):
                raise ValueError("Render requires the exact currently selected timeline inputs.")

    def stage(self, timeline):
        # A unique private directory per invocation retains failed bytes for
        # diagnosis and cannot overwrite a previous attempt or source artifact.
        workspace = self.index.repository.workspace
        root = contained_path(workspace, "work/render/" + uuid4().hex)
        root.mkdir(parents=True)
        for i, clip in enumerate(timeline.clips):
            self._stage_clip(root, i, clip)
        return root

    def stage_scene(self, timeline, scene_id):
        self.current(timeline)
        clip = next((value for value in timeline.clips if value.media.scene_id == scene_id), None)
        if clip is None:
            raise ValueError("Scene is not present in the selected timeline.")
        root = contained_path(self.index.repository.workspace, "work/preview/" + uuid4().hex)
        root.mkdir(parents=True)
        self._stage_clip(root, 0, clip)
        return root, clip

    def _stage_clip(self, root, index, clip):
        media = clip.media
        timing = self.media.plans.timing(media.timing_id)
        accepted = self.media.plans.acceptance(timing.acceptance_id)
        span = next(s for s in timing.scenes if s.scene_id == media.scene_id)
        image = self.media.images.image(media.image.artifact_id)
        selection = next(s for s in self.media.images.selection_history(media.scene_id) if s.id == media.image_selection_id)
        _, audio, _ = self.media.audio._read(media.audio.artifact_id)
        retained = TimelineMedia(self.index.project_id, accepted.plan.section_id, accepted.plan.revision_id,
            span.scene_id, accepted.plan.id, accepted.id, timing.id, timing.quality, selection.id, image,
            AudioSpan(audio.artifact_id, audio.checksum, media.audio.variant, audio.sample_rate,
                      audio.frame_count, span.start_frame, span.end_frame))
        # D023 may narrow a scene to verified sentence boundaries. Preserve that
        # exact range after validating the retained D013 parent span.
        if (retained != replace(media, audio=replace(media.audio, start_sample=span.start_frame,
                                                     end_sample=span.end_frame))
                or not span.start_frame <= media.audio.start_sample < media.audio.end_sample <= span.end_frame
                or selection.artifact_id != image.artifact_id
                or (timing.audio_artifact_id, timing.audio_checksum, timing.sample_rate, timing.frame_count)
                != (audio.artifact_id, audio.checksum, audio.sample_rate, audio.frame_count)):
            raise ValueError("Retained media differs from the enqueue timeline snapshot.")
        for kind, artifact_id, expected in (("image", image.artifact_id, image.checksum),
                                             ("audio", audio.artifact_id, audio.checksum)):
            key = f"{kind}-{index}.source" if kind == "image" else f"audio-{index}.wav"
            path = contained_path(root, key)
            with self.store.open_artifact_id(artifact_id) as source, path.open("xb") as target:
                shutil.copyfileobj(source, target, 1024 * 1024)
            with contained_path(root, key).open("rb") as copied:
                if file_digest(copied, "sha256").hexdigest() != expected:
                    raise ValueError("Source changed while staging render inputs.")
        # Apply EXIF orientation and deterministic black alpha composition.
        with Image.open(contained_path(root, f"image-{index}.source")) as original:
            oriented = ImageOps.exif_transpose(original).convert("RGBA")
            background = Image.new("RGBA", oriented.size, "black")
            background.alpha_composite(oriented)
            background.convert("RGB").save(contained_path(root, f"image-{index}.png"), format="PNG")

    @contextmanager
    def output(self, root, timeline, result):
        if not isinstance(result, RenderedVideo) or result.timeline_id != timeline.id or result.frame_count != timeline.total_frames:
            raise ValueError("Render evidence belongs to another timeline.")
        path = contained_path(root, "render.mp4")
        with path.open("rb") as source:
            if file_digest(source, "sha256").hexdigest() != result.checksum or path.stat().st_size != result.size_bytes:
                raise ValueError("Render bytes changed after validation.")
            source.seek(0)
            yield source, {"artifact_type": "video_render", "module_name": "desktop_video_render",
                           "project_id": timeline.project_id, "render": result.to_payload()}
