"""Development editor entrypoint: python -m app.desktop."""

import sys

from PySide6.QtWidgets import QApplication

from app.application.projects import ProjectSession
from app.desktop.editor import ProjectEditor
from app.storage.project_repository import ProjectRepository


class LocalProjects:
    def create(self, path, **metadata):
        return ProjectSession.create(path, repository_factory=ProjectRepository, **metadata)

    def open(self, path):
        return ProjectSession.open(path, repository_factory=ProjectRepository)


def main(provider=None, audio_factory=None, scene_factory=None, timeline_factory=None, preview_factory=None):
    from app.desktop.preview_composition import compose_preview
    from app.desktop.timeline_composition import compose_timeline

    application = QApplication(sys.argv)
    window = ProjectEditor(LocalProjects(), provider=provider, audio_factory=audio_factory,
                           scene_factory=scene_factory, timeline_factory=timeline_factory or compose_timeline,
                           preview_factory=preview_factory or compose_preview)
    window.show()
    return application.exec()


if __name__ == "__main__":
    raise SystemExit(main())
