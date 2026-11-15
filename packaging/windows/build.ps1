param(
    [switch]$SkipSmokeTest
)

$ErrorActionPreference = "Stop"

if ($env:OS -ne "Windows_NT" -or -not [Environment]::Is64BitOperatingSystem) {
    throw "El artefacto oficial debe compilarse en Windows 10/11 de 64 bits."
}

$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$BuildEnvironment = Join-Path $ProjectRoot ".build-venv"
$BuildOutput = Join-Path $ProjectRoot "build\ConcentradoVentas"
$Distribution = Join-Path $ProjectRoot "dist\ConcentradoVentas"
$VersionFile = Join-Path $ProjectRoot "build\windows\version_info.txt"
Set-Location $ProjectRoot
$Stopwatch = [System.Diagnostics.Stopwatch]::StartNew()

if (-not (Test-Path $BuildEnvironment)) {
    & py -3.12 -m venv $BuildEnvironment
}

$Python = Join-Path $BuildEnvironment "Scripts\python.exe"
$PythonBits = (& $Python -c "import struct; print(struct.calcsize('P') * 8)").Trim()
if ($PythonBits -ne "64") {
    throw "El build requiere Python 3.12 de 64 bits."
}
& $Python -m pip install --upgrade pip
& $Python -m pip install -e ".[packaging]"
$AppVersion = (& $Python -c "from app import __version__; print(__version__)").Trim()
$PyInstallerVersion = (& $Python -m PyInstaller --version).Trim()
& $Python "packaging\windows\generate_version_info.py" $AppVersion $VersionFile

if (Test-Path $BuildOutput) {
    Remove-Item -Recurse -Force $BuildOutput
}
if (Test-Path $Distribution) {
    Remove-Item -Recurse -Force $Distribution
}

& $Python -m PyInstaller --clean --noconfirm "packaging\windows\ConcentradoVentas.spec"

$Executable = Join-Path $Distribution "ConcentradoVentas.exe"
if (-not (Test-Path $Executable)) {
    throw "PyInstaller terminó sin crear $Executable"
}

if (-not $SkipSmokeTest) {
    & powershell -ExecutionPolicy Bypass -File `
        "packaging\windows\smoke-test.ps1" -Executable $Executable
}

$Stopwatch.Stop()
$SizeBytes = (Get-ChildItem $Distribution -Recurse -File | Measure-Object Length -Sum).Sum
$SizeMB = [Math]::Round($SizeBytes / 1MB, 1)

Write-Host ""
Write-Host "Aplicación $AppVersion creada en: $Executable" -ForegroundColor Green
Write-Host "PyInstaller: $PyInstallerVersion"
Write-Host "Modo: onedir | Tamaño: $SizeMB MB | Tiempo: $([Math]::Round($Stopwatch.Elapsed.TotalSeconds, 1)) s"
