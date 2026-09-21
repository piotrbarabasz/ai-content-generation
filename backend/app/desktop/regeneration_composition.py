"""Bind existing desktop services without installing or selecting providers."""

from app.application.result_publication import ResultPublicationService
from app.application.video_render import VideoRenderService
from app.jobs.coordinator import JobCoordinator
from app.jobs.repository import JobRepository
from app.storage.local_store import LocalArtifactStore
from app.storage.regeneration import ProjectRegeneration
from app.storage.video_render import RenderResultIndex, ProjectVideoRender


def compose_regeneration(session, audio, scenes, timeline, preview, audio_selection):
    jobs = JobRepository(session.repository)
    index = RenderResultIndex(session.repository, jobs)
    store = LocalArtifactStore(index.root, index=index)
    render = None
    if preview is not None:
        from app.providers.ffmpeg_render import FFmpegRenderer
        renderer = FFmpegRenderer(preview.renderer.ffmpeg, preview.renderer.ffprobe)
        render = VideoRenderService(ResultPublicationService(index, store), JobCoordinator(jobs),
                                    ProjectVideoRender(index, store), renderer)

    async def run_audio(attempt):
        await audio.supervisor.run_next("desktop-regeneration")

    adapter = ProjectRegeneration(session, index, store,
        audio=audio.production if audio else None, audio_selection=audio_selection if audio else None,
        run_audio=run_audio if audio else None, scenes=scenes, timeline=timeline, preview=preview, render=render)
    return adapter.service
