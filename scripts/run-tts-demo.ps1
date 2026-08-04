param(
    [string]$Text,
    [string]$Output
)

$ErrorActionPreference = "Stop"

$RepoRoot = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $RepoRoot ".venv-tts311\Scripts\python.exe"

if (-not (Test-Path $Python)) {
    throw "Brak środowiska .venv-tts311. Najpierw utwórz i zainstaluj środowisko TTS."
}

if ([string]::IsNullOrWhiteSpace($Text)) {
    $Text = Read-Host "Wpisz tekst do wygenerowania"
}

if ([string]::IsNullOrWhiteSpace($Text)) {
    throw "Tekst nie może być pusty."
}

if ([string]::IsNullOrWhiteSpace($Output)) {
    $Timestamp = Get-Date -Format "yyyyMMdd-HHmmss"
    $Output = ".runtime\tts-smoke\demo-$Timestamp.wav"
}

$OutputPath = [System.IO.Path]::GetFullPath(
    (Join-Path $RepoRoot $Output)
)

$env:PYTHONPATH = Join-Path $RepoRoot "backend"

Write-Host ""
Write-Host "Generowanie głosu..."
Write-Host "Plik wyjściowy: $OutputPath"
Write-Host ""

& $Python `
    -m app.tooling.tts_smoke `
    --provider chatterbox_v3 `
    --text $Text `
    --output $OutputPath `
    --language pl `
    --device cuda `
    --overwrite

if ($LASTEXITCODE -ne 0) {
    throw "Generowanie głosu zakończyło się błędem."
}

Write-Host ""
Write-Host "Gotowe: $OutputPath"

Invoke-Item $OutputPath
