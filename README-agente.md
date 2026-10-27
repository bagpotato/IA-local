# IA-local

Agente local multimodal que corre en el host junto a Ollama y expone una API compatible con OpenAI para Open WebUI. Incluye búsqueda web con filtro SSRF, herramientas de ficheros, sandbox de código en Docker, memoria con Qdrant, visión y voz.

Documentación completa: [docs/AGENT.md](docs/AGENT.md)

## Inicio rápido

Requisitos: Python 3.11+, Docker y Ollama.

```bash
bash scripts/install.sh
docker compose up -d
bash scripts/run.sh
```

El agente queda en `http://127.0.0.1:8000` y Open WebUI en el puerto 3000. `install.sh` genera `.env` con una `AGENT_API_KEY` aleatoria.

## Tests

```bash
.venv/bin/python -m pytest
```

No necesitan red ni Ollama. En Windows (`.venv\Scripts\python`) se omiten los tests que requieren symlinks y `ls`.

## Motores

Se elige con `AGENT_ENGINE` en `.env`: `native` (por defecto) o `crewai` (`pip install -r requirements-crewai.txt`).
