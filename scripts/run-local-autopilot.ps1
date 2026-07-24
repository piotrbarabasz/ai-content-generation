$ErrorActionPreference = 'Stop'

function Initialize-LocalAutopilotEnvironment {
  $npmBin = if ($env:APPDATA) { Join-Path $env:APPDATA 'npm' } else { $null }
  if ($npmBin -and (Test-Path -LiteralPath $npmBin)) {
    $pathEntries = $env:Path -split ';'
    $npmBinPresent = $false
    foreach ($entry in $pathEntries) {
      if ([string]::IsNullOrWhiteSpace($entry)) {
        continue
      }
      if ($entry.TrimEnd('\') -ieq $npmBin.TrimEnd('\')) {
        $npmBinPresent = $true
        break
      }
    }
    if (-not $npmBinPresent) {
      $env:Path = "$npmBin;$env:Path"
    }

    $codexCmd = Join-Path $npmBin 'codex.cmd'
    if (Test-Path -LiteralPath $codexCmd) {
      $env:CODEX_CLI_PATH = $codexCmd
      Write-Host "Codex CLI executable: $codexCmd"
      return $codexCmd
    }
  }

  if ($env:CODEX_CLI_PATH) {
    Write-Host "Codex CLI executable: $($env:CODEX_CLI_PATH)"
    return $env:CODEX_CLI_PATH
  }

  Write-Host 'Codex CLI executable: (resolved via PATH)'
  return $null
}

function Invoke-LocalAutopilot {
  param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]] $Arguments
  )

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

  Initialize-LocalAutopilotEnvironment | Out-Null
  & $python -m backend.app.tooling.local_autopilot @Arguments
  exit $LASTEXITCODE
}

if ($MyInvocation.InvocationName -ne '.') {
  Invoke-LocalAutopilot @args
}
