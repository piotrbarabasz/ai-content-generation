"""Entry point for the local autopilot desktop application."""

from __future__ import annotations

import sys
from dataclasses import dataclass
from importlib import import_module
from typing import Sequence

from .ui import launch_app


@dataclass(frozen=True)
class StartupCheckResult:
    python_ok: bool
    tkinter_ok: bool
    issues: tuple[str, ...] = ()


def validate_startup_environment(
    *,
    version_info: tuple[int, int, int] | None = None,
    tkinter_importer=import_module,
) -> StartupCheckResult:
    version = version_info or sys.version_info[:3]
    issues: list[str] = []
    python_ok = (version[0], version[1]) >= (3, 11)
    if not python_ok:
        issues.append(f"Python 3.11 or newer is required, found {version[0]}.{version[1]}.{version[2]}")
    tkinter_ok = True
    try:
        tkinter_importer("tkinter")
    except ModuleNotFoundError:
        tkinter_ok = False
        issues.append("tkinter is not available")
    return StartupCheckResult(python_ok=python_ok, tkinter_ok=tkinter_ok, issues=tuple(issues))


def main(argv: Sequence[str] | None = None) -> int:
    _ = argv
    startup = validate_startup_environment()
    if startup.issues:
        raise RuntimeError("; ".join(startup.issues))
    return launch_app()


if __name__ == "__main__":
    raise SystemExit(main())
