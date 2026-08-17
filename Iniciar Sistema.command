#!/bin/zsh
set -u

PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
PYTHON_BIN="$PROJECT_DIR/.venv/bin/python"

if [[ ! -x "$PYTHON_BIN" ]]; then
  echo "No se encontró el entorno local .venv. Consulta README.md para instalarlo."
  read -k 1 "?Presiona una tecla para cerrar..."
  exit 1
fi

exec "$PYTHON_BIN" "$PROJECT_DIR/scripts/launch.py"
