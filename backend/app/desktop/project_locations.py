"""Desktop project locations; storage adapters still accept caller-owned roots."""

from pathlib import Path
import re

from app.desktop.deployment import UserDataPaths, installed_root
from app.storage.paths import contained_path, relative_key


def projects_directory() -> Path:
    """Anchor source runs to the checkout, installed runs to writable user data."""
    if installed_root() is not None:
        return UserDataPaths.discover().root / "projects"
    return Path(__file__).resolve().parents[3] / "projects"


def new_project_path(name: str) -> Path:
    """Use one safe child per project; never reuse an existing workspace."""
    folder = re.sub(r'[^\w-]+', '-', name.strip(), flags=re.UNICODE).strip('-_')
    if not folder:
        raise ValueError("Enter a project name containing letters or numbers.")
    relative_key(folder, label="Project name")
    root = projects_directory().resolve()
    path = contained_path(root, folder)
    if path.exists():
        raise FileExistsError(f"Project folder already exists: {path}. Open it or choose another name.")
    return path
