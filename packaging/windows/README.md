# Build oficial para Windows

El artefacto oficial se compila únicamente en Windows 10/11 de 64 bits. No se
puede generar un `.exe` Windows válido ejecutando PyInstaller desde macOS.

## Requisitos del equipo de compilación

- Windows 10 u 11 de 64 bits.
- Python 3.12 de 64 bits disponible mediante `py -3.12`.
- Acceso a internet durante el build para instalar dependencias.
- PyInstaller 6.21.0 (el script lo instala en un entorno aislado).
- Inno Setup 6 para crear el instalador.

El usuario final no necesita Python ni internet.

## Aplicación onedir

Desde PowerShell en la raíz del repositorio:

```powershell
powershell -ExecutionPolicy Bypass -File packaging\windows\build.ps1
```

El script crea `.build-venv`, obtiene la versión desde `app.__version__`, genera
los metadatos Windows, limpia exclusivamente los directorios anteriores de esta
aplicación, ejecuta PyInstaller y corre el smoke test. Produce:

```text
dist\ConcentradoVentas\ConcentradoVentas.exe
```

Se utiliza `onedir` y modo `windowed`, sin consola. Templates, CSS, JavaScript,
gráficas locales e icono se incluyen explícitamente. Para diagnosticar un build
sin ejecutar el smoke test puede agregarse `-SkipSmokeTest`.

## Smoke test

También puede ejecutarse por separado:

```powershell
powershell -ExecutionPolicy Bypass -File packaging\windows\smoke-test.ps1 `
  -Executable dist\ConcentradoVentas\ConcentradoVentas.exe
```

Utiliza un puerto libre y una carpeta temporal independientes. Comprueba
ejecutable, `/health`, `/`, CSS, segunda instancia, base inicial, directorios
runtime y apagado. Nunca toca los datos normales del usuario.

## Instalador Inno Setup

Después de aprobar el build:

```powershell
powershell -ExecutionPolicy Bypass -File packaging\windows\build-installer.ps1
```

El instalador queda en `dist\installer`. Instala por usuario, crea acceso en el
menú Inicio y ofrece opcionalmente uno en el escritorio. La desinstalación
elimina el programa, pero no toca los datos del negocio.

## Datos persistentes

La carpeta exacta es:

```text
%LOCALAPPDATA%\ConcentradoVentas\
```

Contiene `app.db`, `uploads\`, `cost_uploads\` y `logs\`. Está separada de:

```text
%LOCALAPPDATA%\Programs\ConcentradoVentas\
```

Por ello actualizar o desinstalar los binarios no reemplaza los datos. Para
migrar una instalación, cerrar la aplicación y copiar `app.db`, `uploads\` y
`cost_uploads\` a la carpeta persistente del nuevo usuario.

## Validación de release

El smoke test no sustituye la lista `TEST_CHECKLIST.md`. El instalador debe
validarse en un Windows limpio sin Python, incluyendo importaciones reales,
Excel exportado, persistencia, desinstalación y reinicio.

Un ejecutable sin firma puede generar un aviso de SmartScreen. No se intenta
evadirlo; una distribución formal deberá evaluar firma de código.
