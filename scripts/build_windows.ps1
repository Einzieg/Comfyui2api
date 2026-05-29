$ErrorActionPreference = "Stop"

$Root = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
$Frontend = Join-Path $Root "frontend"
$FrontendDist = Join-Path $Frontend "dist"
$WebUiDist = Join-Path $Root "src\comfyui2api\webui_dist"
$DistRoot = Join-Path $Root "dist\comfyui2api"

function Invoke-Native {
    & $args[0] @($args | Select-Object -Skip 1)
    if ($LASTEXITCODE -ne 0) {
        throw "Command failed with exit code ${LASTEXITCODE}: $($args -join ' ')"
    }
}

Write-Host "Building frontend..."
if ($env:CI) {
    Invoke-Native pnpm --dir $Frontend install --frozen-lockfile
}
else {
    Invoke-Native pnpm --dir $Frontend install
}
Invoke-Native pnpm --dir $Frontend build

Write-Host "Copying frontend dist..."
if (Test-Path -LiteralPath $WebUiDist) {
    $ResolvedWebUiDist = (Resolve-Path -LiteralPath $WebUiDist).Path
    if (-not $ResolvedWebUiDist.StartsWith($Root + [IO.Path]::DirectorySeparatorChar)) {
        throw "Refusing to remove unexpected path: $ResolvedWebUiDist"
    }
    Remove-Item -LiteralPath $ResolvedWebUiDist -Recurse -Force
}
New-Item -ItemType Directory -Force -Path $WebUiDist | Out-Null
Copy-Item -Path (Join-Path $FrontendDist "*") -Destination $WebUiDist -Recurse -Force

Write-Host "Building Python executables..."
Push-Location $Root
try {
    Invoke-Native uv sync --locked
    Invoke-Native uv run --with pyinstaller pyinstaller packaging/comfyui2api.spec --clean --noconfirm
}
finally {
    Pop-Location
}

Write-Host "Preparing runtime folders..."
New-Item -ItemType Directory -Force -Path (Join-Path $DistRoot "comfyui-api-workflows") | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $DistRoot "runs") | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $DistRoot "data") | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $DistRoot "logs") | Out-Null

Write-Host "Done."
