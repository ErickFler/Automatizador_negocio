$ErrorActionPreference = "Stop"

$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$Python = Join-Path $ProjectRoot ".build-venv\Scripts\python.exe"
$Executable = Join-Path $ProjectRoot "dist\ConcentradoVentas\ConcentradoVentas.exe"
$CompilerCandidates = @(
    "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
    "$env:ProgramFiles\Inno Setup 6\ISCC.exe"
)
$Compiler = $CompilerCandidates | Where-Object { Test-Path $_ } | Select-Object -First 1

if (-not $Compiler) {
    throw "No se encontró Inno Setup 6. Instálalo y vuelve a ejecutar este script."
}
if (-not (Test-Path $Python)) {
    throw "Primero ejecuta packaging\windows\build.ps1."
}
if (-not (Test-Path $Executable)) {
    throw "No existe el ejecutable. Primero ejecuta packaging\windows\build.ps1."
}

Set-Location $ProjectRoot
$AppVersion = (& $Python -c "from app import __version__; print(__version__)").Trim()
& $Compiler "/DMyAppVersion=$AppVersion" "packaging\windows\installer.iss"
Write-Host "Instalador creado en: dist\installer" -ForegroundColor Green
