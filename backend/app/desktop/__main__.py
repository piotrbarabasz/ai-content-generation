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


def main(provider=None):
    application = QApplication(sys.argv)
    window = ProjectEditor(LocalProjects(), provider=provider)
    window.show()
    return application.exec()


if __name__ == "__main__":
    raise SystemExit(main())
