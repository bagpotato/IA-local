#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

say() { printf '[install] %s\n' "$*"; }
warn() { printf '[install] AVISO: %s\n' "$*" >&2; }

command -v python3 >/dev/null || { echo "Falta python3" >&2; exit 1; }
python3 - <<'PY' || { echo "Se necesita Python 3.11 o superior" >&2; exit 1; }
import sys
sys.exit(0 if sys.version_info >= (3, 11) else 1)
PY

# .env con clave aleatoria (nunca se imprime)
umask 077
if [[ ! -f .env ]]; then
  python3 - <<'PY'
import pathlib
import secrets

lines = []
for line in pathlib.Path(".env.example").read_text().splitlines():
    if line.startswith("AGENT_API_KEY="):
        line = "AGENT_API_KEY=" + secrets.token_urlsafe(32)
    lines.append(line)
env = pathlib.Path(".env")
env.write_text("\n".join(lines) + "\n")
env.chmod(0o600)
PY
  say "Creado .env con una AGENT_API_KEY aleatoria"
elif ! grep -Eq '^AGENT_API_KEY=.+' .env; then
  warn "AGENT_API_KEY está vacía en .env: defínela antes de arrancar"
fi

env_get() {
  local value
  value="$(grep -E "^$1=" .env | tail -n 1 | cut -d= -f2- || true)"
  value="${value%\"}"; value="${value#\"}"
  printf '%s' "${value:-$2}"
}

# venv y dependencias
if [[ ! -d .venv ]]; then
  say "Creando entorno virtual"
  python3 -m venv .venv
fi
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -r requirements.txt

# workspace y datos
workspace="$(env_get WORKSPACE_DIR '~/ia-workspace')"
workspace="${workspace/#\~/$HOME}"
mkdir -p "$workspace" data voices

# modelos de Ollama
if command -v ollama >/dev/null; then
  for model in "$(env_get LLM_MODEL llama3.1:8b)" "$(env_get VISION_MODEL llava)" "$(env_get EMBED_MODEL nomic-embed-text)"; do
    say "ollama pull $model"
    ollama pull "$model" || warn "No se pudo descargar $model"
  done
else
  warn "ollama no está instalado: descarga los modelos a mano"
fi

# imagen del sandbox de código
if command -v docker >/dev/null; then
  docker pull "$(env_get SANDBOX_IMAGE python:3.12-slim)" || warn "No se pudo descargar la imagen del sandbox"
else
  warn "docker no está instalado: run_python no funcionará"
fi

# voz de Piper (no fatal)
voice="$(env_get PIPER_VOICE es_ES-davefx-medium)"
.venv/bin/python -m piper.download_voices "$voice" --data-dir "$(env_get PIPER_DATA_DIR voices)" \
  || warn "No se pudo descargar la voz $voice (el TTS no estará disponible)"

say "Listo. Arranca con: bash scripts/run.sh"
