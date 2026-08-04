param(
    [ValidateSet('tts311')]
    [string]$Profile = 'tts311',
    [string]$Text,
    [string]$Output
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$RepoRoot = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $RepoRoot ".venv-$Profile\Scripts\python.exe"

if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) {
    throw "Missing runtime profile $Profile: $Python"
}

if ([string]::IsNullOrWhiteSpace($Text)) {
    $Text = Read-Host 'Enter text to synthesize'
}

if ([string]::IsNullOrWhiteSpace($Text)) {
    throw 'Text must not be empty.'
}

if ([string]::IsNullOrWhiteSpace($Output)) {
    $Timestamp = Get-Date -Format 'yyyyMMdd-HHmmss'
    $Output = ".runtime\tts-demo\demo-$Timestamp.wav"
}

$OutputPath = [System.IO.Path]::GetFullPath(
    (Join-Path $RepoRoot $Output)
)

$env:PYTHONPATH = Join-Path $RepoRoot 'backend'

Write-Host ''
Write-Host 'Generating speech...'
Write-Host "Output file: $OutputPath"
Write-Host ''

& $Python `
    -m app.tooling.tts_smoke `
    --provider chatterbox_v3 `
    --text $Text `
    --output $OutputPath `
    --language pl `
    --device cuda `
    --overwrite

if ($LASTEXITCODE -ne 0) {
    throw 'Speech generation failed.'
}

Write-Host ''
Write-Host "Done: $OutputPath"

Invoke-Item $OutputPath
