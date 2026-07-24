from __future__ import annotations

import pytest

from app.tooling.local_autopilot import codex_adapter, github_adapter


@pytest.fixture(autouse=True)
def _default_cli_executables(monkeypatch):
    monkeypatch.setattr(codex_adapter, "resolve_codex_cli_executable", lambda: "codex")
    monkeypatch.setattr(github_adapter, "resolve_github_cli_executable", lambda: "gh")
