param(
    [switch]$RunSmoke
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$RepoRoot = Split-Path -Parent $PSScriptRoot
$BackendRoot = Join-Path $RepoRoot 'backend'
$BootstrapPython = Join-Path $RepoRoot '.venv-ci311\Scripts\python.exe'
$ProfileNames = @('tts311', 'piper311', 'xtts311')

function Fail {
    param([string]$Message)

    Write-Error $Message
    exit 1
}

function Get-ProfileRoot {
    param([string]$ProfileName)

    Join-Path $RepoRoot ".venv-$ProfileName"
}

function Get-ProfilePython {
    param([string]$ProfileName)

    Join-Path (Get-ProfileRoot -ProfileName $ProfileName) 'Scripts\python.exe'
}

function Assert-Python311 {
    param(
        [string]$PythonPath,
        [string]$Label
    )

    if (-not (Test-Path -LiteralPath $PythonPath -PathType Leaf)) {
        Fail "Missing $Label interpreter: $PythonPath"
    }

    $versionText = & $PythonPath -c "import sys; print(f'{sys.version_info[0]}.{sys.version_info[1]}.{sys.version_info[2]}')"
    if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($versionText)) {
        Fail "Unable to detect Python version from $Label."
    }

    $version = $versionText.Trim()
    if (-not $version.StartsWith('3.11.')) {
        Fail "$Label must use Python 3.11, found $version."
    }

    return $version
}

function Invoke-ChatterboxProbe {
    param([string]$PythonPath)

    $env:PYTHONPATH = $BackendRoot
    $probe = @'
import json
import sys

import torch
import torchaudio

from app.providers.chatterbox_v3 import ChatterboxV3Provider

report = {
    "python_version": f"{sys.version_info[0]}.{sys.version_info[1]}.{sys.version_info[2]}",
    "torch_version": torch.__version__,
    "torchaudio_version": torchaudio.__version__,
    "cuda_visible": bool(torch.cuda.is_available()),
    "provider_import": ChatterboxV3Provider.__name__,
}

if report["torch_version"] != "2.6.0+cu124":
    raise SystemExit("Expected torch 2.6.0+cu124.")
if report["torchaudio_version"] != "2.6.0+cu124":
    raise SystemExit("Expected torchaudio 2.6.0+cu124.")
if not report["cuda_visible"]:
    raise SystemExit("CUDA must be visible.")

print(json.dumps(report, sort_keys=True))
'@

    $probeOutput = & $PythonPath -c $probe 2>&1
    if ($LASTEXITCODE -ne 0) {
        Fail "Chatterbox profile validation failed:`n$probeOutput"
    }

    return ($probeOutput | Select-Object -Last 1 | ConvertFrom-Json)
}

function Invoke-DemoSmoke {
    param([string]$OutputPath)

    $DemoScript = Join-Path $PSScriptRoot 'run-tts-demo.ps1'
    & $DemoScript -Profile tts311 -Text 'Za oknem powoli zapadal wieczor.' -Output $OutputPath
    if ($LASTEXITCODE -ne 0) {
        Fail 'Optional Chatterbox smoke run failed.'
    }
}

if (-not (Test-Path -LiteralPath $BootstrapPython -PathType Leaf)) {
    Fail "Missing bootstrap interpreter: $BootstrapPython"
}

$bootstrapVersion = Assert-Python311 -PythonPath $BootstrapPython -Label '.venv-ci311'
$profileResults = @()

foreach ($profileName in $ProfileNames) {
    $profileRoot = Get-ProfileRoot -ProfileName $profileName
    $profilePython = Get-ProfilePython -ProfileName $profileName
    $created = $false

    if (-not (Test-Path -LiteralPath $profilePython -PathType Leaf)) {
        & $BootstrapPython -m venv $profileRoot
        if ($LASTEXITCODE -ne 0) {
            Fail "Failed to create profile environment: $profileRoot"
        }
        $created = $true
    }

    $pythonVersion = Assert-Python311 -PythonPath $profilePython -Label ".venv-$profileName"

    $profileResults += [ordered]@{
        profile = $profileName
        environment = $profileRoot
        interpreter = $profilePython
        created = $created
        python_version = $pythonVersion
    }
}

$chatterboxProbe = Invoke-ChatterboxProbe -PythonPath (Get-ProfilePython -ProfileName 'tts311')
$smokeResult = [ordered]@{
    enabled = [bool]$RunSmoke
    status = 'skipped'
    output = $null
}

if ($RunSmoke) {
    $smokeOutput = Join-Path $RepoRoot '.runtime\tts-setup\setup-smoke.wav'
    Invoke-DemoSmoke -OutputPath $smokeOutput
    $smokeResult.status = 'completed'
    $smokeResult.output = $smokeOutput
}

$report = [ordered]@{
    generated_at = (Get-Date).ToString('o')
    bootstrap = [ordered]@{
        interpreter = $BootstrapPython
        python_version = $bootstrapVersion
    }
    profiles = $profileResults
    chatterbox_probe = $chatterboxProbe
    smoke = $smokeResult
}

Write-Host 'TTS runtime setup completed.'
$report | ConvertTo-Json -Depth 8
