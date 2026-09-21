"""Optional local preview composition; no generation provider is constructed."""

from app.application.preview import PreviewService
from app.desktop.deployment import media_executables
from app.jobs.repository import JobRepository
from app.providers.ffmpeg_render import FFmpegRenderer
from app.storage.local_store import LocalArtifactStore
from app.storage.preview import ProjectPreviewMedia
from app.storage.video_render import ProjectVideoRender, RenderResultIndex


def compose_preview(session, timeline_services, *, ffmpeg=None, ffprobe=None, process=None):
    if not ffmpeg or not ffprobe:
        located_ffmpeg, located_ffprobe = media_executables()
        ffmpeg = ffmpeg or located_ffmpeg
        ffprobe = ffprobe or located_ffprobe
    if not ffmpeg or not ffprobe:
        return None
    jobs = JobRepository(session.repository)
    index = RenderResultIndex(session.repository, jobs)
    store = LocalArtifactStore(index.root, index=index)
    renderer = FFmpegRenderer(ffmpeg, ffprobe, process=process, proxy=True)
    media = ProjectPreviewMedia(ProjectVideoRender(index, store))
    return PreviewService(timeline_services.current, media, renderer)
