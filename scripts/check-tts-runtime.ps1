param(
    [ValidateSet('all', 'tts311', 'piper311', 'xtts311')]
    [string]$Profile = 'all'
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$RepoRoot = Split-Path -Parent $PSScriptRoot
$BackendRoot = Join-Path $RepoRoot 'backend'
$BootstrapPython = Join-Path $RepoRoot '.venv-ci311\Scripts\python.exe'

function Fail {
    param([string]$Message)

    Write-Error $Message
    exit 1
}

function Get-ProfileRoot {
    param([string]$ProfileName)

    if ($ProfileName -eq 'ci311') {
        return Join-Path $RepoRoot '.venv-ci311'
    }

    Join-Path $RepoRoot ".venv-$ProfileName"
}

function Get-ProfilePython {
    param([string]$ProfileName)

    Join-Path (Get-ProfileRoot -ProfileName $ProfileName) 'Scripts\python.exe'
}

function Invoke-ProfileCheck {
    param([string]$ProfileName)

    $pythonPath = Get-ProfilePython -ProfileName $ProfileName
    if (-not (Test-Path -LiteralPath $pythonPath -PathType Leaf)) {
        return [ordered]@{
            profile = $ProfileName
            interpreter = $pythonPath
            status = 'missing'
            reason = 'Interpreter not found.'
        }
    }

    $versionText = & $pythonPath -c "import sys; print(f'{sys.version_info[0]}.{sys.version_info[1]}.{sys.version_info[2]}')"
    if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($versionText)) {
        return [ordered]@{
            profile = $ProfileName
            interpreter = $pythonPath
            status = 'fail'
            reason = 'Unable to detect Python version.'
        }
    }

    $details = [ordered]@{
        python_version = $versionText.Trim()
    }

    if ($ProfileName -eq 'tts311') {
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

        $probeOutput = & $pythonPath -c $probe 2>&1
        if ($LASTEXITCODE -ne 0) {
            return [ordered]@{
                profile = $ProfileName
                interpreter = $pythonPath
                status = 'fail'
                reason = ($probeOutput | Out-String).Trim()
                details = $details
            }
        }

        $probeReport = $probeOutput | Select-Object -Last 1 | ConvertFrom-Json
        $details.torch_version = $probeReport.torch_version
        $details.torchaudio_version = $probeReport.torchaudio_version
        $details.cuda_visible = $probeReport.cuda_visible
        $details.provider_import = $probeReport.provider_import
        return [ordered]@{
            profile = $ProfileName
            interpreter = $pythonPath
            status = 'pass'
            details = $details
        }
    }

    return [ordered]@{
        profile = $ProfileName
        interpreter = $pythonPath
        status = 'pass'
        details = $details
    }
}

$profiles = if ($Profile -eq 'all') { @('ci311', 'tts311', 'piper311', 'xtts311') } else { @($Profile) }

if (-not (Test-Path -LiteralPath $BootstrapPython -PathType Leaf)) {
    Fail "Missing bootstrap interpreter: $BootstrapPython"
}

$summary = [ordered]@{
    pass = 0
    fail = 0
    missing = 0
}

$results = @()
foreach ($profileName in $profiles) {
    $result = Invoke-ProfileCheck -ProfileName $profileName
    $results += $result
    switch ($result.status) {
        'pass' { $summary.pass++ }
        'fail' { $summary.fail++ }
        'missing' { $summary.missing++ }
    }
}

$report = [ordered]@{
    generated_at = (Get-Date).ToString('o')
    summary = $summary
    profiles = $results
}

Write-Host 'TTS runtime health check'
foreach ($result in $results) {
    $line = '{0}: {1}' -f $result.profile, $result.status
    if ($result.reason) {
        $line = $line + " - $($result.reason)"
    }
    Write-Host $line
}

$report | ConvertTo-Json -Depth 8

if ($summary.fail -gt 0 -or $summary.missing -gt 0) {
    exit 1
}
