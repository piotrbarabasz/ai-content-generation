"""Installed-desktop locations and bundled native media discovery."""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import shutil
import sys


APP_DIRECTORY = "AI Content Studio"
APPLICATION_VERSION = "0.1.0"
MEDIA_EXECUTABLES = ("ffmpeg.exe", "ffprobe.exe")


@dataclass(frozen=True, slots=True)
class UserDataPaths:
    """Per-user mutable state, deliberately outside the installation directory."""

    root: Path
    runtimes: Path
    models: Path
    cache: Path
    logs: Path

    @property
    def channels(self):
        from app.storage.paths import contained_path
        return contained_path(self.root.parent.resolve(), self.root.name+"/channels")

    @classmethod
    def discover(cls, environment: dict[str, str] | None = None) -> "UserDataPaths":
        environment = os.environ if environment is None else environment
        value = environment.get("LOCALAPPDATA")
        if not value:
            raise RuntimeError("LOCALAPPDATA is required for the Windows desktop installation.")
        base = Path(value).expanduser()
        if not base.is_absolute():
            raise RuntimeError("LOCALAPPDATA must be an absolute path.")
        root = base.resolve() / APP_DIRECTORY
        return cls(root, root / "runtimes", root / "models", root / "cache", root / "logs")

    def prepare(self) -> "UserDataPaths":
        for path in (self.root, self.runtimes, self.models, self.cache, self.logs):
            path.mkdir(parents=True, exist_ok=True)
        return self


def installed_root() -> Path | None:
    """Return the standalone bundle root; source runs deliberately return None."""

    if getattr(sys, "frozen", False) or "__compiled__" in globals():
        return Path(sys.argv[0]).resolve().parent
    return None


def _plain_file(root: Path, path: Path) -> Path:
    resolved_root = root.resolve(strict=True)
    resolved = path.resolve(strict=True)
    if resolved.parent != resolved_root or path.is_symlink() or not resolved.is_file():
        raise RuntimeError(f"Bundled media component is unsafe: {path.name}")
    return resolved


def media_executables(*, root: Path | None = None, path_lookup=shutil.which) -> tuple[str | None, str | None]:
    """Use only app-local media in a bundle; PATH is a source-development fallback."""

    bundle = installed_root() if root is None else Path(root)
    if bundle is not None:
        try:
            media = bundle.resolve(strict=True) / "media"
            return tuple(str(_plain_file(media, media / name)) for name in MEDIA_EXECUTABLES)
        except (FileNotFoundError, OSError):
            return None, None
    return path_lookup("ffmpeg"), path_lookup("ffprobe")


__all__ = ["APP_DIRECTORY", "APPLICATION_VERSION", "MEDIA_EXECUTABLES", "UserDataPaths",
           "installed_root", "media_executables"]


def channel_library_root(environment=None):
    """Windows app-owned library, with an XDG fallback for source/test hosts."""
    environment=os.environ if environment is None else environment
    if environment.get("LOCALAPPDATA") or os.name=="nt":
        return UserDataPaths.discover(environment).channels
    base=Path(environment.get("XDG_DATA_HOME",str(Path.home()/".local/share"))).expanduser()
    if not base.is_absolute(): raise ValueError("XDG_DATA_HOME must be absolute.")
    from app.storage.paths import contained_path,storage_root
    root=storage_root(base)
    return contained_path(root,APP_DIRECTORY+"/channels")
