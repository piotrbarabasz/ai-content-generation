#!/bin/sh
set -eu

version_text=$(python -c 'import sys; print(f"{sys.version_info[0]}.{sys.version_info[1]}.{sys.version_info[2]}")')
major=$(printf '%s' "$version_text" | cut -d. -f1)
minor=$(printf '%s' "$version_text" | cut -d. -f2)

if [ "$major" -lt 3 ] || { [ "$major" -eq 3 ] && [ "$minor" -lt 11 ]; }; then
  printf '%s\n' "setup-dev: Python >= 3.11 required, found $version_text" >&2
  exit 1
fi

python -m pip install -e .
"$(dirname "$0")/install-git-hooks.sh"
python -m pytest backend/tests/unit/tooling
