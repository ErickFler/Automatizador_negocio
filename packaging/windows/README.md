# Compilación para Windows

## Requisitos del equipo de compilación

- Windows 10 u 11 de 64 bits.
- Python 3.12 disponible mediante el comando `py`.
- Inno Setup 6, únicamente si se desea crear el instalador.

## Crear la aplicación

Desde PowerShell, en la raíz del proyecto:

```powershell
powershell -ExecutionPolicy Bypass -File packaging\windows\build.ps1
```

El ejecutable se genera en:

```text
dist\ConcentradoVentas\ConcentradoVentas.exe
```

## Crear el instalador

Después de compilar la aplicación:

```powershell
powershell -ExecutionPolicy Bypass -File packaging\windows\build-installer.ps1
```

El instalador se genera en `dist\installer`.

## Datos del usuario

La aplicación no escribe dentro de la carpeta instalada. El histórico, archivos
cargados, exportaciones y logs se guardan en:

```text
%LOCALAPPDATA%\ConcentradoVentas
```

Esta carpeta no se elimina automáticamente al desinstalar la aplicación.
