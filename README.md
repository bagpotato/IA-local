# IA-local

Agente de IA local y privado. **Ollama** ejecuta los modelos y un agente multimodal (FastAPI, API compatible con OpenAI) añade búsqueda web, ficheros, sandbox de código, memoria, visión, voz y subagentes. Se usa desde [opencode](https://opencode.ai).

```
opencode ──► http://127.0.0.1:8000/v1 ──► agente ──► Ollama
                                            ├─ Qdrant embebido (data/qdrant)
                                            └─ sandbox: docker run --rm --network none
```

Documentación completa: [docs/AGENT.md](docs/AGENT.md)

## Requisitos

- Python 3.11+
- [Ollama](https://ollama.com) en el host
- Docker, solo para el sandbox de `run_python` (sin él, `ENABLE_CODE=false`)
- opencode

## Instalación y uso

```bash
bash scripts/install.sh      # venv, dependencias, elección de modelos, sandbox, voz y .env
bash scripts/run.sh          # arranca el agente en 127.0.0.1:8000

export AGENT_API_KEY=$(grep ^AGENT_API_KEY= .env | cut -d= -f2-)
opencode                     # modelo: ia-local/ia-agent
```

`install.sh` pregunta qué modelos de Ollama usar y los guarda en `.env`; no hay ninguno por defecto:

| Variable | Uso | Obligatoria |
|---|---|---|
| `LLM_MODEL` | LLM del agente (con tool-calling) | sí |
| `VISION_MODEL` | Imágenes y pantalla | no |
| `EMBED_MODEL` | Embeddings de la memoria | no |

También genera una `AGENT_API_KEY` aleatoria. Toda la API la exige salvo `/health`.

## Funciones

- **Web:** `web_search`, `web_news`, `fetch_page`, `web_research`, con filtro SSRF.
- **Ficheros y comandos:** limitados al workspace y a una lista de comandos permitidos.
- **Código:** `run_python` en un contenedor Docker efímero, sin red.
- **Memoria:** Qdrant embebido en `data/qdrant`.
- **Visión y voz:** pantalla, webcam, Whisper y Piper.
- **Subagentes:** `delegate` reparte tareas independientes en paralelo (`SUBAGENT_*` en `.env`).
- **Motores:** `AGENT_ENGINE=native` (por defecto) o `crewai` (`pip install -r requirements-crewai.txt`).

Tras leer contenido web o de pantalla se bloquean las herramientas peligrosas en ese turno (`TAINT_LOCK`).

## Tests

```bash
.venv/bin/python -m pytest      # Windows: .venv\Scripts\python -m pytest
```

No necesitan red ni Ollama. En Windows se omiten los tests que requieren symlinks o `ls`.
