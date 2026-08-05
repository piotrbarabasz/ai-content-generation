param(
    [string]$VoiceKey = 'all',

    [string]$RuntimeRoot = $(Join-Path (Split-Path -Parent $PSScriptRoot) '.runtime\piper')
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$RepoRoot = Split-Path -Parent $PSScriptRoot
$BackendRoot = Join-Path $RepoRoot 'backend'
$PythonPath = Join-Path $RepoRoot '.venv-piper311\Scripts\python.exe'

function Fail {
    param([string]$Message)

    Write-Error $Message
    exit 1
}

function Assert-Python311 {
    param([string]$Path)

    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        Fail "Missing Piper interpreter: $Path"
    }

    $versionText = & $Path -c "import sys; print(f'{sys.version_info[0]}.{sys.version_info[1]}.{sys.version_info[2]}')"
    if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($versionText)) {
        Fail 'Unable to read the Piper interpreter version.'
    }

    $version = $versionText.Trim()
    if (-not $version.StartsWith('3.11.')) {
        Fail "Piper health check must use Python 3.11, found $version."
    }
}

function Get-CatalogPayload {
    param([string]$Path)

    $env:PYTHONPATH = $BackendRoot
    $probe = @'
import json
from app.providers.piper_catalog import list_piper_voice_catalog

print(json.dumps([entry.to_catalog_payload() for entry in list_piper_voice_catalog()], sort_keys=True))
'@

    $output = & $Path -c $probe 2>&1
    if ($LASTEXITCODE -ne 0 -or -not $output) {
        Fail "Failed to load Piper catalog metadata:`n$($output | Out-String)"
    }

    return $output | Select-Object -Last 1 | ConvertFrom-Json
}

function Get-FileDigest {
    param([string]$Path)

    $md5 = (Get-FileHash -LiteralPath $Path -Algorithm MD5).Hash.ToLowerInvariant()
    $sha256 = (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()
    return [ordered]@{
        md5 = $md5
        sha256 = $sha256
    }
}

Assert-Python311 -Path $PythonPath
$catalog = Get-CatalogPayload -Path $PythonPath

if ($VoiceKey -ne 'all' -and ($catalog.provider_key -notcontains $VoiceKey)) {
    Fail "Unknown Piper voice '$VoiceKey'."
}

$selectedCatalog = if ($VoiceKey -eq 'all') { $catalog } else { @($catalog | Where-Object { $_.provider_key -eq $VoiceKey }) }

$results = @()
$summary = [ordered]@{
    pass = 0
    fail = 0
    missing = 0
}

foreach ($entry in $selectedCatalog) {
    $voiceRoot = Join-Path $RuntimeRoot $entry.provider_key
    $missing = @()
    $mismatched = @()
    $files = @()

    foreach ($relativePath in $entry.required_files) {
        $runtimeFile = Join-Path $voiceRoot ($relativePath -replace '/', '\')
        if (-not (Test-Path -LiteralPath $runtimeFile -PathType Leaf)) {
            $missing += $relativePath
            continue
        }

        $actual = Get-FileDigest -Path $runtimeFile
        $expected = [string]$entry.checksums.PSObject.Properties[$relativePath].Value
        $matchedAlgorithm = $null
        if ($actual.md5 -eq $expected) {
            $matchedAlgorithm = 'md5'
        }
        elseif ($actual.sha256 -eq $expected) {
            $matchedAlgorithm = 'sha256'
        }

        if (-not $matchedAlgorithm) {
            $mismatched += [ordered]@{
                path = $relativePath
                expected = $expected
                md5 = $actual.md5
                sha256 = $actual.sha256
            }
        }
        else {
            $files += [ordered]@{
                path = $relativePath
                checksum = $expected
                algorithm = $matchedAlgorithm
            }
        }
    }

    $status = 'pass'
    $reason = $null
    if ($missing.Count -gt 0) {
        $status = 'missing'
        $reason = "Missing $($missing.Count) file(s)."
    }
    elseif ($mismatched.Count -gt 0) {
        $status = 'fail'
        $reason = "Checksum mismatch in $($mismatched.Count) file(s)."
    }

    switch ($status) {
        'pass' { $summary.pass++ }
        'fail' { $summary.fail++ }
        'missing' { $summary.missing++ }
    }

    $results += [ordered]@{
        voice_key = $entry.provider_key
        voice_name = $entry.voice_name
        status = $status
        reason = $reason
        sample_rate_hz = $entry.expected_sample_rate_hz
        source_revision = $entry.source_revision
        engine_license_identifier = $entry.license_identifier.engine
        model_license_identifier = $entry.license_identifier.model
        runtime_root = $voiceRoot
        files = $files
        missing_files = $missing
        mismatched_files = $mismatched
    }
}

$report = [ordered]@{
    generated_at = (Get-Date).ToString('o')
    summary = $summary
    runtime_root = $RuntimeRoot
    voices = $results
}

Write-Host 'Piper runtime health check'
foreach ($result in $results) {
    $line = '{0}: {1}' -f $result.voice_key, $result.status
    if ($result.reason) {
        $line = $line + " - $($result.reason)"
    }
    Write-Host $line
}

$report | ConvertTo-Json -Depth 8

if ($summary.fail -gt 0 -or $summary.missing -gt 0) {
    exit 1
}
