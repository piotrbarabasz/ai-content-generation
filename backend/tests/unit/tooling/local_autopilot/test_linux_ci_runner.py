from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[5]
SCRIPTS_DIR = ROOT / "scripts"


def test_run_linux_ci_ps1_declares_windows_preflight_and_summary_paths() -> None:
    script = (SCRIPTS_DIR / "run-linux-ci.ps1").read_text(encoding="utf-8")

    assert "[string]$Distribution = 'Ubuntu-24.04'" in script
    assert "[switch]$KeepWorkspace" in script
    assert "[switch]$Help" in script
    assert "Get-Command wsl.exe" in script
    assert "wsl.exe -l -q" in script
    assert "git status --porcelain=v1" in script
    assert "git rev-parse HEAD" in script
    assert "git branch --show-current" in script
    assert "git bundle create $script:BundlePath HEAD master" in script
    assert ".specify/runtime/local-ci" in script
    assert "latest.json" in script
    assert "Start-Process -FilePath $wsl.Path" in script
    assert "--exec', '/bin/bash'" in script
    assert script.endswith("\n")


def test_run_linux_ci_sh_clones_to_native_linux_and_runs_required_commands() -> None:
    script = (SCRIPTS_DIR / "run-linux-ci.sh").read_text(encoding="utf-8")

    assert 'workspace_root="$HOME/ai-content-generation-ci/$timestamp"' in script
    assert "/tmp/ai-content-generation-ci-$timestamp.bundle" in script
    assert "git clone \"$clone_bundle_path\" \"$workspace_root\"" in script
    assert "git checkout --detach \"$head_sha\"" in script
    assert "uv venv .venv-ci --python \"$python_executable\"" in script
    assert ".venv-ci/bin/python -m pip install -e ." in script
    assert ".venv-ci/bin/python -m backend.app.tooling.workstream_validation" in script
    assert ".venv-ci/bin/python -m backend.app.tooling.repository_checks --mode task-metadata" in script
    assert ".venv-ci/bin/python -m pytest -ra" in script
    assert ".venv-ci/bin/python -m backend.app.tooling.git_hook_runner ci --base-sha \"$base_sha\" --head-sha \"$head_sha\"" in script
    assert "/mnt/c" not in script
    assert "/mnt/d" not in script
    assert script.endswith("\n")


def test_local_autopilot_platform_safety_sources_avoid_global_os_name_mutation() -> None:
    sources = [
        ROOT / "backend" / "tests" / "unit" / "tooling" / "local_autopilot" / "test_autopilot_hardening.py",
        ROOT / "backend" / "tests" / "unit" / "tooling" / "local_autopilot" / "test_task_pipeline.py",
        ROOT / "backend" / "tests" / "unit" / "tooling" / "test_git_hook_runner.py",
    ]

    for path in sources:
        text = path.read_text(encoding="utf-8")
        assert "os.name =" not in text
        assert 'patch.object(os, "name"' not in text


def test_local_autopilot_fixtures_explicitly_control_final_newline() -> None:
    task_pipeline_test = (ROOT / "backend" / "tests" / "unit" / "tooling" / "local_autopilot" / "test_task_pipeline.py").read_text(encoding="utf-8")
    hardening_test = (ROOT / "backend" / "tests" / "unit" / "tooling" / "local_autopilot" / "test_autopilot_hardening.py").read_text(encoding="utf-8")

    assert "newline=False" in task_pipeline_test
    assert "newline=False" in hardening_test
