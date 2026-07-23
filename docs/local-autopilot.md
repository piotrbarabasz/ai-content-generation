# Local Autopilot

The local autopilot is a Windows desktop control panel for running one epic or one milestone at a time against the local repository.

## What It Does

- Picks a scope from the checked-in manifests.
- Uses the pinned `agent.python` interpreter from local Git config.
- Runs one task at a time through the local Codex CLI.
- Validates each task, commits once per task, and pushes only when the selected mode allows it.
- Creates draft pull requests through the local GitHub CLI.
- Stops before merge and never deploys automatically.

## Safety Rules

- No automatic merge.
- No automatic deployment.
- No shell-based process execution.
- No network access in tests.
- No secrets are written to runtime state or logs.
- Runtime state lives under `.specify/runtime/autopilot/`.

## Startup Checks

- Python 3.11 or newer is required.
- `tkinter` must be available.
- The repository must provide a valid `agent.python` Git config value before a run can start.
- The local Codex and GitHub CLIs are detected at runtime and failures are surfaced as run errors.

## Common Flow

1. Choose a repository path.
2. Pick `Epic` or `Milestone`.
3. Select the manifest ID.
4. Pick `Full` or `Stop before push`.
5. Start the run after confirming the safety summary.
6. Watch progress, logs, and PR state.
7. Use `Resume` after a manual merge when the run is waiting for merge.

## Launchers

- PowerShell: `scripts/run-local-autopilot.ps1`
- Double-click launcher: `scripts/run-local-autopilot.cmd`
- Module entry point: `python -m backend.app.tooling.local_autopilot`
