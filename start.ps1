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

function Import-EnvFile([string]$Path) {
    foreach ($line in Get-Content $Path) {
        $trimmed = $line.Trim()
        if ([string]::IsNullOrWhiteSpace($trimmed)) {
            continue
        }
        if ($trimmed.StartsWith("#")) {
            continue
        }
        if ($trimmed -notmatch "^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)\s*$") {
            continue
        }

        $name = $Matches[1]
        $value = $Matches[2].Trim()

        if ($value.Length -ge 2) {
            if (($value.StartsWith('"') -and $value.EndsWith('"')) -or ($value.StartsWith("'") -and $value.EndsWith("'"))) {
                $value = $value.Substring(1, $value.Length - 2)
            }
        }

        [Environment]::SetEnvironmentVariable($name, $value, "Process")
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

function Get-TcpExcludedPortRanges() {
    $lines = netsh interface ipv4 show excludedportrange protocol=tcp 2>$null
    $ranges = @()
    foreach ($line in $lines) {
        if ($line -match "^\s*(\d+)\s+(\d+)\s*") {
            $ranges += [pscustomobject]@{
                Start = [int]$Matches[1]
                End = [int]$Matches[2]
            }
        }
    }
    return $ranges
}

function Find-TcpExcludedPortRange([int]$Port) {
    foreach ($range in Get-TcpExcludedPortRanges) {
        if ($Port -ge $range.Start -and $Port -le $range.End) {
            return $range
        }
    }
    return $null
}

function Resolve-ListenIPAddress([string]$ListenHostValue) {
    $raw = ($ListenHostValue | ForEach-Object { $_.Trim() })
    if ([string]::IsNullOrWhiteSpace($raw) -or $raw -eq "0.0.0.0") {
        return [System.Net.IPAddress]::Any
    }
    if ($raw -eq "::" -or $raw -eq "[::]") {
        return [System.Net.IPAddress]::IPv6Any
    }
    $ip = $null
    if ([System.Net.IPAddress]::TryParse($raw, [ref]$ip)) {
        return $ip
    }
    try {
        $resolved = [System.Net.Dns]::GetHostAddresses($raw) | Select-Object -First 1
        if ($resolved) {
            return $resolved
        }
    } catch {
    }
    return [System.Net.IPAddress]::Any
}

function Test-TcpBindable([string]$ListenHostValue, [int]$Port) {
    $listener = $null
    try {
        $listener = [System.Net.Sockets.TcpListener]::new((Resolve-ListenIPAddress $ListenHostValue), $Port)
        $listener.Start()
        return $true
    } catch {
        return $false
    } finally {
        if ($listener) {
            try {
                $listener.Stop()
            } catch {
            }
        }
    }
}

function Resolve-ListenPort([string]$ListenHostValue, [int]$RequestedPort) {
    $port = [Math]::Max(1, [Math]::Min(65535, $RequestedPort))
    $excluded = Find-TcpExcludedPortRange -Port $port
    $bindable = Test-TcpBindable -ListenHostValue $ListenHostValue -Port $port
    if (-not $excluded -and $bindable) {
        return $port
    }

    if ($excluded) {
        Write-Warning "Port $port is in a Windows excluded TCP range ($($excluded.Start)-$($excluded.End))."
    } else {
        Write-Warning "Port $port is not bindable on $ListenHostValue. It may already be in use or blocked."
    }

    for ($candidate = $port + 1; $candidate -le 65535; $candidate++) {
        if ((Find-TcpExcludedPortRange -Port $candidate) -or -not (Test-TcpBindable -ListenHostValue $ListenHostValue -Port $candidate)) {
            continue
        }
        Write-Warning "Falling back to available port $candidate."
        return $candidate
    }

    throw "No available TCP port was found starting from $port."
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
    $resolvedEnvFile = (Resolve-Path $EnvFile).Path
    [Environment]::SetEnvironmentVariable("ENV_FILE", $resolvedEnvFile, "Process")
    Import-EnvFile -Path $resolvedEnvFile
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
$resolvedPort = [int][Environment]::GetEnvironmentVariable("API_PORT", "Process")
$selectedPort = Resolve-ListenPort -ListenHostValue $resolvedHost -RequestedPort $resolvedPort
if ($selectedPort -ne $resolvedPort) {
    [Environment]::SetEnvironmentVariable("API_PORT", "$selectedPort", "Process")
}
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
