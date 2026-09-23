"""Render one frozen timeline through injected process/artifact ports and D040."""

import asyncio
import json
from typing import Protocol

from app.domain.caption_track import PublishedCaptionTrack
from app.domain.generation_job import AttemptStatus, JobProgress
from app.domain.render_result import OPERATION, render_request
from app.domain.timeline import TimelineRevision


class RendererPort(Protocol):
    def identity(self): ...
    async def render(self, timeline, root, *, captions=None, canceled, progress): ...


class VideoRenderService:
    def __init__(self, publication, coordinator, artifacts, renderer: RendererPort):
        self.publication, self.coordinator, self.artifacts, self.renderer = publication, coordinator, artifacts, renderer

    def enqueue(self, timeline, *, captions=None):
        request = render_request(timeline, self.renderer.identity(), captions)
        self.artifacts.current(timeline)
        return self.publication.enqueue("project:video_render", request,
            expected_sections={c.media.section_id: c.media.section_revision_id for c in timeline.clips})

    async def run(self, claim):
        existing = self.publication.repository.result(claim)
        if existing is not None:
            return existing
        job = self.coordinator.repository.get_job(claim.job_id)
        if job.request.operation != OPERATION or job.request.algorithm_version != "1":
            raise ValueError("Video service requires a timeline render job.")

        def canceled():
            return self.coordinator.repository.get_attempt(claim.id).cancel_requested

        def progress(phase, completed, total):
            self.coordinator.progress(claim, JobProgress(phase, completed, total))

        try:
            self.publication.repository.prepare(claim)
            settings = json.loads(job.request.settings_json)
            timeline = TimelineRevision.from_payload(settings["timeline"])
            captions = (PublishedCaptionTrack.from_payload(settings["captions"])
                        if settings.get("captions") is not None else None)
            identity = json.loads(job.request.effective_identity_json)
            if self.renderer.identity() != identity:
                raise ValueError("Renderer executable identity changed after enqueue.")
            root = self.artifacts.stage(timeline, captions=captions)
            result = await self.renderer.render(
                timeline, root, captions=captions, canceled=canceled, progress=progress)
            if self.renderer.identity() != identity:
                raise ValueError("Renderer executable identity changed during execution.")
            with self.artifacts.output(root, timeline, result) as (source, metadata):
                return self.publication.publish(claim, "render.mp4", source, metadata=metadata)
        except BaseException as exc:
            actual = self.coordinator.repository.get_attempt(claim.id)
            if actual.status == AttemptStatus.COMPLETED:
                return self.publication.repository.result(claim)
            if actual.status == AttemptStatus.RUNNING:
                if isinstance(exc, asyncio.CancelledError) and not actual.cancel_requested:
                    self.coordinator.cancel(claim.id)
                if canceled():
                    self.coordinator.acknowledge_cancel(claim)
                else:
                    self.coordinator.fail(claim, f"video_render: {type(exc).__name__}: {str(exc)[-1024:]}")
            raise
