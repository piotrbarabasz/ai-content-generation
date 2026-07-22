Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

py -3.11 -c "import sys; raise SystemExit(0 if sys.version_info[:2] == (3, 11) else 1)"
py -3.11 -m pip install -e .
& "$PSScriptRoot/install-git-hooks.ps1"
py -3.11 -m pytest backend/tests/unit/tooling
