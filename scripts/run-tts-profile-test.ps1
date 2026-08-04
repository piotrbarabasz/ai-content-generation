param(
    [string]$Text = "Za oknem powoli zapadał wieczór. Ulice cichły, a światła miasta odbijały się w mokrym bruku. Był to jeden z tych spokojnych momentów, w których czas zdawał się płynąć trochę wolniej."
)

$ErrorActionPreference = "Stop"

$RepoRoot = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $RepoRoot ".venv-tts311\Scripts\python.exe"
$Backend = Join-Path $RepoRoot "backend"
$OutputDir = Join-Path $RepoRoot ".runtime\tts-profile-test"

if (-not (Test-Path $Python)) {
    throw "Nie znaleziono środowiska .venv-tts311."
}

New-Item -ItemType Directory -Force $OutputDir | Out-Null

$env:PYTHONPATH = $Backend

$Profiles = @(
    [PSCustomObject]@{
        Name = "01-neutralny"
        Label = "Neutralny"
        CfgWeight = 0.5
        Exaggeration = 0.5
    },
    [PSCustomObject]@{
        Name = "02-spokojniejszy"
        Label = "Spokojniejszy"
        CfgWeight = 0.3
        Exaggeration = 0.35
    },
    [PSCustomObject]@{
        Name = "03-dramatyczny"
        Label = "Dramatyczny"
        CfgWeight = 0.3
        Exaggeration = 0.7
    }
)

foreach ($Profile in $Profiles) {
    $WavPath = Join-Path $OutputDir "$($Profile.Name).wav"
    $ReportPath = Join-Path $OutputDir "$($Profile.Name).json"

    Write-Host ""
    Write-Host "========================================"
    Write-Host "Profil: $($Profile.Label)"
    Write-Host "cfg_weight: $($Profile.CfgWeight)"
    Write-Host "exaggeration: $($Profile.Exaggeration)"
    Write-Host "========================================"

    & $Python `
        -m app.tooling.tts_smoke `
        --provider chatterbox_v3 `
        --text $Text `
        --output $WavPath `
        --report $ReportPath `
        --language pl `
        --device cuda `
        --cfg-weight $Profile.CfgWeight `
        --exaggeration $Profile.Exaggeration `
        --temperature 0.8 `
        --repetition-penalty 1.2 `
        --min-p 0.05 `
        --top-p 1.0 `
        --overwrite

    if ($LASTEXITCODE -ne 0) {
        throw "Generowanie profilu '$($Profile.Label)' zakończyło się błędem."
    }
}

$TestInfo = [ordered]@{
    generated_at = (Get-Date).ToString("o")
    text = $Text
    constant_settings = [ordered]@{
        provider = "chatterbox_v3"
        language = "pl"
        device = "cuda"
        temperature = 0.8
        repetition_penalty = 1.2
        min_p = 0.05
        top_p = 1.0
    }
    profiles = $Profiles
}

$TestInfo |
    ConvertTo-Json -Depth 5 |
    Set-Content `
        -Path (Join-Path $OutputDir "test-info.json") `
        -Encoding UTF8

@"
01-neutralny.wav
02-spokojniejszy.wav
03-dramatyczny.wav
"@ | Set-Content `
    -Path (Join-Path $OutputDir "profiles.m3u") `
    -Encoding ASCII

Write-Host ""
Write-Host "Test zakończony."
Write-Host "Wyniki: $OutputDir"

Invoke-Item $OutputDir
