#!/bin/sh
set -eu

fail() {
  printf '%s\n' "HOOK_INSTALL: FAIL"
  printf '%s\n' "reason: $1" >&2
  exit 1
}

root=$(git rev-parse --show-toplevel 2>/dev/null || true)
if [ -z "${root:-}" ]; then
  fail "not inside a git repository"
fi

cd "$root"

if [ ! -f .githooks/pre-commit ]; then
  fail "missing hook file .githooks/pre-commit"
fi

if [ ! -f .githooks/pre-push ]; then
  fail "missing hook file .githooks/pre-push"
fi

python_bin=$(python -c 'import sys; print(sys.executable)')
git config --local agent.python "$python_bin"
stored_python=$(git config --local --get agent.python || true)
if [ "$stored_python" != "$python_bin" ]; then
  fail "expected agent.python to match the active interpreter"
fi

git config --local core.hooksPath .githooks
hooks_path=$(git config --local --get core.hooksPath || true)
if [ "$hooks_path" != ".githooks" ]; then
  fail "expected .githooks, got ${hooks_path:-<empty>}"
fi

chmod +x .githooks/pre-commit
chmod +x .githooks/pre-push

printf '%s\n' "HOOK_INSTALL: PASS"
printf '%s\n' "HOOKS_PATH: .githooks"
