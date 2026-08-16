$ErrorActionPreference = "Stop"

$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$CompilerCandidates = @(
    "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
    "$env:ProgramFiles\Inno Setup 6\ISCC.exe"
)
$Compiler = $CompilerCandidates | Where-Object { Test-Path $_ } | Select-Object -First 1

if (-not $Compiler) {
    throw "No se encontró Inno Setup 6. Instálalo y vuelve a ejecutar este script."
}

Set-Location $ProjectRoot
& $Compiler "packaging\windows\installer.iss"
Write-Host "Instalador creado en: dist\installer" -ForegroundColor Green
