"""Local QA composition; no generation/upload or semantic provider is constructed."""
from app.application.production_quality import ProductionQualityService
from app.application.quality_sources import ProjectQualitySources
from app.desktop.deployment import media_executables
from app.providers.ffmpeg_quality import FFmpegQAInspector
from app.storage.production_quality import ProjectQuality


def compose_quality(publishing):
    ffmpeg, ffprobe = media_executables()
    inspector = FFmpegQAInspector(ffmpeg, ffprobe) if ffmpeg and ffprobe else None
    return ProductionQualityService(ProjectQualitySources(publishing), ProjectQuality(publishing.store, publishing.session.project.id), inspector)
