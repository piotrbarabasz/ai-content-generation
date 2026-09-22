"""Desktop editor entrypoint for source and standalone release runs."""

from functools import partial
import json
from pathlib import Path
import sys

from PySide6.QtCore import QCoreApplication, QTimer
from PySide6.QtWidgets import QApplication

from app.application.projects import ProjectSession
from app.desktop.editor import ProjectEditor
from app.storage.project_repository import ProjectRepository


class LocalProjects:
    def create(self, path, **metadata):
        return ProjectSession.create(path, repository_factory=ProjectRepository, **metadata)

    def open(self, path):
        return ProjectSession.open(path, repository_factory=ProjectRepository)


def main(provider=None, audio_factory=None, scene_factory=None, timeline_factory=None, preview_factory=None,
         regeneration_factory=None):
    smoke_report = None
    if "--release-smoke" in sys.argv:
        position = sys.argv.index("--release-smoke")
        try:
            smoke_report = Path(sys.argv[position + 1]).resolve()
        except IndexError as exc:
            raise ValueError("--release-smoke requires an explicit report path.") from exc
        del sys.argv[position:position + 2]
    from app.desktop.regeneration_composition import compose_regeneration
    from app.desktop.image_composition import compose_installed_image
    from app.desktop.llm_composition import compose_installed_llm
    from app.desktop.preview_composition import compose_preview
    from app.desktop.product_composition import compose_installed_audio
    from app.desktop.scene_composition import compose_scenes
    from app.desktop.timeline_composition import compose_timeline

    from app.desktop.deployment import APPLICATION_VERSION

    QCoreApplication.setOrganizationName("AI Content Studio")
    QCoreApplication.setApplicationName("AI Content Studio")
    QCoreApplication.setApplicationVersion(APPLICATION_VERSION)
    application = QApplication(sys.argv)
    llm_provider = provider if provider is not None else compose_installed_llm()
    image_provider = compose_installed_image() if scene_factory is None else None
    if scene_factory is None and (llm_provider is not None or image_provider is not None):
        identity_builder = getattr(llm_provider, "generation_identity", None) if llm_provider else None
        prompt_identity = (identity_builder() if callable(identity_builder) else
                           ({"provider": getattr(llm_provider, "provider_name", "configured")}
                            if llm_provider else {}))
        scene_factory = partial(
            compose_scenes,
            prompt_provider=llm_provider,
            prompt_identity=prompt_identity,
            image_provider=image_provider,
        )
    window = ProjectEditor(LocalProjects(), provider=llm_provider, audio_factory=audio_factory or compose_installed_audio,
                           scene_factory=scene_factory, timeline_factory=timeline_factory or compose_timeline,
                           preview_factory=preview_factory or compose_preview,
                           regeneration_factory=regeneration_factory or compose_regeneration)
    window.show()
    if smoke_report is not None:
        from app.desktop.deployment import UserDataPaths, installed_root, media_executables

        def finish_smoke():
            paths = UserDataPaths.discover().prepare()
            ffmpeg, ffprobe = media_executables()
            payload = {
                "schema_version": 1,
                "window_visible": window.isVisible(),
                "installed_root": str(installed_root()) if installed_root() else None,
                "user_data_root": str(paths.root),
                "ffmpeg": ffmpeg,
                "ffprobe": ffprobe,
            }
            smoke_report.parent.mkdir(parents=True, exist_ok=True)
            smoke_report.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
            application.quit()

        QTimer.singleShot(0, finish_smoke)
    return application.exec()


if __name__ == "__main__":
    raise SystemExit(main())
