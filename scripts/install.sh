#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

say() { printf '[install] %s\n' "$*"; }
warn() { printf '[install] WARNING: %s\n' "$*" >&2; }

command -v python3 >/dev/null || { echo "python3 is missing" >&2; exit 1; }
python3 - <<'PY' || { echo "Python 3.11 or newer is required" >&2; exit 1; }
import sys
sys.exit(0 if sys.version_info >= (3, 11) else 1)
PY

# .env with a random key (never printed)
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
  say "Created .env with a random AGENT_API_KEY"
elif ! grep -Eq '^AGENT_API_KEY=.+' .env; then
  warn "AGENT_API_KEY is empty in .env: set it before starting"
fi

env_get() {
  local value
  value="$(grep -E "^$1=" .env | tail -n 1 | cut -d= -f2- || true)"
  value="${value%\"}"; value="${value#\"}"
  printf '%s' "${value:-$2}"
}

# venv and dependencies
if [[ ! -d .venv ]]; then
  say "Creating virtual environment"
  python3 -m venv .venv
fi
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -r requirements.txt

# workspace and data
workspace="$(env_get WORKSPACE_DIR '~/ia-workspace')"
workspace="${workspace/#\~/$HOME}"
mkdir -p "$workspace" data voices

env_set() {
  local tmp
  tmp="$(mktemp)"
  grep -v "^$1=" .env > "$tmp" || true
  printf '%s=%s\n' "$1" "$2" >> "$tmp"
  cat "$tmp" > .env
  rm -f "$tmp"
}

# Ollama models: chosen by the user
choose_model() {
  local var="$1" label="$2" required="$3" current answer
  current="$(env_get "$var" "")"
  if [[ -n "$current" ]]; then
    say "$var=$current (already set in .env)"
  elif [[ -t 0 ]]; then
    if command -v ollama >/dev/null; then
      say "Models installed in Ollama:"
      ollama list 2>/dev/null | tail -n +2 | awk '{print "  - " $1}' || true
    fi
    read -r -p "[install] Model for $label ($var)$([[ $required == yes ]] || echo ' [Enter = skip]'): " answer
    while [[ -z "$answer" && $required == yes ]]; do
      read -r -p "[install] $var is required: " answer
    done
    [[ -n "$answer" ]] && env_set "$var" "$answer"
    current="$answer"
  else
    [[ $required == yes ]] && warn "$var is empty: set it in .env before starting"
  fi
  if [[ -n "$current" ]] && command -v ollama >/dev/null; then
    if ollama list 2>/dev/null | awk 'NR>1{print $1}' | grep -qx "$current" || ollama list 2>/dev/null | awk 'NR>1{print $1}' | grep -qx "$current:latest"; then
      say "$current is already in Ollama"
    else
      read -r -p "[install] Download $current with ollama pull? [Y/n] " answer </dev/tty || answer=n
      [[ "${answer:-Y}" =~ ^[Yy]$ ]] && { ollama pull "$current" || warn "Could not download $current"; }
    fi
  fi
}

choose_model LLM_MODEL "the agent LLM (needs tool-calling)" yes
choose_model VISION_MODEL "vision (describe images and screen)" no
choose_model EMBED_MODEL "embeddings (memory)" no
command -v ollama >/dev/null || warn "ollama is not installed: download the models manually"

# code sandbox image
if command -v docker >/dev/null; then
  docker pull "$(env_get SANDBOX_IMAGE python:3.12-slim)" || warn "Could not download the sandbox image"
else
  warn "docker is not installed: run_python will not work"
fi

# Piper voice (non-fatal)
voice="$(env_get PIPER_VOICE es_ES-davefx-medium)"
.venv/bin/python -m piper.download_voices "$voice" --data-dir "$(env_get PIPER_DATA_DIR voices)" \
  || warn "Could not download voice $voice (TTS will be unavailable)"

say "Done. Start with: bash scripts/run.sh"
