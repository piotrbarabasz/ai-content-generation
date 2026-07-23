"""Local autopilot primitives for the AI Content Studio workflow."""

from .config import AutopilotConfig, DEFAULT_AUTOPILOT_CONFIG_PATH, load_autopilot_config
from .controller import AutopilotController, AutopilotControllerError, ControllerEvent, ControllerSnapshot, ScopeChoices, run_local_autopilot_controller
from .models import (
    AutopilotRequest,
    AutopilotRun,
    CommandResult,
    PullRequestInfo,
    RunMode,
    RunStatus,
    ScopeType,
    TaskResult,
)
from .state_store import (
    AUTOPILOT_STATE_DIR,
    load_run_state,
    run_state_path,
    save_run_state,
)

__all__ = [
    "AUTOPILOT_STATE_DIR",
    "AutopilotConfig",
    "AutopilotController",
    "AutopilotControllerError",
    "AutopilotRequest",
    "AutopilotRun",
    "CommandResult",
    "ControllerEvent",
    "ControllerSnapshot",
    "DEFAULT_AUTOPILOT_CONFIG_PATH",
    "PullRequestInfo",
    "RunMode",
    "RunStatus",
    "ScopeType",
    "TaskResult",
    "ScopeChoices",
    "load_autopilot_config",
    "load_run_state",
    "run_state_path",
    "run_local_autopilot_controller",
    "save_run_state",
]
