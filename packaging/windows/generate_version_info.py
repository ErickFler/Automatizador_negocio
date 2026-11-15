"""Genera metadatos Windows desde la única versión declarada por la app."""

from argparse import ArgumentParser
from pathlib import Path


TEMPLATE = """VSVersionInfo(
  ffi=FixedFileInfo(
    filevers=({version_tuple}),
    prodvers=({version_tuple}),
    mask=0x3f,
    flags=0x0,
    OS=0x40004,
    fileType=0x1,
    subtype=0x0,
    date=(0, 0)
  ),
  kids=[
    StringFileInfo([
      StringTable(
        '040A04B0',
        [
          StringStruct('FileDescription', 'Sistema Local de Concentrado de Ventas'),
          StringStruct('FileVersion', '{version}'),
          StringStruct('InternalName', 'ConcentradoVentas'),
          StringStruct('OriginalFilename', 'ConcentradoVentas.exe'),
          StringStruct('ProductName', 'Concentrado de Ventas'),
          StringStruct('ProductVersion', '{version}')
        ]
      )
    ]),
    VarFileInfo([VarStruct('Translation', [0x040A, 1200])])
  ]
)
"""


def version_tuple(version: str) -> tuple[int, int, int, int]:
    parts = version.split(".")
    if len(parts) != 3 or not all(part.isdigit() for part in parts):
        raise ValueError("La versión debe utilizar el formato mayor.menor.parche.")
    return int(parts[0]), int(parts[1]), int(parts[2]), 0


def write_version_info(version: str, destination: Path) -> None:
    numbers = version_tuple(version)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        TEMPLATE.format(
            version=version,
            version_tuple=", ".join(str(number) for number in numbers),
        ),
        encoding="utf-8",
    )


def main() -> None:
    parser = ArgumentParser()
    parser.add_argument("version")
    parser.add_argument("destination", type=Path)
    arguments = parser.parse_args()
    write_version_info(arguments.version, arguments.destination)


if __name__ == "__main__":
    main()
