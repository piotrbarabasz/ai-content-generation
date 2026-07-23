Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Fail {
    param([string]$Message)
    Write-Error $Message
    exit 1
}

$versionText = & python -c "import sys; print(f'{sys.version_info[0]}.{sys.version_info[1]}.{sys.version_info[2]}')"
if (-not $versionText) {
    Fail 'setup-dev: failed to detect Python version'
}

$versionParts = $versionText.Split('.')
$major = [int]$versionParts[0]
$minor = [int]$versionParts[1]
if (($major -lt 3) -or ($major -eq 3 -and $minor -lt 11)) {
    Fail "setup-dev: Python >= 3.11 required, found $versionText"
}

& python -m pip install -e .
& "$PSScriptRoot/install-git-hooks.ps1"
& python -m pytest backend/tests/unit/tooling
