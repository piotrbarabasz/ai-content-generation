"""Bind existing desktop services without installing or selecting providers."""

from pathlib import Path

from app.application.result_publication import ResultPublicationService
from app.application.video_render import VideoRenderService
from app.jobs.coordinator import JobCoordinator
from app.jobs.repository import JobRepository
from app.storage.local_store import LocalArtifactStore
from app.storage.regeneration import ProjectRegeneration
from app.storage.video_render import RenderResultIndex, ProjectVideoRender
from app.desktop.deployment import media_executables


def compose_regeneration(session, audio, scenes, timeline, preview, audio_selection, *,
                         ffmpeg=None, ffprobe=None, process=None):
    jobs = JobRepository(session.repository)
    index = RenderResultIndex(session.repository, jobs)
    store = LocalArtifactStore(index.root, index=index)
    render = None
    render_error = ""
    try:
        from app.providers.ffmpeg_render import FFmpegRenderer
        from app.storage.scene_motion import ProjectMotionSettings
        located = media_executables()
        ffmpeg, ffprobe = ffmpeg or located[0], ffprobe or located[1]
        if not ffmpeg or not ffprobe:
            raise ValueError("FFmpeg and ffprobe are required. Install/configure both, then refresh render availability.")
        if not Path(ffmpeg).is_file() or not Path(ffprobe).is_file():
            raise ValueError("FFmpeg/ffprobe executable files are missing. Correct the installation and refresh render availability.")
        settings = ProjectMotionSettings(store, session.project.id)
        renderer = FFmpegRenderer(ffmpeg, ffprobe, process=process, motion_settings=settings.current)
        render = VideoRenderService(ResultPublicationService(index, store), JobCoordinator(jobs),
                                    ProjectVideoRender(index, store), renderer)
    except (ValueError, OSError, RuntimeError) as exc:
        render_error = f"Final render service unavailable: {exc}"

    async def run_audio(attempt):
        await audio.supervisor.run_next("desktop-regeneration")

    # Cloud narration has its own approved async dispatch. The legacy regeneration
    # port is optional; retained audio remains consumable without any generator.
    production = getattr(audio, "production", None)
    adapter = ProjectRegeneration(session, index, store,
        audio=production, audio_selection=audio_selection if production else None,
        run_audio=run_audio if production else None, scenes=scenes, timeline=timeline, preview=preview, render=render)
    adapter.render_error = render_error
    adapter.service.final_render = adapter
    return adapter.service
