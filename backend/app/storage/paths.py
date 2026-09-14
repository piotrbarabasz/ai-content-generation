"""Root-bound local I/O; operation-boundary checks, not a hostile-process sandbox."""

from pathlib import Path, PureWindowsPath
import stat


def relative_key(value: str, *, label: str = "Artifact key") -> str:
    value = str(value).replace("\\", "/")
    if not value:
        raise ValueError(f"{label} is required.")
    if value.startswith("/") or PureWindowsPath(value).drive:
        raise ValueError(f"{label} must be relative.")
    parts = value.split("/")
    if any(part in ("", ".", "..") for part in parts):
        raise ValueError(f"{label} cannot traverse directories.")
    for part in parts:
        if (any(ord(c) < 32 or c in ':<>"|?*' for c in part)
                or part.endswith((".", " ")) or PureWindowsPath(part).is_reserved()):
            raise ValueError(f"{label} contains unsafe Windows path syntax.")
    return "/".join(parts)


def storage_root(value: Path | str) -> Path:
    """Bind a caller-selected root once; links inside this root are never adopted."""
    path = Path(value).expanduser().absolute()
    # Validate before resolve(), which can erase drive/ADS/device aliases.
    if str(path).startswith(("\\\\?\\", "\\\\.\\")):
        raise ValueError("Device namespace paths are not supported.")
    if len(path.parts) > 1:
        relative_key("/".join(path.parts[1:]))
    return path.resolve()


def contained_path(root: Path, key: str) -> Path:
    """Reject links/reparse points, including junctions, before following children."""
    key = relative_key(key)
    if root.resolve() != root:
        raise ValueError("Configured storage root was redirected.")
    current = root
    for part in key.split("/"):
        current = current / part
        try:
            info = current.lstat()
        except FileNotFoundError:
            continue
        if (stat.S_ISLNK(info.st_mode)
                or getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT):
            raise ValueError("Storage path contains a link or reparse point.")
    current.resolve().relative_to(root)
    return current


def database_path(root: Path, key: str) -> Path:
    path = contained_path(root, key)
    # SQLite can open these itself, even when the main database is safe.
    for suffix in ("-journal", "-wal", "-shm"):
        contained_path(root, key + suffix)
    return path


def import_path(source: Path | str, source_root: Path | None = None) -> Path:
    """An explicit external file is a caller capability; relative refs need a root."""
    path = Path(source).expanduser()
    if source_root is not None:
        root = storage_root(source_root)
        key = path.relative_to(root).as_posix() if path.is_absolute() else str(source)
        return contained_path(root, key)
    # Backwards-compatible explicit file selection; reject redirected files/parents.
    if str(path).startswith(("\\\\?\\", "\\\\.\\")) or (path.drive and not path.is_absolute()):
        raise ValueError("Import source must not use device or drive-relative syntax.")
    absolute = path.absolute()
    root = Path(absolute.anchor)
    return contained_path(root, absolute.relative_to(root).as_posix())
