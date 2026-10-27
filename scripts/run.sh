#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

if [[ ! -x .venv/bin/python ]]; then
  echo "No hay entorno virtual. Ejecuta primero: bash scripts/install.sh" >&2
  exit 1
fi
if [[ ! -f .env ]]; then
  echo "Falta .env. Ejecuta primero: bash scripts/install.sh" >&2
  exit 1
fi

exec .venv/bin/python -m agent.main
