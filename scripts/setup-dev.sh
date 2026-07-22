#!/bin/sh
set -eu

python3.11 -c 'import sys; raise SystemExit(0 if sys.version_info[:2] == (3, 11) else 1)'
python3.11 -m pip install -e .
sh "$(dirname "$0")/install-git-hooks.sh"
python3.11 -m pytest backend/tests/unit/tooling
