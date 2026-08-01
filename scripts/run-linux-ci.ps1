[CmdletBinding()]
param(
    [string]$Distribution = 'Ubuntu-24.04',
    [switch]$KeepWorkspace,
    [switch]$Help
)

$ErrorActionPreference = 'Stop'

$script:RepoRoot = $null
$script:RuntimeDir = $null
$script:Timestamp = $null
$script:BundlePath = $null
$script:SummaryPath = $null
$script:LatestSummaryPath = $null
$script:LogPath = $null

function Show-Help {
    @'
Usage:
  powershell -ExecutionPolicy Bypass -File scripts/run-linux-ci.ps1 [-Distribution <name>] [-KeepWorkspace] [-Help]

What it does:
  - checks that WSL is available and the selected distribution exists
  - checks that the repository is clean and resolves the current HEAD and branch
  - creates a git bundle with HEAD and master under .specify/runtime/local-ci/
  - runs the Linux validation script inside WSL on a native Linux filesystem clone
  - writes a run summary to .specify/runtime/local-ci/latest.json

Defaults:
  -Distribution Ubuntu-24.04
  -KeepWorkspace keeps the Linux clone after a failure
'@ | Write-Host
}

function Fail {
    param([string]$Message, [int]$ExitCode = 1)
    Write-Error $Message
    exit $ExitCode
}

function Write-LocalCiLog {
    param([string]$Message)
    Write-Host $Message
    if ($script:LogPath) {
        Add-Content -LiteralPath $script:LogPath -Value $Message
    }
}

function Write-AtomicJson {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path,
        [Parameter(Mandatory = $true)]
        [object]$Value
    )

    $directory = Split-Path -Parent $Path
    if ($directory) {
        New-Item -ItemType Directory -Force -Path $directory | Out-Null
    }
    $json = $Value | ConvertTo-Json -Depth 32
    $tempName = Join-Path $directory ('.' + [System.IO.Path]::GetFileName($Path) + '.' + ([guid]::NewGuid().ToString('N')) + '.tmp')
    try {
        [System.IO.File]::WriteAllText($tempName, ($json + [Environment]::NewLine), [System.Text.UTF8Encoding]::new($false))
        Move-Item -LiteralPath $tempName -Destination $Path -Force
    } finally {
        if (Test-Path -LiteralPath $tempName) {
            Remove-Item -LiteralPath $tempName -Force
        }
    }
}

function Convert-ToWslPath {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path,
        [Parameter(Mandatory = $true)]
        [string]$DistributionName
    )

    $converted = & wsl.exe -d $DistributionName --exec wslpath -u $Path
    if ($LASTEXITCODE -ne 0) {
        Fail "failed to convert path for WSL: $Path"
    }
    return ($converted | Out-String).Trim()
}

function Get-DistributionList {
    $listing = & wsl.exe -l -q
    if ($LASTEXITCODE -ne 0) {
        Fail "unable to enumerate WSL distributions"
    }
    return @($listing | ForEach-Object { $_.Trim().TrimStart('*').Trim() } | Where-Object { $_ })
}

function Get-BranchName {
    $branch = (& git branch --show-current).Trim()
    if (-not $branch) {
        Fail "current branch could not be determined"
    }
    return $branch
}

function Get-HeadSha {
    $headSha = (& git rev-parse HEAD).Trim()
    if (-not $headSha) {
        Fail "current HEAD could not be resolved"
    }
    return $headSha
}

function Write-FailedSummary {
    param(
        [string]$Reason,
        [string]$FailedStage = "",
        [int]$ExitCode = 1
    )

    $branch = Get-BranchName
    $headSha = Get-HeadSha
    $summary = [ordered]@{
        status = 'FAIL'
        reason = $Reason
        failed_stage = $FailedStage
        exit_code = $ExitCode
        failed_tests = @()
        repo_root = $script:RepoRoot
        branch = $branch
        head_sha = $headSha
        bundle_path = $script:BundlePath
        workspace_path = ''
        summary_path = $script:SummaryPath
        log_path = $script:LogPath
        timestamp = $script:Timestamp
        distribution = $Distribution
        keep_workspace = [bool]$KeepWorkspace
    }
    Write-AtomicJson -Path $script:SummaryPath -Value $summary
    if ($script:LatestSummaryPath) {
        Copy-Item -LiteralPath $script:SummaryPath -Destination $script:LatestSummaryPath -Force
    }
}

if ($Help) {
    Show-Help
    exit 0
}

$wsl = Get-Command wsl.exe -ErrorAction SilentlyContinue
if (-not $wsl) {
    Fail "wsl.exe is not available on this machine"
}

$script:RepoRoot = (& git rev-parse --show-toplevel).Trim()
if (-not $script:RepoRoot) {
    Fail "not inside a git repository"
}

Set-Location $script:RepoRoot

$branch = Get-BranchName
$headSha = Get-HeadSha

$status = & git status --porcelain=v1
if ($LASTEXITCODE -ne 0) {
    Fail "failed to inspect git status"
}
if ($status) {
    Fail "working tree must be clean before running local Linux CI"
}

$availableDistributions = Get-DistributionList
if (-not ($availableDistributions -contains $Distribution)) {
    Fail "WSL distribution '$Distribution' is not available. Installed distributions: $($availableDistributions -join ', ')"
}

$script:RuntimeDir = Join-Path $script:RepoRoot '.specify/runtime/local-ci'
New-Item -ItemType Directory -Force -Path $script:RuntimeDir | Out-Null
$script:Timestamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$script:BundlePath = Join-Path $script:RuntimeDir "$($script:Timestamp).bundle"
$script:SummaryPath = Join-Path $script:RuntimeDir "$($script:Timestamp).json"
$script:LatestSummaryPath = Join-Path $script:RuntimeDir 'latest.json'
$script:LogPath = Join-Path $script:RuntimeDir "$($script:Timestamp).log"

New-Item -ItemType File -Force -Path $script:LogPath | Out-Null
Write-LocalCiLog "LOCAL_LINUX_CI: start"
Write-LocalCiLog "distribution: $Distribution"
Write-LocalCiLog "repository: $script:RepoRoot"
Write-LocalCiLog "branch: $branch"
Write-LocalCiLog "head_sha: $headSha"
Write-LocalCiLog "bundle: $script:BundlePath"
Write-LocalCiLog "summary: $script:SummaryPath"
Write-LocalCiLog "log: $script:LogPath"

& git bundle create $script:BundlePath HEAD master
if ($LASTEXITCODE -ne 0) {
    Write-FailedSummary -Reason 'failed to create git bundle with HEAD and master'
    Fail 'failed to create git bundle with HEAD and master'
}

$scriptPath = Join-Path $script:RepoRoot 'scripts/run-linux-ci.sh'
if (-not (Test-Path -LiteralPath $scriptPath -PathType Leaf)) {
    Write-FailedSummary -Reason 'scripts/run-linux-ci.sh is missing'
    Fail 'scripts/run-linux-ci.sh is missing'
}

$bundleWsl = Convert-ToWslPath -Path $script:BundlePath -DistributionName $Distribution
$summaryWsl = Convert-ToWslPath -Path $script:SummaryPath -DistributionName $Distribution
$scriptWsl = Convert-ToWslPath -Path $scriptPath -DistributionName $Distribution
$repoWsl = Convert-ToWslPath -Path $script:RepoRoot -DistributionName $Distribution

Write-LocalCiLog "wsl_script: $scriptWsl"
Write-LocalCiLog "repo_wsl: $repoWsl"

$wslArgs = @(
    '--bundle-path', $bundleWsl,
    '--summary-path', $summaryWsl,
    '--repo-root', $repoWsl,
    '--head-sha', $headSha,
    '--branch', $branch
)
if ($KeepWorkspace) {
    $wslArgs += '--keep-workspace'
}

$stdoutFile = Join-Path $script:RuntimeDir "$($script:Timestamp).wsl.stdout.log"
$stderrFile = Join-Path $script:RuntimeDir "$($script:Timestamp).wsl.stderr.log"
try {
    $process = Start-Process -FilePath $wsl.Path -ArgumentList @('-d', $Distribution, '--exec', '/bin/bash', $scriptWsl) + $wslArgs -Wait -PassThru -NoNewWindow -RedirectStandardOutput $stdoutFile -RedirectStandardError $stderrFile
    $exitCode = $process.ExitCode
    if (Test-Path -LiteralPath $stdoutFile) {
        Get-Content -LiteralPath $stdoutFile | ForEach-Object {
            Write-Host $_
            Add-Content -LiteralPath $script:LogPath -Value $_
        }
    }
    if (Test-Path -LiteralPath $stderrFile) {
        Get-Content -LiteralPath $stderrFile | ForEach-Object {
            Write-Host $_
            Add-Content -LiteralPath $script:LogPath -Value $_
        }
    }
} finally {
    if (Test-Path -LiteralPath $stdoutFile) {
        Remove-Item -LiteralPath $stdoutFile -Force
    }
    if (Test-Path -LiteralPath $stderrFile) {
        Remove-Item -LiteralPath $stderrFile -Force
    }
}

if (-not (Test-Path -LiteralPath $script:SummaryPath -PathType Leaf)) {
    $summaryExitCode = if ($exitCode -ne 0) { $exitCode } else { 1 }
    Write-FailedSummary -Reason 'WSL runner did not write a summary' -ExitCode $summaryExitCode
}

if (Test-Path -LiteralPath $script:SummaryPath -PathType Leaf) {
    Copy-Item -LiteralPath $script:SummaryPath -Destination $script:LatestSummaryPath -Force
    $summaryText = Get-Content -LiteralPath $script:SummaryPath -Raw
    Write-Host $summaryText
    Add-Content -LiteralPath $script:LogPath -Value $summaryText
}

if ($exitCode -ne 0) {
    Write-LocalCiLog "LOCAL_LINUX_CI: failed"
    Write-LocalCiLog "workspace: $(if (Test-Path -LiteralPath $script:SummaryPath) { (Get-Content -LiteralPath $script:SummaryPath -Raw | ConvertFrom-Json).workspace_path } else { '' })"
    exit $exitCode
}

Write-LocalCiLog "LOCAL_LINUX_CI: pass"
exit 0
