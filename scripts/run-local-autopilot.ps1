$ErrorActionPreference = 'Stop'

$repoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $repoRoot

$python = (& git config --local --get agent.python).Trim()
if (-not $python) {
  throw 'agent.python is not configured'
}
if (-not (Test-Path -LiteralPath $python)) {
  throw "agent.python does not point to an existing interpreter: $python"
}

$version = & $python -c "import sys; print(f'{sys.version_info[0]}.{sys.version_info[1]}.{sys.version_info[2]}')"
$parts = $version.Trim().Split('.')
if ([int]$parts[0] -lt 3 -or ([int]$parts[0] -eq 3 -and [int]$parts[1] -lt 11)) {
  throw "Python 3.11 or newer is required; found $version"
}

& $python -m backend.app.tooling.local_autopilot @args
exit $LASTEXITCODE
