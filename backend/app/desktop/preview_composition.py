"""Optional local preview composition; no generation provider is constructed."""

import shutil

from app.application.preview import PreviewService
from app.jobs.repository import JobRepository
from app.providers.ffmpeg_render import FFmpegRenderer
from app.storage.local_store import LocalArtifactStore
from app.storage.preview import ProjectPreviewMedia
from app.storage.video_render import ProjectVideoRender, RenderResultIndex


def compose_preview(session, timeline_services, *, ffmpeg=None, ffprobe=None, process=None):
    ffmpeg = ffmpeg or shutil.which("ffmpeg")
    ffprobe = ffprobe or shutil.which("ffprobe")
    if not ffmpeg or not ffprobe:
        return None
    jobs = JobRepository(session.repository)
    index = RenderResultIndex(session.repository, jobs)
    store = LocalArtifactStore(index.root, index=index)
    renderer = FFmpegRenderer(ffmpeg, ffprobe, process=process, proxy=True)
    media = ProjectPreviewMedia(ProjectVideoRender(index, store))
    return PreviewService(timeline_services.current, media, renderer)

