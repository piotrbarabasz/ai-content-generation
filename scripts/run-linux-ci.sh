#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage:
  scripts/run-linux-ci.sh --bundle-path <path> --summary-path <path> --repo-root <path> --head-sha <sha> --branch <branch> [--keep-workspace]

This script runs inside WSL on a native Linux filesystem clone.
EOF
}

fail() {
  local message="$1"
  echo "LOCAL_LINUX_CI: FAIL"
  echo "reason: $message"
  exit "${2:-1}"
}

bundle_path=""
summary_path=""
repo_root=""
head_sha=""
branch=""
keep_workspace="false"
timestamp="$(date -u +%Y%m%d-%H%M%S)"
stage_results_file="$(mktemp)"
pytest_log_file=""
workspace_path=""
clone_bundle_path=""
python_executable=""
python_version=""
uv_used="false"
failed_stage=""
failed_exit_code=0
summary_python=""

cleanup() {
  local exit_code="$1"
  if [[ -n "$summary_path" ]]; then
    if [[ -z "$summary_python" ]]; then
      summary_python="${python_executable:-}"
    fi
    if [[ -z "$summary_python" ]]; then
      summary_python="$(command -v python3 || command -v python || true)"
    fi
    if [[ -n "$summary_python" ]]; then
      "$summary_python" - "$summary_path" "$stage_results_file" "$pytest_log_file" "$repo_root" "$workspace_path" "$bundle_path" "$head_sha" "$branch" "$timestamp" "$keep_workspace" "$exit_code" "$failed_stage" "$failed_exit_code" "$python_executable" "$python_version" "$uv_used" <<'PY'
import json
import os
import re
import sys
from pathlib import Path

summary_path = Path(sys.argv[1])
stage_results_file = Path(sys.argv[2])
pytest_log_file = Path(sys.argv[3]) if sys.argv[3] else None
repo_root = sys.argv[4]
workspace_path = sys.argv[5]
bundle_path = sys.argv[6]
head_sha = sys.argv[7]
branch = sys.argv[8]
timestamp = sys.argv[9]
keep_workspace = sys.argv[10].lower() == "true"
exit_code = int(sys.argv[11])
failed_stage = sys.argv[12]
failed_exit_code = int(sys.argv[13])
python_executable = sys.argv[14]
python_version = sys.argv[15]
uv_used = sys.argv[16].lower() == "true"

stages = []
if stage_results_file.exists():
    for line in stage_results_file.read_text(encoding="utf-8").splitlines():
        if line.strip():
            stages.append(json.loads(line))

failed_tests = []
if pytest_log_file and pytest_log_file.exists():
    for line in pytest_log_file.read_text(encoding="utf-8", errors="replace").splitlines():
        match = re.match(r"^(FAILED|ERROR)\s+([^\s]+)", line)
        if match:
            failed_tests.append(match.group(2))

summary = {
    "status": "PASS" if exit_code == 0 else "FAIL",
    "reason": None if exit_code == 0 else f"{failed_stage} failed",
    "failed_stage": failed_stage or None,
    "exit_code": exit_code,
    "failed_exit_code": failed_exit_code if failed_stage else None,
    "failed_tests": failed_tests,
    "repo_root": repo_root,
    "workspace_path": workspace_path,
    "bundle_path": bundle_path,
    "head_sha": head_sha,
    "branch": branch,
    "timestamp": timestamp,
    "keep_workspace": keep_workspace,
    "python_executable": python_executable,
    "python_version": python_version,
    "uv_used": uv_used,
    "stages": stages,
}
summary_path.parent.mkdir(parents=True, exist_ok=True)
tmp = summary_path.with_suffix(summary_path.suffix + ".tmp")
tmp.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
tmp.replace(summary_path)
PY
    fi
  fi
  if [[ "$exit_code" -eq 0 ]]; then
    if [[ "$keep_workspace" != "true" && -n "$workspace_path" && -d "$workspace_path" ]]; then
      rm -rf "$workspace_path"
    fi
    echo "LOCAL_LINUX_CI: PASS"
  else
    echo "LOCAL_LINUX_CI: FAIL"
    if [[ -n "$workspace_path" ]]; then
      echo "workspace: $workspace_path"
    fi
  fi
  if [[ -n "$stage_results_file" && -f "$stage_results_file" ]]; then
    rm -f "$stage_results_file"
  fi
}

trap 'cleanup $?' EXIT

while [[ $# -gt 0 ]]; do
  case "$1" in
    --bundle-path)
      bundle_path="$2"
      shift 2
      ;;
    --summary-path)
      summary_path="$2"
      shift 2
      ;;
    --repo-root)
      repo_root="$2"
      shift 2
      ;;
    --head-sha)
      head_sha="$2"
      shift 2
      ;;
    --branch)
      branch="$2"
      shift 2
      ;;
    --keep-workspace)
      keep_workspace="true"
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      fail "unknown argument: $1"
      ;;
  esac
done

if [[ -z "$bundle_path" || -z "$summary_path" || -z "$repo_root" || -z "$head_sha" || -z "$branch" ]]; then
  usage
  fail "missing required arguments"
fi

log() {
  echo "$1"
}

record_stage() {
  local name="$1"
  local status="$2"
  local exit_code="$3"
  local duration_ms="$4"
  local command_json="$5"
  "$summary_python" - "$stage_results_file" "$name" "$status" "$exit_code" "$duration_ms" "$command_json" <<'PY'
import json
import sys
from pathlib import Path

path = Path(sys.argv[1])
entry = {
    "name": sys.argv[2],
    "status": sys.argv[3],
    "exit_code": int(sys.argv[4]) if sys.argv[4] != "null" else None,
    "duration_ms": int(sys.argv[5]),
    "command": json.loads(sys.argv[6]),
}
with path.open("a", encoding="utf-8") as handle:
    handle.write(json.dumps(entry) + "\n")
PY
}

detect_python() {
  local candidate=""
  for candidate in python3.11 python3 python; do
    if command -v "$candidate" >/dev/null 2>&1; then
      local version
      version="$("$candidate" -c 'import sys; print(f"{sys.version_info[0]}.{sys.version_info[1]}.{sys.version_info[2]}")')"
      if [[ "$version" == 3.11.* ]]; then
        python_executable="$candidate"
        python_version="$version"
        summary_python="$candidate"
        return 0
      fi
    fi
  done
  fail "Python 3.11 is required inside WSL"
}

run_stage() {
  local name="$1"
  shift
  local start_ns end_ns duration_ms exit_code stage_log stage_output command_json
  stage_log="$(mktemp)"
  command_json="$(python3 - "$@" <<'PY'
import json
import sys
print(json.dumps(sys.argv[1:]))
PY
)"
  start_ns="$(date +%s%N)"
  set +e
  "$@" 2>&1 | tee "$stage_log"
  exit_code="${PIPESTATUS[0]}"
  set -e
  end_ns="$(date +%s%N)"
  duration_ms="$(( (end_ns - start_ns) / 1000000 ))"
  record_stage "$name" "$( [[ "$exit_code" -eq 0 ]] && echo PASS || echo FAIL )" "$exit_code" "$duration_ms" "$command_json"
  if [[ "$exit_code" -ne 0 ]]; then
    failed_stage="$name"
    failed_exit_code="$exit_code"
    if [[ "$name" == "pytest" ]]; then
      pytest_log_file="$stage_log"
    else
      rm -f "$stage_log"
    fi
    return "$exit_code"
  fi
  rm -f "$stage_log"
}

log "LOCAL_LINUX_CI: start"
log "repo_root: $repo_root"
log "branch: $branch"
log "head_sha: $head_sha"
log "bundle_path: $bundle_path"
log "summary_path: $summary_path"
log "timestamp: $timestamp"

detect_python
log "python: $python_executable ($python_version)"

if command -v uv >/dev/null 2>&1; then
  uv_used="true"
  log "uv: $(command -v uv)"
else
  log "uv is not installed. Install uv to speed up environment setup or rerun with a standard Python 3.11 venv."
fi

workspace_root="$HOME/ai-content-generation-ci/$timestamp"
workspace_path="$workspace_root"
mkdir -p "$workspace_root"

clone_bundle_path="/tmp/ai-content-generation-ci-$timestamp.bundle"
cp "$bundle_path" "$clone_bundle_path"
log "clone_bundle: $clone_bundle_path"

git clone "$clone_bundle_path" "$workspace_root"
cd "$workspace_root"
git checkout --detach "$head_sha"

base_sha="$(git rev-parse master)"
clone_head_sha="$(git rev-parse HEAD)"
if [[ "$clone_head_sha" != "$head_sha" ]]; then
  fail "checked out head $clone_head_sha does not match expected $head_sha"
fi

if [[ "$uv_used" == "true" ]]; then
  uv venv .venv-ci --python "$python_executable"
else
  "$python_executable" -m venv .venv-ci
fi

.venv-ci/bin/python -m pip install -e .

run_stage "workstream_validation" .venv-ci/bin/python -m backend.app.tooling.workstream_validation
run_stage "repository_checks" .venv-ci/bin/python -m backend.app.tooling.repository_checks --mode task-metadata
run_stage "pytest" .venv-ci/bin/python -m pytest -ra
run_stage "git_hook_runner" .venv-ci/bin/python -m backend.app.tooling.git_hook_runner ci --base-sha "$base_sha" --head-sha "$head_sha"
