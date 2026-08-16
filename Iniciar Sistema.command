#!/bin/zsh
set -u

PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$PROJECT_DIR"

if [[ ! -x ".venv/bin/python" ]]; then
  echo "No se encontró el entorno local .venv. Consulta README.md para instalarlo."
  read -k 1 "?Presiona una tecla para cerrar..."
  exit 1
fi

exec .venv/bin/python scripts/launch.py

