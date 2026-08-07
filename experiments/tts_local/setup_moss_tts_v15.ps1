[CmdletBinding()]
param(
    [switch]$DependencyOnly,
    [switch]$DownloadModels,
    [switch]$BuildCpu,
    [switch]$BuildCuda,
    [switch]$PrepareGGUF,
    [ValidateSet("None", "Q4_K_M")]
    [string]$Quantize = "None",
    [switch]$CleanupIntermediate,
    [switch]$Force
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$ModelId = "OpenMOSS-Team/MOSS-TTS-v1.5"
$AudioTokenizerId = "OpenMOSS-Team/MOSS-Audio-Tokenizer-ONNX"
$RuntimeUrl = "https://github.com/OpenMOSS/llama.cpp.git"
$RuntimeBranch = "moss-tts-firstclass"
$MossTtsUrl = "https://github.com/OpenMOSS/MOSS-TTS.git"
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$RuntimeRoot = Join-Path $RepoRoot ".runtime\tts-experiments"
$Venv = Join-Path $RuntimeRoot "venvs\moss-v15"
$Python = Join-Path $Venv "Scripts\python.exe"
$HfCli = Join-Path $Venv "Scripts\hf.exe"
$UpstreamRoot = Join-Path $RuntimeRoot "upstream"
$MossCheckout = Join-Path $UpstreamRoot "MOSS-TTS"
$LlamaCheckout = Join-Path $UpstreamRoot "llama.cpp-moss"
$ModelRoot = Join-Path $RuntimeRoot "models\moss-tts-v15"
$HfModelDir = Join-Path $ModelRoot "hf"
$AudioTokenizerDir = Join-Path $ModelRoot "audio-tokenizer"
$OnnxDir = Join-Path $ModelRoot "audio-tokenizer-onnx"
$GgufDir = Join-Path $ModelRoot "gguf"
$F16Gguf = Join-Path $GgufDir "moss_tts_v15_firstclass_f16.gguf"
$Q4Gguf = Join-Path $GgufDir "moss_tts_v15_firstclass_q4_k_m.gguf"
$Q4ReceiptPath = Join-Path $GgufDir "moss_tts_v15_firstclass_q4_k_m.verified.json"
$ProvenancePath = Join-Path $ModelRoot "provenance.json"
$DownloadRevisionPath = Join-Path $ModelRoot "download-revisions.json"
$OutputDir = Join-Path $RuntimeRoot "outputs\moss-tts-v15"
$ReferenceDir = Join-Path $RuntimeRoot "references"
$GiB = [int64](1024 * 1024 * 1024)

function Write-Stage {
    param([int]$Number, [string]$Text)
    Write-Host "[$Number/6] $Text"
}

function Write-Usage {
    Write-Host @"

MOSS-TTS-v1.5 local experiment setup (PowerShell)

No large download or compilation happens without an explicit switch.

  -DependencyOnly   Create/reuse the isolated Python environment.
  -DownloadModels  Download the exact v1.5 checkpoint and ONNX tokenizer.
  -BuildCpu         Build llama-moss-tts and llama-quantize for CPU.
  -BuildCuda        Build llama-moss-tts and llama-quantize with CUDA.
  -PrepareGGUF      Validate the exact MossTTSDelay layout, then invoke the
                    official first-class converter for $ModelId.
  -Quantize Q4_K_M  Quantize the verified v1.5 F16 intermediate with the
                    official OpenMOSS llama-quantize binary, preserving the
                    embedding and output tables in F16.
  -CleanupIntermediate
                    After verified Q4 creation, remove only the v1.5 F16 GGUF.
  -Force            Reinstall/redownload/reconfigure; never deletes or resets.

Examples:
  .\experiments\tts_local\setup_moss_tts_v15.ps1
  .\experiments\tts_local\setup_moss_tts_v15.ps1 -DependencyOnly
  .\experiments\tts_local\setup_moss_tts_v15.ps1 -DependencyOnly -DownloadModels
  .\experiments\tts_local\setup_moss_tts_v15.ps1 -BuildCpu
  .\experiments\tts_local\setup_moss_tts_v15.ps1 -BuildCuda
  .\experiments\tts_local\setup_moss_tts_v15.ps1 -PrepareGGUF
  .\experiments\tts_local\setup_moss_tts_v15.ps1 -PrepareGGUF -Quantize Q4_K_M
"@
}

function Test-Command {
    param([string]$Name)
    return $null -ne (Get-Command $Name -ErrorAction SilentlyContinue)
}

function Invoke-Checked {
    param(
        [Parameter(Mandatory = $true)][string]$FilePath,
        [Parameter(Mandatory = $true)][string[]]$ArgumentList,
        [string]$FailureMessage = "Command failed"
    )
    & $FilePath @ArgumentList
    if ($LASTEXITCODE -ne 0) {
        throw "$FailureMessage (exit code $LASTEXITCODE)."
    }
}

function Get-GitValue {
    param([string]$Checkout, [string[]]$Arguments)
    $value = & git -C $Checkout @Arguments 2>$null
    if ($LASTEXITCODE -ne 0) {
        return $null
    }
    return ($value | Out-String).Trim()
}

function Convert-ToRepoPath {
    param([string]$Path)
    $resolved = [System.IO.Path]::GetFullPath($Path)
    if ($resolved.StartsWith($RepoRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
        return $resolved.Substring($RepoRoot.Length).TrimStart("\").Replace("\", "/")
    }
    return "<external>/" + [System.IO.Path]::GetFileName($resolved)
}

function Get-FreeBytes {
    param([string]$Path)
    $root = [System.IO.Path]::GetPathRoot([System.IO.Path]::GetFullPath($Path))
    return [int64]([System.IO.DriveInfo]::new($root).AvailableFreeSpace)
}

function Assert-FreeDiskSpace {
    param(
        [string]$Operation,
        [int64]$RequiredBytes,
        [string]$Basis
    )
    $freeBytes = Get-FreeBytes -Path $RuntimeRoot
    Write-Host ("Disk precheck for {0}: require {1:N2} GiB; {2:N2} GiB free." -f $Operation, ($RequiredBytes / 1GB), ($freeBytes / 1GB))
    Write-Host "Estimate basis: $Basis"
    if ($freeBytes -lt $RequiredBytes) {
        throw ("Insufficient free disk for {0}. Free at least {1:N2} additional GiB before retrying." -f $Operation, (($RequiredBytes - $freeBytes) / 1GB))
    }
}

function Show-Preflight {
    Write-Stage 1 "Checking prerequisites"
    Write-Host "Windows: $([Environment]::OSVersion.VersionString)"
    Write-Host "PowerShell: $($PSVersionTable.PSVersion)"

    if (Test-Command "py") {
        Write-Host "Python launcher installations:"
        & py -0p 2>$null
    }
    elseif (Test-Command "python") {
        Write-Host "Python: $(& python --version 2>&1)"
    }
    else {
        Write-Warning "Python was not found. Python 3.11 is required for the experiment helpers."
    }

    foreach ($tool in @("git", "cmake")) {
        $command = Get-Command $tool -ErrorAction SilentlyContinue
        if ($null -eq $command) {
            if ($tool -eq "cmake") {
                Write-Warning "CMake was not found. Install it manually on Windows with: winget install --id Kitware.CMake -e"
            }
            else {
                Write-Warning "$tool was not found."
            }
        }
        else {
            Write-Host "${tool}: $($command.Source)"
        }
    }

    $compiler = Get-Command "cl.exe" -ErrorAction SilentlyContinue
    $vswhere = Join-Path ${env:ProgramFiles(x86)} "Microsoft Visual Studio\Installer\vswhere.exe"
    if ($null -ne $compiler) {
        Write-Host "C++ compiler: $($compiler.Source)"
    }
    elseif (Test-Path -LiteralPath $vswhere) {
        $installation = & $vswhere -latest -products * -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath
        if ($installation) {
            Write-Host "Visual Studio C++ tools: $installation"
        }
        else {
            Write-Warning "Visual Studio was found, but the MSVC x64 build tools were not detected."
        }
    }
    else {
        Write-Warning "MSVC/Visual Studio C++ build tools were not detected. Model download and CPU dependencies remain available."
    }

    if (Test-Command "nvidia-smi") {
        Write-Host "NVIDIA GPU:"
        & nvidia-smi --query-gpu=name,memory.total,driver_version --format=csv,noheader
    }
    else {
        Write-Warning "nvidia-smi was not found; CUDA is optional for CPU setup."
    }

    if (Test-Command "nvcc") {
        Write-Host "CUDA compiler: $((Get-Command nvcc).Source)"
        & nvcc --version | Select-Object -Last 1
    }
    else {
        Write-Warning "nvcc was not found; this does not prevent CPU setup."
    }

    try {
        $ram = (Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory
        Write-Host ("System RAM: {0:N1} GiB" -f ($ram / 1GB))
    }
    catch {
        Write-Warning "Could not query total system RAM: $($_.Exception.Message)"
    }

    $driveName = [System.IO.Path]::GetPathRoot($RepoRoot).TrimEnd("\").TrimEnd(":")
    $drive = Get-PSDrive -Name $driveName -ErrorAction SilentlyContinue
    if ($null -ne $drive) {
        Write-Host ("Free disk on {0}: {1:N1} GiB" -f $drive.Root, ($drive.Free / 1GB))
    }
    Write-Host "Expected runtime root: $(Convert-ToRepoPath $RuntimeRoot)"
    Write-Host "Expected model root: $(Convert-ToRepoPath $ModelRoot)"
    Write-Host "Expected output: $(Convert-ToRepoPath (Join-Path $OutputDir 'benchmark.wav'))"
}

function Ensure-Directories {
    foreach ($directory in @(
        $Venv,
        $UpstreamRoot,
        $HfModelDir,
        $AudioTokenizerDir,
        $OnnxDir,
        $GgufDir,
        $ReferenceDir,
        $OutputDir
    )) {
        if (-not (Test-Path -LiteralPath $directory)) {
            New-Item -ItemType Directory -Path $directory -Force | Out-Null
        }
    }
}

function Ensure-PythonEnvironment {
    Write-Stage 2 "Preparing Python environment"
    if (-not (Test-Path -LiteralPath $Python)) {
        if (-not (Test-Command "py")) {
            throw "Python launcher 'py' was not found. Install Python 3.11 first."
        }
        Invoke-Checked -FilePath "py" -ArgumentList @("-3.11", "-m", "venv", $Venv) -FailureMessage "Could not create the Python 3.11 environment"
    }
    if ($DependencyOnly -or $DownloadModels -or $PrepareGGUF -or $Force) {
        Invoke-Checked -FilePath $Python -ArgumentList @("-m", "pip", "install", "--upgrade", "pip", "huggingface_hub", "numpy", "soundfile", "tokenizers", "onnxruntime") -FailureMessage "Could not install isolated helper dependencies"
    }
    Write-Host "Python environment: $(Convert-ToRepoPath $Venv)"
}

function Ensure-GitRepository {
    param(
        [string]$Url,
        [string]$Destination,
        [string]$Branch
    )
    if (-not (Test-Path -LiteralPath $Destination)) {
        $arguments = @("clone", "--single-branch", "--branch", $Branch, $Url, $Destination)
        Invoke-Checked -FilePath "git" -ArgumentList $arguments -FailureMessage "Could not clone $Url"
        return
    }
    if (-not (Test-Path -LiteralPath (Join-Path $Destination ".git"))) {
        throw "Existing path is not a Git checkout and will not be replaced: $(Convert-ToRepoPath $Destination)"
    }
    $origin = Get-GitValue -Checkout $Destination -Arguments @("remote", "get-url", "origin")
    $normalizedOrigin = $origin -replace '\.git$', ''
    $normalizedExpected = $Url -replace '\.git$', ''
    if ($normalizedOrigin -ne $normalizedExpected) {
        throw "Existing checkout has unexpected origin '$origin': $(Convert-ToRepoPath $Destination)"
    }
    $currentBranch = Get-GitValue -Checkout $Destination -Arguments @("branch", "--show-current")
    if ($currentBranch -ne $Branch) {
        throw "Existing checkout is on '$currentBranch', expected '$Branch'. It will not be switched automatically."
    }
    Write-Host "Reusing verified checkout: $(Convert-ToRepoPath $Destination) at $(Get-GitValue -Checkout $Destination -Arguments @('rev-parse', '--short', 'HEAD'))"
}

function Ensure-Sources {
    Write-Stage 3 "Preparing OpenMOSS sources"
    if (-not (Test-Command "git")) {
        throw "Git is required to prepare the official source checkouts."
    }
    Ensure-GitRepository -Url $RuntimeUrl -Destination $LlamaCheckout -Branch $RuntimeBranch
    if ($PrepareGGUF) {
        Ensure-GitRepository -Url $MossTtsUrl -Destination $MossCheckout -Branch "main"
    }
}

function Get-HuggingFaceRevision {
    param([string]$RepositoryId)
    if (-not (Test-Path -LiteralPath $Python)) {
        return $null
    }
    $code = "from huggingface_hub import HfApi; print(HfApi().model_info('$RepositoryId').sha)"
    $revision = & $Python -c $code 2>$null
    if ($LASTEXITCODE -ne 0) {
        return $null
    }
    return ($revision | Out-String).Trim()
}

function Get-HuggingFaceRepositoryBytes {
    param([string]$RepositoryId)
    $code = @"
from huggingface_hub import HfApi
info = HfApi().model_info('$RepositoryId', files_metadata=True)
print(sum((item.size or 0) for item in info.siblings))
"@
    $value = & $Python -c $code 2>$null
    if ($LASTEXITCODE -ne 0 -or -not (($value | Out-String).Trim() -match '^\d+$')) {
        throw "Could not derive current repository size metadata for $RepositoryId."
    }
    return [int64](($value | Out-String).Trim())
}

function Test-HuggingFaceAccess {
    param([string]$RepositoryId)
    $code = "from huggingface_hub import HfApi; info=HfApi().model_info('$RepositoryId'); print(info.id, info.sha)"
    & $Python -c $code
    if ($LASTEXITCODE -ne 0) {
        throw "Cannot access $RepositoryId. If authentication or model terms are required, run '$(Convert-ToRepoPath $HfCli) auth login' and accept the terms on Hugging Face. No token is embedded by this script."
    }
}

function Download-ModelFiles {
    Write-Stage 4 "Preparing model files"
    if (-not (Test-Path -LiteralPath $HfCli)) {
        throw "Hugging Face CLI is missing. Run with -DependencyOnly first."
    }
    $driveName = [System.IO.Path]::GetPathRoot($RepoRoot).TrimEnd("\").TrimEnd(":")
    $drive = Get-PSDrive -Name $driveName -ErrorAction SilentlyContinue
    Test-HuggingFaceAccess -RepositoryId $ModelId
    Test-HuggingFaceAccess -RepositoryId $AudioTokenizerId
    $modelBytes = Get-HuggingFaceRepositoryBytes -RepositoryId $ModelId
    $audioBytes = Get-HuggingFaceRepositoryBytes -RepositoryId $AudioTokenizerId
    $requiredBytes = [int64][Math]::Ceiling((($modelBytes + $audioBytes) * 1.15) + (2 * $GiB))
    Write-Host "Exact model: $ModelId"
    Write-Host "Destination: $(Convert-ToRepoPath $HfModelDir)"
    Write-Host ("Current upstream repository metadata: {0:N2} GB ({1:N2} GiB)." -f ($modelBytes / 1e9), ($modelBytes / 1GB))
    Write-Host "Audio tokenizer: $AudioTokenizerId"
    Write-Host "Destination: $(Convert-ToRepoPath $OnnxDir)"
    Write-Host ("Current upstream repository metadata: {0:N2} GB ({1:N2} GiB)." -f ($audioBytes / 1e9), ($audioBytes / 1GB))
    Assert-FreeDiskSpace -Operation "the requested model downloads" -RequiredBytes $requiredBytes -Basis "current Hugging Face file metadata for both repositories, plus 15% transfer/cache and 2 GiB safety headroom; this intentionally remains conservative when resumable files already exist"
    if ($null -ne $drive) {
        Write-Host ("Available free space: {0:N1} GiB" -f ($drive.Free / 1GB))
    }
    $modelRevision = Get-HuggingFaceRevision -RepositoryId $ModelId
    $audioRevision = Get-HuggingFaceRevision -RepositoryId $AudioTokenizerId
    if (-not $modelRevision -or -not $audioRevision) {
        throw "Could not resolve immutable Hugging Face revisions before download."
    }
    $forceArguments = @()
    if ($Force) {
        $forceArguments += "--force-download"
    }
    Invoke-Checked -FilePath $HfCli -ArgumentList (@("download", $ModelId, "--revision", $modelRevision, "--local-dir", $HfModelDir) + $forceArguments) -FailureMessage "MOSS-TTS-v1.5 download failed"
    Invoke-Checked -FilePath $HfCli -ArgumentList (@("download", $AudioTokenizerId, "--revision", $audioRevision, "--local-dir", $OnnxDir) + $forceArguments) -FailureMessage "ONNX audio tokenizer download failed"
    $downloadRevisions = [ordered]@{
        model_id = $ModelId
        model_revision = $modelRevision
        audio_tokenizer_id = $AudioTokenizerId
        audio_tokenizer_revision = $audioRevision
    }
    $downloadJson = $downloadRevisions | ConvertTo-Json -Depth 4
    [System.IO.File]::WriteAllText($DownloadRevisionPath, $downloadJson + [Environment]::NewLine, [System.Text.UTF8Encoding]::new($false))
}

function Get-BinaryCandidates {
    param([string]$BuildDirectory, [string]$Name)
    return @(
        (Join-Path $BuildDirectory "bin\Release\$Name.exe"),
        (Join-Path $BuildDirectory "bin\$Name.exe")
    )
}

function Test-BuildMatchesCheckout {
    param([string]$BuildDirectory)
    $revisionPath = Join-Path $BuildDirectory "openmoss-source-revision.txt"
    if (-not (Test-Path -LiteralPath $revisionPath)) {
        return $false
    }
    $recorded = (Get-Content -Raw -LiteralPath $revisionPath).Trim()
    $current = Get-GitValue -Checkout $LlamaCheckout -Arguments @("rev-parse", "HEAD")
    return $recorded -eq $current -and $current -match '^[0-9a-f]{40}$'
}

function Build-LlamaTarget {
    param([ValidateSet("cpu", "cuda")][string]$Kind)
    Write-Stage 5 "Building llama-moss-tts and llama-quantize ($Kind)"
    if (-not (Test-Command "cmake")) {
        throw "CMake is required for the $Kind build. Install it manually with 'winget install --id Kitware.CMake -e', open a new PowerShell session, and retry."
    }
    $buildDirectory = Join-Path $LlamaCheckout "build-$Kind"
    $runtimeCandidates = Get-BinaryCandidates -BuildDirectory $buildDirectory -Name "llama-moss-tts"
    $quantizerCandidates = Get-BinaryCandidates -BuildDirectory $buildDirectory -Name "llama-quantize"
    $existingRuntime = $runtimeCandidates | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
    $existingQuantizer = $quantizerCandidates | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
    if ($existingRuntime -and $existingQuantizer -and (Test-BuildMatchesCheckout -BuildDirectory $buildDirectory) -and -not $Force) {
        Write-Host "Reusing runtime: $(Convert-ToRepoPath $existingRuntime)"
        Write-Host "Reusing quantizer: $(Convert-ToRepoPath $existingQuantizer)"
        return
    }
    $configure = @("-S", $LlamaCheckout, "-B", $buildDirectory, "-DCMAKE_BUILD_TYPE=Release")
    if ($Kind -eq "cuda") {
        $configure += @("-DGGML_CUDA=ON", "-DCMAKE_CUDA_ARCHITECTURES=75")
        Write-Host "CUDA build target: compute capability 7.5 (GTX 1660 SUPER). OpenMOSS inherits a documented CUDA 11.7 build path; this script never installs or upgrades CUDA."
    }
    Invoke-Checked -FilePath "cmake" -ArgumentList $configure -FailureMessage "CMake $Kind configuration failed"
    Invoke-Checked -FilePath "cmake" -ArgumentList @("--build", $buildDirectory, "--config", "Release", "--target", "llama-moss-tts", "llama-quantize", "--parallel") -FailureMessage "OpenMOSS runtime/quantizer $Kind build failed"
    $builtRuntime = $runtimeCandidates | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
    $builtQuantizer = $quantizerCandidates | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
    if (-not $builtRuntime -or -not $builtQuantizer) {
        throw "Build completed but llama-moss-tts.exe and llama-quantize.exe were not both found in the documented CMake output locations."
    }
    $sourceRevision = Get-GitValue -Checkout $LlamaCheckout -Arguments @("rev-parse", "HEAD")
    if ($sourceRevision -notmatch '^[0-9a-f]{40}$') {
        throw "Build succeeded, but the exact OpenMOSS source revision could not be recorded."
    }
    [System.IO.File]::WriteAllText((Join-Path $buildDirectory "openmoss-source-revision.txt"), $sourceRevision + [Environment]::NewLine, [System.Text.UTF8Encoding]::new($false))
    Write-Host "Built runtime: $(Convert-ToRepoPath $builtRuntime)"
    Write-Host "Built quantizer: $(Convert-ToRepoPath $builtQuantizer)"
}

function Get-PreparedGgufs {
    $entries = @()
    $items = @(@{ Path = $F16Gguf; Quantization = "F16" })
    if (Test-VerifiedExistingQ4) {
        $items += @{ Path = $Q4Gguf; Quantization = "Q4_K_M" }
    }
    foreach ($item in $items) {
        if (Test-Path -LiteralPath $item.Path) {
            $entries += [ordered]@{
                path = Convert-ToRepoPath $item.Path
                source_model_id = $ModelId
                quantization = $item.Quantization
                sha256 = (Get-FileHash -Algorithm SHA256 -LiteralPath $item.Path).Hash.ToLowerInvariant()
            }
        }
    }
    return $entries
}

function Get-OfficialQuantizer {
    foreach ($build in @("build-cuda", "build-cpu")) {
        $buildDirectory = Join-Path $LlamaCheckout $build
        if (-not (Test-BuildMatchesCheckout -BuildDirectory $buildDirectory)) {
            continue
        }
        $candidate = Get-BinaryCandidates -BuildDirectory $buildDirectory -Name "llama-quantize" |
            Where-Object { Test-Path -LiteralPath $_ } |
            Select-Object -First 1
        if ($candidate) {
            return $candidate
        }
    }
    return $null
}

function Get-OfficialRuntimeBinary {
    foreach ($build in @("build-cuda", "build-cpu")) {
        $buildDirectory = Join-Path $LlamaCheckout $build
        if (-not (Test-BuildMatchesCheckout -BuildDirectory $buildDirectory)) {
            continue
        }
        $candidate = Get-BinaryCandidates -BuildDirectory $buildDirectory -Name "llama-moss-tts" |
            Where-Object { Test-Path -LiteralPath $_ } |
            Select-Object -First 1
        if ($candidate) {
            return $candidate
        }
    }
    return $null
}

function Assert-ValidQ4Gguf {
    param([string]$Path)
    if (-not (Test-Path -LiteralPath $Path) -or (Get-Item -LiteralPath $Path).Length -le 0) {
        throw "Quantized GGUF is missing or empty: $(Convert-ToRepoPath $Path)"
    }
    $dumpScript = Join-Path $LlamaCheckout "gguf-py\gguf\scripts\gguf_dump.py"
    if (-not (Test-Path -LiteralPath $dumpScript)) {
        throw "Official GGUF metadata reader is missing from the verified OpenMOSS checkout."
    }
    $dumpJson = & $Python $dumpScript --json $Path
    if ($LASTEXITCODE -ne 0) {
        throw "Official GGUF metadata validation failed: $($dumpJson | Out-String)"
    }
    try {
        $dump = ($dumpJson | Out-String) | ConvertFrom-Json
    }
    catch {
        throw "Official GGUF metadata reader did not return valid JSON: $($_.Exception.Message)"
    }
    $architecture = $dump.metadata.'general.architecture'.value
    if ($architecture -ne "moss-tts-delay") {
        throw "Unexpected GGUF architecture '$architecture'; expected moss-tts-delay."
    }
    $tensorProperties = @($dump.tensors.PSObject.Properties)
    if ($tensorProperties.Count -ne 463) {
        throw "Unexpected tensor count $($tensorProperties.Count); expected the verified v1.5 layout with 463 tensors."
    }
    $preservedNames = @("token_embd.weight", "output.weight")
    $preservedNames += @(0..31 | ForEach-Object { "token_embd_audio.$_.weight" })
    $preservedNames += @(0..31 | ForEach-Object { "output_audio.$_.weight" })
    foreach ($name in $preservedNames) {
        $property = $dump.tensors.PSObject.Properties[$name]
        if ($null -eq $property) {
            throw "Required first-class MOSS tensor is missing after quantization: $name"
        }
        if ($property.Value.type -ne "F16") {
            throw "Required first-class MOSS tensor '$name' is $($property.Value.type), expected F16."
        }
    }
    $quantizedCount = @($tensorProperties | Where-Object { $_.Value.type -match '^Q[2-8]_' }).Count
    if ($quantizedCount -eq 0) {
        throw "GGUF metadata contains no quantized backbone tensors."
    }
    return [ordered]@{
        architecture = $architecture
        tensor_count = $tensorProperties.Count
        quantized_tensor_count = $quantizedCount
        preserved_f16_tensor_count = $preservedNames.Count
        general_file_type = $dump.metadata.'general.file_type'.value
    }
}

function Test-VerifiedExistingQ4 {
    if (-not (Test-Path -LiteralPath $Q4Gguf) -or -not (Test-Path -LiteralPath $Q4ReceiptPath)) {
        return $false
    }
    try {
        $receipt = Get-Content -Raw -LiteralPath $Q4ReceiptPath | ConvertFrom-Json
        $downloadRevisions = Get-Content -Raw -LiteralPath $DownloadRevisionPath | ConvertFrom-Json
        $runtimeRevision = Get-GitValue -Checkout $LlamaCheckout -Arguments @("rev-parse", "HEAD")
        $actualQ4Hash = (Get-FileHash -Algorithm SHA256 -LiteralPath $Q4Gguf).Hash.ToLowerInvariant()
        if (
            $receipt.status -ne "verified" -or
            $receipt.source_model_id -ne $ModelId -or
            $receipt.source_model_revision -ne $downloadRevisions.model_revision -or
            $receipt.quantized_sha256 -ne $actualQ4Hash -or
            $receipt.source_f16_sha256 -notmatch '^[0-9a-f]{64}$' -or
            $receipt.quantization -ne "Q4_K_M" -or
            $receipt.quantizer_revision -ne $runtimeRevision -or
            $receipt.runtime_revision -ne $runtimeRevision -or
            $receipt.quantized_bytes -ne (Get-Item -LiteralPath $Q4Gguf).Length -or
            -not $receipt.created_at -or
            $receipt.special_tensor_rules.'token_embd.weight' -notmatch '^F16' -or
            $receipt.special_tensor_rules.'output.weight' -notmatch '^F16' -or
            $receipt.special_tensor_rules.'token_embd_audio.[0-31].weight' -notmatch '^F16' -or
            $receipt.special_tensor_rules.'output_audio.[0-31].weight' -notmatch '^F16'
        ) {
            return $false
        }
        if (Test-Path -LiteralPath $F16Gguf) {
            $actualF16Hash = (Get-FileHash -Algorithm SHA256 -LiteralPath $F16Gguf).Hash.ToLowerInvariant()
            if ($receipt.source_f16_sha256 -ne $actualF16Hash) {
                return $false
            }
        }
        $null = Assert-ValidQ4Gguf -Path $Q4Gguf
        return $true
    }
    catch {
        return $false
    }
}

function Test-VerifiedExistingF16 {
    if (-not (Test-Path -LiteralPath $ProvenancePath) -or -not (Test-Path -LiteralPath $F16Gguf)) {
        return $false
    }
    try {
        $existing = Get-Content -Raw -LiteralPath $ProvenancePath | ConvertFrom-Json
        $downloadRevisions = Get-Content -Raw -LiteralPath $DownloadRevisionPath | ConvertFrom-Json
        if (
            $existing.conversion_status -ne "verified_official_v15" -or
            $existing.model_id -ne $ModelId -or
            $existing.model_revision -ne $downloadRevisions.model_revision -or
            $existing.audio_tokenizer_revision -ne $downloadRevisions.audio_tokenizer_revision
        ) {
            return $false
        }
        $relativePath = Convert-ToRepoPath $F16Gguf
        $entry = @($existing.prepared_ggufs) | Where-Object {
            $_.path -eq $relativePath -and $_.source_model_id -eq $ModelId -and $_.quantization -eq "F16"
        } | Select-Object -First 1
        if (-not $entry) {
            return $false
        }
        $actualHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $F16Gguf).Hash.ToLowerInvariant()
        return $entry.sha256 -eq $actualHash
    }
    catch {
        return $false
    }
}

function Write-Provenance {
    param(
        [string]$ConversionStatus,
        [AllowNull()][string]$BlockReason
    )
    $runtimeRevision = $null
    if (Test-Path -LiteralPath (Join-Path $LlamaCheckout ".git")) {
        $runtimeRevision = Get-GitValue -Checkout $LlamaCheckout -Arguments @("rev-parse", "HEAD")
    }
    $modelRevision = Get-HuggingFaceRevision -RepositoryId $ModelId
    $audioRevision = Get-HuggingFaceRevision -RepositoryId $AudioTokenizerId
    if (Test-Path -LiteralPath $DownloadRevisionPath) {
        $downloadRevisions = Get-Content -Raw -LiteralPath $DownloadRevisionPath | ConvertFrom-Json
        if ($downloadRevisions.model_id -eq $ModelId -and $downloadRevisions.audio_tokenizer_id -eq $AudioTokenizerId) {
            $modelRevision = $downloadRevisions.model_revision
            $audioRevision = $downloadRevisions.audio_tokenizer_revision
        }
    }
    $payload = [ordered]@{
        model_id = $ModelId
        model_revision = $modelRevision
        audio_tokenizer_id = $AudioTokenizerId
        audio_tokenizer_revision = $audioRevision
        runtime_repository = "OpenMOSS/llama.cpp"
        runtime_branch = $RuntimeBranch
        runtime_revision = $runtimeRevision
        conversion_status = $ConversionStatus
        conversion_block_reason = $BlockReason
        prepared_ggufs = @(Get-PreparedGgufs)
    }
    $json = $payload | ConvertTo-Json -Depth 8
    [System.IO.File]::WriteAllText($ProvenancePath, $json + [Environment]::NewLine, [System.Text.UTF8Encoding]::new($false))
    Write-Host "Provenance: $(Convert-ToRepoPath $ProvenancePath)"
}

function Test-OfficialV15FirstClassSupport {
    $guide = Join-Path $LlamaCheckout "docs\moss-tts-firstclass-e2e.md"
    $converter = Join-Path $LlamaCheckout "convert_hf_to_gguf.py"
    $modelConfig = Join-Path $HfModelDir "config.json"
    $modelIndex = Join-Path $HfModelDir "model.safetensors.index.json"
    if (
        -not (Test-Path -LiteralPath $guide) -or
        -not (Test-Path -LiteralPath $converter) -or
        -not (Test-Path -LiteralPath $modelConfig) -or
        -not (Test-Path -LiteralPath $modelIndex)
    ) {
        return $false
    }
    $guideText = Get-Content -Raw -LiteralPath $guide
    $converterText = Get-Content -Raw -LiteralPath $converter
    $config = Get-Content -Raw -LiteralPath $modelConfig | ConvertFrom-Json
    $index = Get-Content -Raw -LiteralPath $modelIndex | ConvertFrom-Json
    $tensorNames = @($index.weight_map.PSObject.Properties.Name)
    $expectedEmbeddings = @(0..31 | ForEach-Object { "emb_ext.$_.weight" })
    $expectedHeads = @(0..32 | ForEach-Object { "lm_heads.$_.weight" })
    $layoutMatches = (@($expectedEmbeddings | Where-Object { $_ -notin $tensorNames }).Count -eq 0) -and
        (@($expectedHeads | Where-Object { $_ -notin $tensorNames }).Count -eq 0)
    return (
        $guideText.Contains("convert_hf_to_gguf.py") -and
        $converterText.Contains('register("MossTTSDelayModel"') -and
        (@($config.architectures) -contains "MossTTSDelayModel") -and
        $config.model_type -eq "moss_tts_delay" -and
        $config.language_config.model_type -eq "qwen3" -and
        $config.language_config.num_hidden_layers -eq 36 -and
        $config.n_vq -eq 32 -and
        $config.audio_vocab_size -eq 1024 -and
        $tensorNames.Count -eq 463 -and
        $index.metadata.total_size -eq 16979683328 -and
        $layoutMatches
    )
}

function Prepare-OfficialGguf {
    Write-Host "Preparing the exact v1.5 first-class GGUF conversion intermediate"
    foreach ($required in @(
        (Join-Path $HfModelDir "config.json"),
        (Join-Path $HfModelDir "model.safetensors.index.json"),
        $DownloadRevisionPath
    )) {
        if (-not (Test-Path -LiteralPath $required)) {
            throw "Exact v1.5 checkpoint is incomplete: $(Convert-ToRepoPath $required). Run -DownloadModels explicitly."
        }
    }
    $downloadRevisions = Get-Content -Raw -LiteralPath $DownloadRevisionPath | ConvertFrom-Json
    if (
        $downloadRevisions.model_id -ne $ModelId -or
        $downloadRevisions.audio_tokenizer_id -ne $AudioTokenizerId -or
        -not $downloadRevisions.model_revision -or
        -not $downloadRevisions.audio_tokenizer_revision
    ) {
        throw "Download revision metadata does not identify the exact required model and ONNX tokenizer. Run -DownloadModels explicitly."
    }
    if (-not (Test-OfficialV15FirstClassSupport)) {
        $reason = "The downloaded $ModelId metadata does not match the MossTTSDelay layout supported by the current official first-class converter, so conversion was not run."
        Write-Provenance -ConversionStatus "blocked_unverified_official_v15" -BlockReason $reason
        throw "$reason Force cannot bypass this compatibility gate."
    }

    $quantizer = $null
    $runtimeBinary = $null
    if ($Quantize -eq "Q4_K_M") {
        $quantizer = Get-OfficialQuantizer
        $runtimeBinary = Get-OfficialRuntimeBinary
        if (-not $quantizer -or -not $runtimeBinary) {
            throw "A revision-matched llama-quantize.exe and llama-moss-tts.exe were not found in the verified OpenMOSS checkout. Run this setup with -BuildCuda (or -BuildCpu) first."
        }
    }

    if ($Quantize -eq "Q4_K_M" -and (Test-Path -LiteralPath $Q4Gguf)) {
        if (-not (Test-VerifiedExistingQ4)) {
            throw "An existing Q4_K_M GGUF does not pass its receipt, revision, hash, and tensor-layout checks. It will not be overwritten, even with -Force. Move it aside manually after reviewing it."
        }
        Write-Host "Reusing verified Q4_K_M GGUF: $(Convert-ToRepoPath $Q4Gguf)"
        if ($CleanupIntermediate -and (Test-Path -LiteralPath $F16Gguf)) {
            Remove-Item -LiteralPath $F16Gguf -Force
            Write-Host "Removed only the verified intermediate: $(Convert-ToRepoPath $F16Gguf)"
        }
        Write-Provenance -ConversionStatus "verified_official_v15" -BlockReason $null
        return
    }

    if (Test-Path -LiteralPath $F16Gguf) {
        if (-not (Test-VerifiedExistingF16)) {
            throw "An existing F16 GGUF lacks matching verified v1.5 provenance and will not be overwritten, even with -Force. Move it aside manually after reviewing it."
        }
        Write-Host "Reusing provenance-verified F16 conversion intermediate: $(Convert-ToRepoPath $F16Gguf)"
    }
    else {
        $modelIndex = Get-Content -Raw -LiteralPath (Join-Path $HfModelDir "model.safetensors.index.json") | ConvertFrom-Json
        $expectedF16Bytes = [int64]$modelIndex.metadata.total_size
        Assert-FreeDiskSpace -Operation "first-class F16 conversion" -RequiredBytes ($expectedF16Bytes + $GiB) -Basis "the downloaded v1.5 safetensors index total_size plus 1 GiB conversion/output headroom"
        $requirements = Join-Path $LlamaCheckout "requirements.txt"
        Invoke-Checked -FilePath $Python -ArgumentList @("-m", "pip", "install", "-r", $requirements) -FailureMessage "Official converter dependencies failed to install"
        $partialF16 = Join-Path $GgufDir ("moss_tts_v15_firstclass_f16.{0}.partial.gguf" -f [guid]::NewGuid().ToString("N"))
        try {
            Invoke-Checked -FilePath $Python -ArgumentList @(
                (Join-Path $LlamaCheckout "convert_hf_to_gguf.py"),
                $HfModelDir,
                "--outfile", $partialF16,
                "--outtype", "f16"
            ) -FailureMessage "Official first-class v1.5 F16 conversion failed"
            Move-Item -LiteralPath $partialF16 -Destination $F16Gguf
        }
        catch {
            if (Test-Path -LiteralPath $partialF16) {
                Remove-Item -LiteralPath $partialF16 -Force
            }
            throw
        }
    }

    Write-Provenance -ConversionStatus "verified_official_v15" -BlockReason $null

    if ($Quantize -eq "Q4_K_M") {
        if (Test-Path -LiteralPath $Q4Gguf) {
            throw "An unverified Q4_K_M output already exists and will not be overwritten. Move it aside manually after reviewing it."
        }

        $config = Get-Content -Raw -LiteralPath (Join-Path $HfModelDir "config.json") | ConvertFrom-Json
        $hiddenSize = [int64]$config.language_config.hidden_size
        $textRows = [int64]$config.language_config.vocab_size
        $audioRows = [int64]$config.audio_vocab_size + 1
        $channelCount = [int64]$config.n_vq
        $preservedF16Bytes = [int64](2 * $textRows * $hiddenSize * 2) + [int64](2 * $channelCount * $audioRows * $hiddenSize * 2)
        $f16Bytes = [int64](Get-Item -LiteralPath $F16Gguf).Length
        $estimatedQ4Bytes = [int64][Math]::Ceiling($preservedF16Bytes + (($f16Bytes - $preservedF16Bytes) * (4.91 / 16.0)))
        $requiredQ4Bytes = [int64][Math]::Ceiling(($estimatedQ4Bytes * 1.15) + $GiB)
        Write-Host ("Estimated Q4_K_M output from current F16 and precision rules: {0:N2} GB ({1:N2} GiB)." -f ($estimatedQ4Bytes / 1e9), ($estimatedQ4Bytes / 1GB))
        Write-Host ("Deliberately preserved embedding/output tables: {0:N2} GB ({1:N2} GiB) F16." -f ($preservedF16Bytes / 1e9), ($preservedF16Bytes / 1GB))
        Assert-FreeDiskSpace -Operation "Q4_K_M quantization" -RequiredBytes $requiredQ4Bytes -Basis "current F16 byte size, Q4_K_M's 4.91-bit class estimate for the backbone, preserved F16 tables, 15% variance, and 1 GiB headroom"

        $specialArguments = @(
            "--token-embedding-type", "f16",
            "--output-tensor-type", "f16",
            "--tensor-type", '^token_embd_audio\.[0-9]+\.weight$=f16',
            "--tensor-type", '^output_audio\.[0-9]+\.weight$=f16'
        )
        Write-Host "Validating the exact quantization plan with llama-quantize --dry-run."
        Invoke-Checked -FilePath $quantizer -ArgumentList (@("--dry-run") + $specialArguments + @($F16Gguf, $Q4Gguf, "Q4_K_M")) -FailureMessage "Official OpenMOSS quantizer rejected the v1.5 Q4_K_M plan"

        $sourceF16Hash = (Get-FileHash -Algorithm SHA256 -LiteralPath $F16Gguf).Hash.ToLowerInvariant()
        $runtimeRevision = Get-GitValue -Checkout $LlamaCheckout -Arguments @("rev-parse", "HEAD")
        $partialQ4 = Join-Path $GgufDir ("moss_tts_v15_firstclass_q4_k_m.{0}.partial.gguf" -f [guid]::NewGuid().ToString("N"))
        try {
            Invoke-Checked -FilePath $quantizer -ArgumentList ($specialArguments + @($F16Gguf, $partialQ4, "Q4_K_M")) -FailureMessage "Official v1.5 Q4_K_M quantization failed"
            $validation = Assert-ValidQ4Gguf -Path $partialQ4
            Invoke-Checked -FilePath $runtimeBinary -ArgumentList @("-m", $partialQ4, "--n-gpu-layers", "0", "--print-delay-config") -FailureMessage "llama-moss-tts could not load the quantized v1.5 model for metadata inspection"
            Move-Item -LiteralPath $partialQ4 -Destination $Q4Gguf
            $quantizedHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $Q4Gguf).Hash.ToLowerInvariant()
            $q4Receipt = [ordered]@{
                status = "verified"
                source_model_id = $ModelId
                source_model_revision = $downloadRevisions.model_revision
                source_f16_sha256 = $sourceF16Hash
                quantized_sha256 = $quantizedHash
                quantization = "Q4_K_M"
                quantizer_revision = $runtimeRevision
                runtime_revision = $runtimeRevision
                special_tensor_rules = [ordered]@{
                    "token_embd.weight" = "F16 via --token-embedding-type f16"
                    "output.weight" = "F16 via --output-tensor-type f16"
                    "token_embd_audio.[0-31].weight" = "F16 via --tensor-type regex"
                    "output_audio.[0-31].weight" = "F16 via --tensor-type regex"
                    rationale = "Matches the official MOSS Q4 backend boundary: quantized Qwen3 backbone with float16 embedding tables and LM heads."
                }
                validation = $validation
                model_load_validation = "llama-moss-tts --n-gpu-layers 0 --print-delay-config"
                quantized_bytes = [int64](Get-Item -LiteralPath $Q4Gguf).Length
                created_at = (Get-Date).ToUniversalTime().ToString("o")
            }
            $q4Json = $q4Receipt | ConvertTo-Json -Depth 8
            [System.IO.File]::WriteAllText($Q4ReceiptPath, $q4Json + [Environment]::NewLine, [System.Text.UTF8Encoding]::new($false))
        }
        catch {
            if (Test-Path -LiteralPath $partialQ4) {
                Remove-Item -LiteralPath $partialQ4 -Force
            }
            throw
        }
        Write-Provenance -ConversionStatus "verified_official_v15" -BlockReason $null
        if ($CleanupIntermediate) {
            if (-not (Test-VerifiedExistingQ4)) {
                throw "Q4 verification changed before cleanup; the F16 intermediate was retained."
            }
            Remove-Item -LiteralPath $F16Gguf -Force
            Write-Host "Removed only the verified intermediate: $(Convert-ToRepoPath $F16Gguf)"
            Write-Provenance -ConversionStatus "verified_official_v15" -BlockReason $null
        }
    }
}

function Verify-Runtime {
    Write-Stage 6 "Verifying runtime"
    if ($Quantize -eq "Q4_K_M" -and -not (Test-VerifiedExistingQ4)) {
        throw "A verified exact-v1.5 Q4_K_M GGUF was not produced."
    }
    if ($PrepareGGUF -and $Quantize -eq "None" -and -not (Test-Path -LiteralPath $F16Gguf)) {
        throw "The requested first-class F16 conversion intermediate was not produced."
    }
    foreach ($kind in @("cpu", "cuda")) {
        if (($kind -eq "cpu" -and $BuildCpu) -or ($kind -eq "cuda" -and $BuildCuda)) {
            $buildDirectory = Join-Path $LlamaCheckout "build-$kind"
            $binary = Get-BinaryCandidates -BuildDirectory $buildDirectory -Name "llama-moss-tts" | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
            $quantizer = Get-BinaryCandidates -BuildDirectory $buildDirectory -Name "llama-quantize" | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
            if (-not $binary -or -not $quantizer -or -not (Test-BuildMatchesCheckout -BuildDirectory $buildDirectory)) {
                throw "Expected $kind runtime and quantizer binaries were not both found."
            }
            Write-Host "Verified $kind binary: $(Convert-ToRepoPath $binary)"
            Write-Host "Verified $kind quantizer: $(Convert-ToRepoPath $quantizer)"
        }
    }
    if (($BuildCpu -or $BuildCuda) -and -not ($PrepareGGUF -or ($Quantize -ne "None"))) {
        Write-Provenance -ConversionStatus "not_prepared" -BlockReason "Runtime built, but the exact v1.5 Q4_K_M artifact has not been prepared yet."
    }
    Write-Host "No model inference was run."
}

if ($CleanupIntermediate -and $Quantize -ne "Q4_K_M") {
    throw "-CleanupIntermediate is valid only with -Quantize Q4_K_M, after Q4 verification."
}

$hasAction = $DependencyOnly -or $DownloadModels -or $BuildCpu -or $BuildCuda -or $PrepareGGUF -or ($Quantize -ne "None")
Show-Preflight
if (-not $hasAction) {
    Write-Usage
    exit 0
}

Ensure-Directories
if ($DependencyOnly -or $DownloadModels -or $PrepareGGUF -or ($Quantize -ne "None")) {
    Ensure-PythonEnvironment
}
else {
    Write-Stage 2 "Python environment not requested"
}

if ($BuildCpu -or $BuildCuda -or $PrepareGGUF -or ($Quantize -ne "None")) {
    Ensure-Sources
}
else {
    Write-Stage 3 "OpenMOSS source preparation not requested"
}

if ($DownloadModels) {
    Download-ModelFiles
}
elseif ($PrepareGGUF -or ($Quantize -ne "None")) {
    Write-Stage 4 "Preparing model files"
}
else {
    Write-Stage 4 "Model download/preparation not requested"
}

if ($BuildCpu) {
    Build-LlamaTarget -Kind "cpu"
}
if ($BuildCuda) {
    Build-LlamaTarget -Kind "cuda"
}
if (-not $BuildCpu -and -not $BuildCuda) {
    Write-Stage 5 "Runtime build not requested"
}

if ($PrepareGGUF -or ($Quantize -ne "None")) {
    Prepare-OfficialGguf
}
elseif ($DownloadModels) {
    Write-Provenance -ConversionStatus "not_prepared" -BlockReason "Exact v1.5 and ONNX files were downloaded; run -PrepareGGUF -Quantize Q4_K_M explicitly to create the primary Q4 runtime artifact."
}

Verify-Runtime
