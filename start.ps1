param(
    [string]$ListenHost,
    [int]$Port,
    [string]$Python,
    [string]$EnvFile,
    [switch]$SkipComfyCheck,
    [switch]$CheckOnly
)

$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$venvPython = Join-Path $projectRoot ".venv\Scripts\python.exe"
$defaultEnvFile = Join-Path $projectRoot ".env"

function Write-Info([string]$Message) {
    Write-Host "[comfyui2api] $Message"
}

function Test-Command([string]$Name) {
    return $null -ne (Get-Command $Name -ErrorAction SilentlyContinue)
}

function Set-EnvDefault([string]$Name, [string]$Value) {
    $current = [Environment]::GetEnvironmentVariable($Name, "Process")
    if ([string]::IsNullOrWhiteSpace($current)) {
        [Environment]::SetEnvironmentVariable($Name, $Value, "Process")
    }
}

function Resolve-BootstrapPython() {
    if (Test-Command "py") {
        return @("py", "-3")
    }
    if (Test-Command "python") {
        return @("python")
    }
    throw "Python was not found. Install Python 3.11+ first."
}

function Ensure-Venv() {
    if (Test-Path $venvPython) {
        return
    }

    $bootstrap = Resolve-BootstrapPython
    $bootstrapCmd = $bootstrap[0]
    $bootstrapArgs = @()
    if ($bootstrap.Length -gt 1) {
        $bootstrapArgs = $bootstrap[1..($bootstrap.Length - 1)]
    }
    Write-Info "Creating .venv ..."
    & $bootstrapCmd @bootstrapArgs -m venv (Join-Path $projectRoot ".venv")
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path $venvPython)) {
        throw "Failed to create .venv."
    }
}

function Resolve-Python() {
    if ($Python) {
        if (-not (Test-Path $Python)) {
            throw "Python executable not found: $Python"
        }
        return (Resolve-Path $Python).Path
    }

    Ensure-Venv
    return $venvPython
}

function Ensure-PackageInstalled([string]$PythonExe) {
    & $PythonExe -m pip show comfyui2api *> $null
    if ($LASTEXITCODE -eq 0) {
        return
    }

    Write-Info "Installing project into the virtual environment ..."
    & $PythonExe -m pip install -e $projectRoot
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to install comfyui2api into the virtual environment."
    }
}

function Test-ComfyReachable([string]$BaseUrl) {
    $healthUrl = $BaseUrl.TrimEnd("/") + "/system_stats"
    try {
        $null = Invoke-WebRequest -Uri $healthUrl -Method Get -TimeoutSec 5
        Write-Info "ComfyUI reachable at $BaseUrl"
    } catch {
        Write-Warning "ComfyUI is not reachable at $BaseUrl. The API will still start, but requests may fail until ComfyUI is available."
    }
}

Set-Location $projectRoot

$pythonExe = Resolve-Python
Ensure-PackageInstalled -PythonExe $pythonExe

if (-not $EnvFile -and (Test-Path $defaultEnvFile)) {
    $EnvFile = $defaultEnvFile
}
if ($EnvFile) {
    if (-not (Test-Path $EnvFile)) {
        throw "ENV file not found: $EnvFile"
    }
    [Environment]::SetEnvironmentVariable("ENV_FILE", (Resolve-Path $EnvFile).Path, "Process")
}

if ($PSBoundParameters.ContainsKey("ListenHost")) {
    [Environment]::SetEnvironmentVariable("API_LISTEN", $ListenHost, "Process")
}
if ($PSBoundParameters.ContainsKey("Port")) {
    [Environment]::SetEnvironmentVariable("API_PORT", "$Port", "Process")
}

Set-EnvDefault "COMFYUI_BASE_URL" "http://127.0.0.1:8188"
Set-EnvDefault "IMAGE_UPLOAD_MODE" "comfy"
Set-EnvDefault "API_LISTEN" "0.0.0.0"
Set-EnvDefault "API_PORT" "8000"

$resolvedComfyBase = [Environment]::GetEnvironmentVariable("COMFYUI_BASE_URL", "Process")
$resolvedUploadMode = [Environment]::GetEnvironmentVariable("IMAGE_UPLOAD_MODE", "Process")
$resolvedHost = [Environment]::GetEnvironmentVariable("API_LISTEN", "Process")
$resolvedPort = [Environment]::GetEnvironmentVariable("API_PORT", "Process")

Write-Info "Project root: $projectRoot"
Write-Info "Python: $pythonExe"
Write-Info "ENV_FILE: $([Environment]::GetEnvironmentVariable('ENV_FILE', 'Process'))"
Write-Info "COMFYUI_BASE_URL: $resolvedComfyBase"
Write-Info "IMAGE_UPLOAD_MODE: $resolvedUploadMode"
Write-Info "Listening on: http://$resolvedHost`:$resolvedPort"

if (-not $SkipComfyCheck) {
    Test-ComfyReachable -BaseUrl $resolvedComfyBase
}

if ($CheckOnly) {
    Write-Info "Check only mode finished."
    exit 0
}

Write-Info "Starting comfyui2api ..."
& $pythonExe -m comfyui2api
exit $LASTEXITCODE
