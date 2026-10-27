#!/usr/bin/env bash
set -euo pipefail

if [[ $# -eq 0 ]]; then
  echo "Uso: bash instalar.sh <modelo> [<modelo> ...]   (ejemplo: bash instalar.sh nombre-del-modelo)" >&2
  exit 1
fi
for model in "$@"; do
  echo "[INFO] ollama pull $model"
  ollama pull "$model"
done
echo "[SUCCESS] Instalación terminada."
