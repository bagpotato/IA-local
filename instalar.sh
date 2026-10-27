#!/usr/bin/env bash
set -euo pipefail

echo "[INFO] Descargando modelos pequeños en Ollama (nativo)..."
ollama pull qwen2.5-coder:1.5b
ollama pull hf.co/Andycurrent/Gemma-3-1B-it-GLM-4.7-Flash-Heretic-Uncensored-Thinking_GGUF:latest
echo "[SUCCESS] Instalación terminada."
