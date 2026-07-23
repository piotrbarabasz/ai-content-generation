Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Fail {
    param([string]$Message)
    Write-Error $Message
    exit 1
}

try {
    $root = git rev-parse --show-toplevel
} catch {
    Fail "HOOK_INSTALL: FAIL`nreason: not inside a git repository"
}

if (-not $root) {
    Fail "HOOK_INSTALL: FAIL`nreason: not inside a git repository"
}

Set-Location $root

$hookPaths = @('.githooks/pre-commit', '.githooks/pre-push')
foreach ($hookPath in $hookPaths) {
    if (-not (Test-Path -LiteralPath $hookPath -PathType Leaf)) {
        Fail "HOOK_INSTALL: FAIL`nreason: missing hook file $hookPath"
    }
}

$pythonBin = & python -c "import sys; print(sys.executable)"
git config --local agent.python $pythonBin
$storedPython = git config --local --get agent.python
if ($storedPython -ne $pythonBin) {
    Fail "HOOK_INSTALL: FAIL`nreason: expected agent.python to match the active interpreter"
}

git config --local core.hooksPath .githooks
$hooksPath = git config --local --get core.hooksPath

if ($hooksPath -ne '.githooks') {
    Fail "HOOK_INSTALL: FAIL`nreason: expected .githooks, got $hooksPath"
}

Write-Output 'HOOK_INSTALL: PASS'
Write-Output 'HOOKS_PATH: .githooks'
