$ErrorActionPreference = "Stop"

$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$BuildEnvironment = Join-Path $ProjectRoot ".build-venv"
Set-Location $ProjectRoot

if (-not (Test-Path $BuildEnvironment)) {
    py -3.12 -m venv $BuildEnvironment
}

$Python = Join-Path $BuildEnvironment "Scripts\python.exe"
& $Python -m pip install --upgrade pip
& $Python -m pip install -e ".[packaging]"
& $Python -m PyInstaller --clean --noconfirm "packaging\windows\ConcentradoVentas.spec"

Write-Host ""
Write-Host "Aplicación creada en: dist\ConcentradoVentas\ConcentradoVentas.exe" -ForegroundColor Green
