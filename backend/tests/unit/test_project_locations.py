"""Desktop workspace policy without providers or user data."""

from pathlib import Path

import pytest

from app.desktop import project_locations as locations


def test_source_root_does_not_depend_on_launch_directory(tmp_path, monkeypatch):
    monkeypatch.setattr(locations, "installed_root", lambda: None)
    monkeypatch.chdir(tmp_path)
    assert locations.projects_directory() == Path(locations.__file__).resolve().parents[3] / "projects"


def test_installed_projects_use_writable_user_data(tmp_path, monkeypatch):
    monkeypatch.setattr(locations, "installed_root", lambda: tmp_path / "installation")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "user"))
    assert locations.projects_directory() == tmp_path / "user" / "AI Content Studio" / "projects"


def test_project_name_cannot_escape_root(tmp_path, monkeypatch):
    root = tmp_path / "projects"
    monkeypatch.setattr(locations, "projects_directory", lambda: root)
    assert locations.new_project_path("../Film: GPS / test") == root / "Film-GPS-test"
    assert not root.exists()  # Location selection alone never creates data.


@pytest.mark.parametrize("name", ["...", "  ", "CON", "nul", "COM1"])
def test_invalid_names_do_not_create_data(tmp_path, monkeypatch, name):
    monkeypatch.setattr(locations, "projects_directory", lambda: tmp_path / "projects")
    with pytest.raises(ValueError):
        locations.new_project_path(name)
    assert list(tmp_path.iterdir()) == []


def test_existing_folder_is_never_reused(tmp_path, monkeypatch):
    monkeypatch.setattr(locations, "projects_directory", lambda: tmp_path)
    project = tmp_path / "Film"
    project.mkdir()
    data = project / "project.sqlite"
    data.write_bytes(b"existing project")
    with pytest.raises(FileExistsError):
        locations.new_project_path("Film")
    assert data.read_bytes() == b"existing project"
