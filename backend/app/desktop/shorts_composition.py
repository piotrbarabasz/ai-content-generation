"""Optional trusted local encoder; constructing Shorts never loads a model."""
from app.application.shorts import ShortsService
from app.desktop.deployment import media_executables
from app.providers.ffmpeg_shorts import FFmpegShortsRenderer


def compose_shorts(publishing):
    ffmpeg, ffprobe = media_executables()
    renderer = FFmpegShortsRenderer(ffmpeg, ffprobe) if ffmpeg and ffprobe else None
    return ShortsService(publishing, renderer)
