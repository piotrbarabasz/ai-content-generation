"""Configuration loading for the local autopilot."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

import yaml

ROOT = Path(__file__).resolve().parents[4]
DEFAULT_AUTOPILOT_CONFIG_PATH = ROOT / ".specify" / "autopilot.yml"


@dataclass(frozen=True)
class AutopilotConfig:
    auto_commit: bool
    auto_push: bool
    create_draft_pr: bool
    auto_merge: bool
    deploy: bool
    max_repair_cycles: int
    max_tasks_per_run: int
    command_timeout_seconds: int
    codex_timeout_seconds: int
    closure_mode: str


def _require_bool(mapping: Mapping[str, Any], key: str) -> bool:
    value = mapping.get(key)
    if not isinstance(value, bool):
        raise ValueError(f"{key} must be a boolean")
    return value


def _require_int(mapping: Mapping[str, Any], key: str) -> int:
    value = mapping.get(key)
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"{key} must be an integer")
    if value <= 0:
        raise ValueError(f"{key} must be positive")
    return value


def _require_text(mapping: Mapping[str, Any], key: str) -> str:
    value = mapping.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{key} must be a non-empty string")
    return value.strip()


def load_autopilot_config(path: Path | str = DEFAULT_AUTOPILOT_CONFIG_PATH) -> AutopilotConfig:
    path = Path(path)
    try:
        loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise FileNotFoundError(f"autopilot config does not exist: {path}") from exc
    except yaml.YAMLError as exc:
        raise ValueError(f"{path.name}: invalid YAML: {exc}") from exc
    if not isinstance(loaded, dict):
        raise ValueError("autopilot config must be a mapping")

    required_fields = {
        "auto_commit",
        "auto_push",
        "create_draft_pr",
        "auto_merge",
        "deploy",
        "max_repair_cycles",
        "max_tasks_per_run",
        "command_timeout_seconds",
        "codex_timeout_seconds",
        "closure_mode",
    }
    missing = sorted(required_fields - loaded.keys())
    if missing:
        raise ValueError(f"autopilot config is missing required fields: {', '.join(missing)}")

    config = AutopilotConfig(
        auto_commit=_require_bool(loaded, "auto_commit"),
        auto_push=_require_bool(loaded, "auto_push"),
        create_draft_pr=_require_bool(loaded, "create_draft_pr"),
        auto_merge=_require_bool(loaded, "auto_merge"),
        deploy=_require_bool(loaded, "deploy"),
        max_repair_cycles=_require_int(loaded, "max_repair_cycles"),
        max_tasks_per_run=_require_int(loaded, "max_tasks_per_run"),
        command_timeout_seconds=_require_int(loaded, "command_timeout_seconds"),
        codex_timeout_seconds=_require_int(loaded, "codex_timeout_seconds"),
        closure_mode=_require_text(loaded, "closure_mode"),
    )
    if config.closure_mode != "pull_request":
        raise ValueError("closure_mode must be 'pull_request'")
    return config


__all__ = ["AutopilotConfig", "DEFAULT_AUTOPILOT_CONFIG_PATH", "load_autopilot_config"]
