param(
    [Parameter(Mandatory = $true)]
    [string]$VoiceKey,

    [string]$RuntimeRoot = $(Join-Path (Split-Path -Parent $PSScriptRoot) '.runtime\piper'),

    [switch]$InstallRuntime
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
        Fail "Piper setup must use Python 3.11, found $version."
    }
}

function Get-CatalogPayload {
    param(
        [string]$Path,
        [string]$Key
    )

    $env:PYTHONPATH = $BackendRoot
    $probe = @'
import json
import sys

from app.providers.piper_catalog import PiperCatalogError, get_piper_voice_catalog_entry, list_piper_voice_keys

voice_key = sys.argv[1]
supported = list(list_piper_voice_keys())
try:
    entry = get_piper_voice_catalog_entry(voice_key)
except PiperCatalogError as exc:
    print(json.dumps({
        "ok": False,
        "error": str(exc),
        "supported": supported,
    }, sort_keys=True))
else:
    print(json.dumps({
        "ok": True,
        "entry": entry.to_catalog_payload(),
    }, sort_keys=True))
'@

    $output = & $Path -c $probe $Key 2>&1
    if ($LASTEXITCODE -ne 0 -or -not $output) {
        Fail "Failed to resolve Piper catalog metadata:`n$($output | Out-String)"
    }

    $payload = $output | Select-Object -Last 1 | ConvertFrom-Json
    if (-not $payload.ok) {
        $supported = ($payload.supported -join ', ')
        Fail "Unknown Piper voice '$Key'. Supported Polish voices: $supported."
    }

    return $payload.entry
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

if ($InstallRuntime) {
    & $PythonPath -m pip install ".[piper]"
    if ($LASTEXITCODE -ne 0) {
        Fail 'Piper runtime installation failed.'
    }
}

$entry = Get-CatalogPayload -Path $PythonPath -Key $VoiceKey
$voiceKey = $entry.provider_key
$targetRoot = Join-Path $RuntimeRoot $voiceKey
$stagingRoot = Join-Path (Join-Path $RuntimeRoot '_staging') $voiceKey

if (Test-Path -LiteralPath $stagingRoot) {
    Remove-Item -LiteralPath $stagingRoot -Recurse -Force
}
if (Test-Path -LiteralPath $targetRoot) {
    Remove-Item -LiteralPath $targetRoot -Recurse -Force
}

New-Item -ItemType Directory -Path $stagingRoot -Force | Out-Null

$downloaded = @()
foreach ($relativePath in $entry.download_urls.PSObject.Properties.Name) {
    $url = [string]$entry.download_urls.PSObject.Properties[$relativePath].Value
    $stagingFile = Join-Path $stagingRoot ($relativePath -replace '/', '\')
    $stagingDir = Split-Path -Parent $stagingFile
    New-Item -ItemType Directory -Path $stagingDir -Force | Out-Null

    Write-Host "Downloading $relativePath"
    Invoke-WebRequest -Uri $url -OutFile $stagingFile -UseBasicParsing

    $actual = Get-FileDigest -Path $stagingFile
    $expected = [string]$entry.checksums.PSObject.Properties[$relativePath].Value
    $matchedAlgorithm = $null
    if ($actual.md5 -eq $expected) {
        $matchedAlgorithm = 'md5'
    }
    elseif ($actual.sha256 -eq $expected) {
        $matchedAlgorithm = 'sha256'
    }

    if (-not $matchedAlgorithm) {
        Remove-Item -LiteralPath $stagingRoot -Recurse -Force
        Fail "Checksum mismatch for $relativePath. Expected $expected."
    }

    $downloaded += [ordered]@{
        path = $relativePath
        checksum = $expected
        algorithm = $matchedAlgorithm
    }
}

New-Item -ItemType Directory -Path $targetRoot -Force | Out-Null
Get-ChildItem -LiteralPath $stagingRoot -Recurse -File | ForEach-Object {
    $relative = $_.FullName.Substring($stagingRoot.Length).TrimStart('\')
    $destination = Join-Path $targetRoot $relative
    $destinationDir = Split-Path -Parent $destination
    New-Item -ItemType Directory -Path $destinationDir -Force | Out-Null
    Move-Item -LiteralPath $_.FullName -Destination $destination
}
Remove-Item -LiteralPath $stagingRoot -Recurse -Force

$report = [ordered]@{
    status = 'ready'
    voice_key = $voiceKey
    voice_name = $entry.voice_name
    language_id = $entry.language_id
    quality = $entry.quality
    expected_sample_rate_hz = $entry.expected_sample_rate_hz
    source_repository = $entry.source_repository
    source_revision = $entry.source_revision
    engine_license_identifier = $entry.license_identifier.engine
    model_license_identifier = $entry.license_identifier.model
    runtime_root = $targetRoot
    files = $downloaded
    next_step = "Run scripts\check-piper-runtime.ps1 -VoiceKey $voiceKey"
}

Write-Host "Piper voice $voiceKey verified and activated."
$report | ConvertTo-Json -Depth 8
