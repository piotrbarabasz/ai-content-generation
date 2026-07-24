param(
    [string]$RepoPath = "D:\Projects\ai-content-generation",
    [string]$BranchName = "feat/local-autopilot-ui",
    [string]$BaseBranch = "master",
    [string]$RemoteName = "origin"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Fail {
    param([string]$Message)
    Write-Host ""
    Write-Host "ERROR: $Message" -ForegroundColor Red
    exit 1
}

function Invoke-Git {
    param([Parameter(ValueFromRemainingArguments = $true)][string[]]$GitArgs)

    & git @GitArgs
    if ($LASTEXITCODE -ne 0) {
        Fail "Git command failed: git $($GitArgs -join ' ')"
    }
}

if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
    Fail "Git is not available in PATH."
}

if (-not (Test-Path -LiteralPath $RepoPath -PathType Container)) {
    Fail "Repository path does not exist: $RepoPath"
}

Set-Location -LiteralPath $RepoPath

$insideRepo = (& git rev-parse --is-inside-work-tree 2>$null)
if ($LASTEXITCODE -ne 0 -or $insideRepo.Trim() -ne "true") {
    Fail "The selected path is not a Git repository: $RepoPath"
}

$bootstrapFiles = @(
    "scripts/create_local_autopilot_branch.ps1",
    "scripts/create_local_autopilot_branch.cmd",
    "scripts/LOCAL_AUTOPILOT_CODEX_PROMPTS_PL.md"
)

$statusLines = @(& git status --porcelain)
if ($LASTEXITCODE -ne 0) {
    Fail "Could not read repository status."
}

$unexpectedChanges = @()
foreach ($line in $statusLines) {
    if ([string]::IsNullOrWhiteSpace($line)) {
        continue
    }

    if (-not $line.StartsWith("?? ")) {
        $unexpectedChanges += $line
        continue
    }

    $relativePath = $line.Substring(3).Replace("\", "/")
    if ($bootstrapFiles -notcontains $relativePath) {
        $unexpectedChanges += $line
    }
}

if ($unexpectedChanges.Count -gt 0) {
    Write-Host ""
    Write-Host "Unexpected repository changes:" -ForegroundColor Yellow
    $unexpectedChanges | ForEach-Object { Write-Host $_ }
    Fail "Commit, discard, or move these changes before creating the autopilot branch."
}

Write-Host "Updating $BaseBranch..." -ForegroundColor Cyan
Invoke-Git switch $BaseBranch
Invoke-Git pull --ff-only $RemoteName $BaseBranch

& git show-ref --verify --quiet "refs/heads/$BranchName"
$localBranchExists = ($LASTEXITCODE -eq 0)

if ($localBranchExists) {
    Write-Host "Local branch already exists. Switching to $BranchName..." -ForegroundColor Yellow
    Invoke-Git switch $BranchName
}
else {
    Write-Host "Creating $BranchName from $BaseBranch..." -ForegroundColor Cyan
    Invoke-Git switch -c $BranchName $BaseBranch
}

$currentBranch = (& git branch --show-current).Trim()
if ($currentBranch -ne $BranchName) {
    Fail "Expected branch $BranchName, but current branch is $currentBranch."
}

$existingBootstrapFiles = @()
foreach ($file in $bootstrapFiles) {
    if (Test-Path -LiteralPath $file -PathType Leaf) {
        $existingBootstrapFiles += $file
    }
}

if ($existingBootstrapFiles.Count -gt 0) {
    $addArgs = @("add", "--") + $existingBootstrapFiles
    & git @addArgs
    if ($LASTEXITCODE -ne 0) {
        Fail "Could not stage bootstrap files."
    }

    & git diff --cached --quiet
    $cachedDiffExitCode = $LASTEXITCODE

    if ($cachedDiffExitCode -eq 1) {
        Invoke-Git diff --cached --check
        Invoke-Git commit -m "chore(autopilot): add local bootstrap files"
    }
    elseif ($cachedDiffExitCode -gt 1) {
        Fail "Could not inspect staged bootstrap changes."
    }
}

$head = (& git rev-parse HEAD).Trim()
$statusAfter = @(& git status --porcelain)

Write-Host ""
Write-Host "BRANCH_READY: yes" -ForegroundColor Green
Write-Host "REPO: $RepoPath"
Write-Host "BASE: $BaseBranch"
Write-Host "BRANCH: $currentBranch"
Write-Host "HEAD: $head"
Write-Host "WORKTREE_CLEAN: $([bool]($statusAfter.Count -eq 0))"
Write-Host ""
Write-Host "Next step: open scripts/LOCAL_AUTOPILOT_CODEX_PROMPTS_PL.md and run PROMPT 0 in Codex."