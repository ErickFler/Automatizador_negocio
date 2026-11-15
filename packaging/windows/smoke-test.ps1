param(
    [Parameter(Mandatory = $true)]
    [string]$Executable
)

$ErrorActionPreference = "Stop"
$Executable = (Resolve-Path $Executable).Path
$SmokeRoot = Join-Path $env:TEMP ("ConcentradoVentas-Smoke-" + [guid]::NewGuid())
$PreviousDataDirectory = $env:APP_DATA_DIR
$PreviousPort = $env:APP_PORT
$PrimaryProcess = $null
$RestartProcess = $null

function Get-FreePort {
    $Listener = [System.Net.Sockets.TcpListener]::new(
        [System.Net.IPAddress]::Loopback,
        0
    )
    $Listener.Start()
    $Port = $Listener.LocalEndpoint.Port
    $Listener.Stop()
    return $Port
}

try {
    New-Item -ItemType Directory -Path $SmokeRoot | Out-Null
    $env:APP_DATA_DIR = $SmokeRoot
    $env:APP_PORT = [string](Get-FreePort)
    $BaseUrl = "http://127.0.0.1:$($env:APP_PORT)/"

    $PrimaryProcess = Start-Process $Executable -ArgumentList "--no-browser" -PassThru
    $Deadline = [DateTime]::UtcNow.AddSeconds(25)
    $Health = $null
    while ([DateTime]::UtcNow -lt $Deadline) {
        try {
            $Health = Invoke-RestMethod -Uri ($BaseUrl + "health") -TimeoutSec 1
            if ($Health.status -eq "ok") { break }
        } catch {
            Start-Sleep -Milliseconds 200
        }
    }
    if (-not $Health -or $Health.status -ne "ok") {
        throw "El ejecutable no respondió /health dentro del tiempo esperado."
    }

    $Home = Invoke-WebRequest -UseBasicParsing -Uri $BaseUrl -TimeoutSec 3
    $Static = Invoke-WebRequest -UseBasicParsing -Uri ($BaseUrl + "static/css/app.css") -TimeoutSec 3
    if ($Home.StatusCode -ne 200 -or $Static.StatusCode -ne 200) {
        throw "La página principal o los recursos estáticos no respondieron correctamente."
    }

    $SecondProcess = Start-Process $Executable -ArgumentList "--no-browser" -PassThru -Wait
    if ($SecondProcess.ExitCode -ne 0 -or $PrimaryProcess.HasExited) {
        throw "La segunda ejecución no reutilizó correctamente la instancia existente."
    }

    $TokenMatch = [regex]::Match(
        $Home.Content,
        'name="shutdown_token" value="([^"]+)"'
    )
    if (-not $TokenMatch.Success) {
        throw "No se encontró el token local de apagado."
    }
    $Shutdown = Invoke-WebRequest -UseBasicParsing `
        -Uri ($BaseUrl + "system/shutdown") `
        -Method Post `
        -Body @{ shutdown_token = $TokenMatch.Groups[1].Value } `
        -TimeoutSec 5
    if ($Shutdown.StatusCode -ne 200 -or $Shutdown.Content -notmatch "Sistema apagado") {
        throw "El apagado no devolvió la página final esperada."
    }

    if (-not $PrimaryProcess.WaitForExit(10000)) {
        throw "El proceso no terminó después del apagado."
    }
    if (-not (Test-Path (Join-Path $SmokeRoot "app.db"))) {
        throw "La primera ejecución no creó la base persistente."
    }
    foreach ($Directory in @("uploads", "cost_uploads", "logs")) {
        if (-not (Test-Path (Join-Path $SmokeRoot $Directory))) {
            throw "No se creó el directorio runtime $Directory."
        }
    }

    $RestartProcess = Start-Process $Executable -ArgumentList "--no-browser" -PassThru
    $RestartDeadline = [DateTime]::UtcNow.AddSeconds(25)
    $RestartHealth = $null
    while ([DateTime]::UtcNow -lt $RestartDeadline) {
        try {
            $RestartHealth = Invoke-RestMethod -Uri ($BaseUrl + "health") -TimeoutSec 1
            if ($RestartHealth.status -eq "ok") { break }
        } catch {
            Start-Sleep -Milliseconds 200
        }
    }
    if (-not $RestartHealth -or $RestartHealth.status -ne "ok") {
        throw "El ejecutable no respondió después del reinicio."
    }
    $RestartHome = Invoke-WebRequest -UseBasicParsing -Uri $BaseUrl -TimeoutSec 3
    $RestartToken = [regex]::Match(
        $RestartHome.Content,
        'name="shutdown_token" value="([^"]+)"'
    )
    if (-not $RestartToken.Success) {
        throw "No se encontró el token después del reinicio."
    }
    Invoke-WebRequest -UseBasicParsing `
        -Uri ($BaseUrl + "system/shutdown") `
        -Method Post `
        -Body @{ shutdown_token = $RestartToken.Groups[1].Value } `
        -TimeoutSec 5 | Out-Null
    if (-not $RestartProcess.WaitForExit(10000)) {
        throw "El proceso reiniciado no terminó correctamente."
    }

    $BuildPython = Join-Path $PSScriptRoot "..\..\.build-venv\Scripts\python.exe"
    $DatabaseResult = (& $BuildPython `
        (Join-Path $PSScriptRoot "verify_runtime_db.py") `
        (Join-Path $SmokeRoot "app.db")).Trim()
    if ($DatabaseResult -ne "integrity=ok;foreign_key_violations=0;branches=4") {
        throw "La base runtime no superó integridad: $DatabaseResult"
    }

    Write-Host "Smoke test aprobado: health, static, segunda instancia, shutdown, reinicio y DB." -ForegroundColor Green
} finally {
    if ($PrimaryProcess -and -not $PrimaryProcess.HasExited) {
        Stop-Process -Id $PrimaryProcess.Id -Force
    }
    if ($RestartProcess -and -not $RestartProcess.HasExited) {
        Stop-Process -Id $RestartProcess.Id -Force
    }
    $env:APP_DATA_DIR = $PreviousDataDirectory
    $env:APP_PORT = $PreviousPort
    if (Test-Path $SmokeRoot) {
        Remove-Item -Recurse -Force $SmokeRoot
    }
}
