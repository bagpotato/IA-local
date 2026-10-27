# IA-local

Private, local AI agent. **Ollama** runs the models and a multimodal agent (FastAPI, OpenAI-compatible API) adds web search, files, a code sandbox, memory, vision, voice and subagents. Use it from [opencode](https://opencode.ai).

```
opencode ──► http://127.0.0.1:8000/v1 ──► agent ──► Ollama
                                            ├─ embedded Qdrant (data/qdrant)
                                            └─ sandbox: docker run --rm --network none
```

Full documentation: [docs/AGENT.md](docs/AGENT.md)

## Requirements

- Python 3.11+
- [Ollama](https://ollama.com) on the host
- Docker, only for the `run_python` sandbox (without it, set `ENABLE_CODE=false`)
- opencode

## Install and run

```bash
bash scripts/install.sh      # venv, dependencies, model selection, sandbox image, voice and .env
bash scripts/run.sh          # starts the agent on 127.0.0.1:8000

export AGENT_API_KEY=$(grep ^AGENT_API_KEY= .env | cut -d= -f2-)
opencode                     # model: ia-local/ia-agent
```

`install.sh` asks which Ollama models to use and saves them to `.env`; none is preselected:

| Variable | Use | Required |
|---|---|---|
| `LLM_MODEL` | Agent LLM (needs tool-calling) | yes |
| `VISION_MODEL` | Images and screen | no |
| `EMBED_MODEL` | Memory embeddings | no |

It also generates a random `AGENT_API_KEY`. The whole API requires it except `/health`.

## Features

- **Web:** `web_search`, `web_news`, `fetch_page`, `web_research`, with an SSRF filter.
- **Files and commands:** limited to the workspace and an allowlist of commands.
- **Code:** `run_python` in an ephemeral Docker container with no network.
- **Memory:** embedded Qdrant in `data/qdrant`.
- **Vision and voice:** screen, webcam, Whisper and Piper.
- **Subagents:** `delegate` splits independent tasks across parallel subagents (`SUBAGENT_*` in `.env`).
- **Engines:** `AGENT_ENGINE=native` (default) or `crewai` (`pip install -r requirements-crewai.txt`).

After reading web or screen content, dangerous tools are blocked for that turn (`TAINT_LOCK`).

## Tests

```bash
.venv/bin/python -m pytest      # Windows: .venv\Scripts\python -m pytest
```

They need neither network nor Ollama. On Windows, tests that require symlinks or `ls` are skipped.
