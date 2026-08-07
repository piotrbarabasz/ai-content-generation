[CmdletBinding()]
param(
    [switch]$DependencyOnly,
    [switch]$DownloadModels,
    [switch]$BuildCpu,
    [switch]$BuildCuda,
    [switch]$PrepareGGUF,
    [ValidateSet("None", "Q4_K_M")]
    [string]$Quantize = "None",
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
  -BuildCpu         Clone/verify sources and build llama-moss-tts for CPU.
  -BuildCuda        Clone/verify sources and build llama-moss-tts with CUDA.
  -PrepareGGUF      Validate the exact MossTTSDelay layout, then invoke the
                    official first-class converter for $ModelId.
  -Quantize Q4_K_M  Quantize only when the same official guide explicitly
                    documents v1.5 first-class quantization safety.
  -Force            Reinstall/redownload/reconfigure; never deletes or resets.

Examples:
  .\experiments\tts_local\setup_moss_tts_v15.ps1
  .\experiments\tts_local\setup_moss_tts_v15.ps1 -DependencyOnly
  .\experiments\tts_local\setup_moss_tts_v15.ps1 -DependencyOnly -DownloadModels
  .\experiments\tts_local\setup_moss_tts_v15.ps1 -BuildCpu
  .\experiments\tts_local\setup_moss_tts_v15.ps1 -BuildCuda
  .\experiments\tts_local\setup_moss_tts_v15.ps1 -PrepareGGUF
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
            Write-Warning "$tool was not found."
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
    Write-Host "Exact model: $ModelId"
    Write-Host "Destination: $(Convert-ToRepoPath $HfModelDir)"
    Write-Host "Official weight shards observed upstream: approximately 17.0 GB (15.8 GiB)."
    Write-Host "Audio tokenizer: $AudioTokenizerId"
    Write-Host "Destination: $(Convert-ToRepoPath $OnnxDir)"
    Write-Host "Official ONNX files observed upstream: approximately 14.2 GB (13.3 GiB)."
    Write-Host "Allow at least 50 GiB free for both downloads plus a future F16 GGUF; additional cache headroom is prudent."
    if ($null -ne $drive) {
        Write-Host ("Available free space: {0:N1} GiB" -f ($drive.Free / 1GB))
    }
    Test-HuggingFaceAccess -RepositoryId $ModelId
    Test-HuggingFaceAccess -RepositoryId $AudioTokenizerId
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

function Build-LlamaTarget {
    param([ValidateSet("cpu", "cuda")][string]$Kind)
    Write-Stage 5 "Building llama-moss-tts ($Kind)"
    if (-not (Test-Command "cmake")) {
        throw "CMake is required for the $Kind build."
    }
    $buildDirectory = Join-Path $LlamaCheckout "build-$Kind"
    $candidates = Get-BinaryCandidates -BuildDirectory $buildDirectory -Name "llama-moss-tts"
    $existing = $candidates | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
    if ($existing -and -not $Force) {
        Write-Host "Reusing existing binary: $(Convert-ToRepoPath $existing)"
        return
    }
    $configure = @("-S", $LlamaCheckout, "-B", $buildDirectory, "-DCMAKE_BUILD_TYPE=Release")
    if ($Kind -eq "cuda") {
        $configure += "-DGGML_CUDA=ON"
    }
    Invoke-Checked -FilePath "cmake" -ArgumentList $configure -FailureMessage "CMake $Kind configuration failed"
    Invoke-Checked -FilePath "cmake" -ArgumentList @("--build", $buildDirectory, "--config", "Release", "--target", "llama-moss-tts", "--parallel") -FailureMessage "llama-moss-tts $Kind build failed"
    $built = $candidates | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
    if (-not $built) {
        throw "Build completed but llama-moss-tts.exe was not found in the documented CMake output locations."
    }
    Write-Host "Built: $(Convert-ToRepoPath $built)"
}

function Get-PreparedGgufs {
    $entries = @()
    $items = @(@{ Path = $F16Gguf; Quantization = "F16" })
    if ((Test-Path -LiteralPath $Q4Gguf) -and (Test-Path -LiteralPath $Q4ReceiptPath)) {
        $receipt = Get-Content -Raw -LiteralPath $Q4ReceiptPath | ConvertFrom-Json
        $actualQ4Hash = (Get-FileHash -Algorithm SHA256 -LiteralPath $Q4Gguf).Hash.ToLowerInvariant()
        if ($receipt.model_id -eq $ModelId -and $receipt.sha256 -eq $actualQ4Hash) {
            $items += @{ Path = $Q4Gguf; Quantization = "Q4_K_M" }
        }
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
    Write-Host "Preparing canonical first-class F16 GGUF"
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

    if (Test-Path -LiteralPath $F16Gguf) {
        if (-not (Test-VerifiedExistingF16)) {
            throw "An existing F16 GGUF lacks matching verified v1.5 provenance and will not be overwritten, even with -Force. Move it aside manually after reviewing it."
        }
        Write-Host "Reusing provenance-verified canonical F16 GGUF: $(Convert-ToRepoPath $F16Gguf)"
    }
    else {
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
        $guideText = Get-Content -Raw -LiteralPath (Join-Path $LlamaCheckout "docs\moss-tts-firstclass-e2e.md")
        if (-not ($guideText.Contains($ModelId) -and $guideText.Contains("Q4_K_M") -and $guideText.Contains("llama-quantize"))) {
            throw "Official first-class documentation does not explicitly verify Q4_K_M for $ModelId. F16 remains canonical; quantization was not run."
        }
        $quantizer = $null
        foreach ($build in @("build-cuda", "build-cpu")) {
            $candidate = Get-BinaryCandidates -BuildDirectory (Join-Path $LlamaCheckout $build) -Name "llama-quantize" | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
            if ($candidate) {
                $quantizer = $candidate
                break
            }
        }
        if (-not $quantizer) {
            throw "llama-quantize.exe was not found. Build the official runtime first."
        }
        if (Test-Path -LiteralPath $Q4Gguf) {
            if (-not (Test-Path -LiteralPath $Q4ReceiptPath)) {
                throw "An existing Q4_K_M file lacks an official-support receipt and will not be overwritten. Move it aside manually after reviewing it."
            }
            $existingQ4Receipt = Get-Content -Raw -LiteralPath $Q4ReceiptPath | ConvertFrom-Json
            $existingQ4Hash = (Get-FileHash -Algorithm SHA256 -LiteralPath $Q4Gguf).Hash.ToLowerInvariant()
            if ($existingQ4Receipt.model_id -ne $ModelId -or $existingQ4Receipt.sha256 -ne $existingQ4Hash) {
                throw "The existing Q4_K_M file does not match its receipt and will not be used."
            }
        }
        else {
            Invoke-Checked -FilePath $quantizer -ArgumentList @($F16Gguf, $Q4Gguf, "Q4_K_M") -FailureMessage "Official v1.5 Q4_K_M quantization failed"
            $q4Receipt = [ordered]@{
                model_id = $ModelId
                source_f16_sha256 = (Get-FileHash -Algorithm SHA256 -LiteralPath $F16Gguf).Hash.ToLowerInvariant()
                sha256 = (Get-FileHash -Algorithm SHA256 -LiteralPath $Q4Gguf).Hash.ToLowerInvariant()
            }
            $q4Json = $q4Receipt | ConvertTo-Json -Depth 4
            [System.IO.File]::WriteAllText($Q4ReceiptPath, $q4Json + [Environment]::NewLine, [System.Text.UTF8Encoding]::new($false))
        }
        Write-Provenance -ConversionStatus "verified_official_v15" -BlockReason $null
    }
}

function Verify-Runtime {
    Write-Stage 6 "Verifying runtime"
    if (($PrepareGGUF -or ($Quantize -ne "None")) -and -not (Test-Path -LiteralPath $F16Gguf)) {
        throw "Canonical first-class F16 GGUF was not produced."
    }
    foreach ($kind in @("cpu", "cuda")) {
        if (($kind -eq "cpu" -and $BuildCpu) -or ($kind -eq "cuda" -and $BuildCuda)) {
            $buildDirectory = Join-Path $LlamaCheckout "build-$kind"
            $binary = Get-BinaryCandidates -BuildDirectory $buildDirectory -Name "llama-moss-tts" | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
            if (-not $binary) {
                throw "Expected $kind runtime binary was not found."
            }
            Write-Host "Verified $kind binary: $(Convert-ToRepoPath $binary)"
        }
    }
    if (($BuildCpu -or $BuildCuda) -and -not ($PrepareGGUF -or ($Quantize -ne "None"))) {
        Write-Provenance -ConversionStatus "not_prepared" -BlockReason "Runtime built, but the exact v1.5 F16 GGUF has not been prepared yet."
    }
    Write-Host "No model inference was run."
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

if ($PrepareGGUF -or ($Quantize -ne "None")) {
    Prepare-OfficialGguf
}
elseif ($DownloadModels) {
    Write-Provenance -ConversionStatus "not_prepared" -BlockReason "Exact v1.5 and ONNX files were downloaded; run -PrepareGGUF explicitly to create the canonical F16 GGUF."
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

Verify-Runtime
