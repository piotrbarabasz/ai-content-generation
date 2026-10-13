"""Optional preparation composition; never constructs a YouTube upload adapter."""

from app.application.publishing_package import PublishingPackageService
from app.desktop.deployment import media_executables
from app.providers.local_thumbnail import LocalThumbnailBuilder
from app.storage.plan_script import ProjectPlanScripts
from app.storage.video_plans import ProjectVideoPlans


def compose_publishing(session, export_panel, timelines, *, llm=None, alignment=None):
    plans = ProjectVideoPlans(session.repository, export_panel.store)
    ffmpeg, _ = media_executables()
    return PublishingPackageService(session, export_panel.store, export_panel.render_media, timelines, plans,
        ProjectPlanScripts(session.repository, export_panel.store, plans), LocalThumbnailBuilder(ffmpeg),
        llm=llm, alignment=alignment, delivery=export_panel.delivery.service, finishing=export_panel.branding.service)
