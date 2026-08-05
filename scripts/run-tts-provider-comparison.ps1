param(
    [string]$Text,
    [string]$InputTextFile,
    [string]$OutputDir = $(Join-Path (Split-Path -Parent $PSScriptRoot) ".runtime\tts-comparison\comparison-$((Get-Date).ToString('yyyyMMdd-HHmmss'))"),
    [string[]]$Profile,
    [switch]$Overwrite
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$RepoRoot = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $RepoRoot '.venv-ci311\Scripts\python.exe'

if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) {
    throw "Missing runtime interpreter: $Python"
}

if ([System.IO.Path]::IsPathRooted($OutputDir)) {
    $OutputPath = [System.IO.Path]::GetFullPath($OutputDir)
}
else {
    $OutputPath = [System.IO.Path]::GetFullPath((Join-Path $RepoRoot $OutputDir))
}

$env:PYTHONPATH = Join-Path $RepoRoot 'backend'

$arguments = @(
    '-m', 'app.tooling.tts_compare',
    '--output-dir', $OutputPath
)

if (-not [string]::IsNullOrWhiteSpace($Text)) {
    $arguments += @('--text', $Text)
}
elseif (-not [string]::IsNullOrWhiteSpace($InputTextFile)) {
    $arguments += @('--input-text-file', $InputTextFile)
}
else {
    $Text = Read-Host 'Enter text to compare'
    if ([string]::IsNullOrWhiteSpace($Text)) {
        throw 'Text must not be empty.'
    }
    $arguments += @('--text', $Text)
}

if ($Profile) {
    foreach ($item in $Profile) {
        $arguments += @('--profile', $item)
    }
}

if ($Overwrite) {
    $arguments += '--overwrite'
}

& $Python @arguments

if ($LASTEXITCODE -ne 0) {
    throw 'TTS comparison failed.'
}
