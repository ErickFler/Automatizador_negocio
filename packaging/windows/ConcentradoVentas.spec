from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules

project_root = Path(SPECPATH).parents[1]
version_file = project_root / "build" / "windows" / "version_info.txt"
if not version_file.is_file():
    raise SystemExit(
        "Falta build/windows/version_info.txt. Ejecuta packaging/windows/build.ps1."
    )
hidden_imports = (
    collect_submodules("openpyxl")
    + collect_submodules("sqlalchemy.dialects.sqlite")
    + collect_submodules("uvicorn")
)

analysis = Analysis(
    [str(project_root / "scripts" / "launch.py")],
    pathex=[str(project_root)],
    binaries=[],
    datas=[
        (str(project_root / "app" / "templates"), "app/templates"),
        (str(project_root / "app" / "static"), "app/static"),
    ],
    hiddenimports=hidden_imports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["pytest"],
    noarchive=False,
    optimize=1,
)

python_archive = PYZ(analysis.pure)

executable = EXE(
    python_archive,
    analysis.scripts,
    [],
    exclude_binaries=True,
    name="ConcentradoVentas",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(project_root / "assets" / "app.ico"),
    version=str(version_file),
)

distribution = COLLECT(
    executable,
    analysis.binaries,
    analysis.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="ConcentradoVentas",
)
