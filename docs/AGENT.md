# Local multimodal agent

The agent runs on the **host** (Python venv) next to Ollama and exposes an OpenAI-compatible API for clients such as opencode. Docker is used only for the ephemeral code-sandbox containers; Qdrant runs embedded (`data/qdrant` folder), with no server.

```
opencode (host) ──► http://127.0.0.1:8000/v1 ──► agent (host) ──► Ollama (host)
                                                                   ├─► Qdrant (embedded, data/qdrant)
                                                                   └─► sandbox: docker run --rm --network none ...
```

## Installation

Requirements: Python 3.11+, Docker, Ollama running on the host and [opencode](https://opencode.ai) as the client.

```bash
bash scripts/install.sh      # venv, dependencies, Ollama model selection, sandbox image, Piper voice, .env
bash scripts/run.sh          # starts the agent on :8000
```

`install.sh` creates `.env` with a random `AGENT_API_KEY` and does not print it. To see it: `grep AGENT_API_KEY .env`. Make sure `.env`, `.venv/`, `data/` and `voices/` are in your `.gitignore`.

Tests (no network or Ollama needed): `.venv/bin/python -m pytest`

## Modes

**1. Minimal** — direct tool-calling loop against Ollama, no CrewAI, web and files only.

```env
AGENT_ENGINE=native
ENABLE_CODE=false
ENABLE_MEMORY=false
ENABLE_SCREEN=false
```

**2. Full and safe** — adds the code sandbox, memory and screen vision.

```env
AGENT_ENGINE=native
ENABLE_CODE=true
ENABLE_MEMORY=true
ENABLE_SCREEN=true
```

Requires Docker and the `python:3.12-slim` image (sandbox); memory uses embedded Qdrant, with no server.

**3. CrewAI** — same tools and registry; a router decides which roles (research, vision, executor) are needed.

```env
AGENT_ENGINE=crewai
```

```bash
.venv/bin/python -m pip install -r requirements-crewai.txt
```

Models: none is preselected. `install.sh` asks for `LLM_MODEL` (required, with tool-calling), `VISION_MODEL` and `EMBED_MODEL`, and saves them to `.env`. Mistral 7B is not reliable for tool-calling.

## Tools

| Group | Tools |
|---|---|
| Web | `web_search`, `web_news`, `fetch_page`, `web_research` |
| Files (`ENABLE_FILES`) | `list_files`, `read_file`, `write_file`*, `run_command`*, `describe_image` |
| Code (`ENABLE_CODE`) | `run_python`* |
| Screen/webcam (`ENABLE_SCREEN`) | `look_at_screen`, `look_through_webcam` |
| Memory (`ENABLE_MEMORY`) | `remember`*, `recall` |
| Subagents (`ENABLE_SUBAGENTS`) | `delegate` |

\* dangerous: blocked after touching web or screen content in the same turn (`TAINT_LOCK=true`). If the agent mentions it, repeat the request in a new message.

Limits: the workspace is `~/ia-workspace`; `run_command` only allows `ls pwd cat head tail wc grep uniq date df du uname stat file`, with no shell and no paths outside the workspace; `run_python` uses a container with no network, read-only and with no mounts.

## Subagents and parallelism

To work faster and keep the main context clean (`native` engine only):

- **`delegate`**: the agent splits 2 or more independent tasks (researching several topics, comparing options...) across subagents running in parallel and gets a short result from each. Each subagent has its own context, at most `SUBAGENT_ITERATIONS` steps, only non-dangerous tools (no `write_file`, `run_command`, `run_python`, `remember`) and cannot delegate. If a subagent reads web or screen content, the taint lock also applies to the main agent. Results arrive as `<subagent_result untrusted>`.
- **Parallel tools**: if the model requests several tools in one step, the non-dangerous ones run at once; the dangerous ones run afterwards, one at a time.

`.env` settings: `ENABLE_SUBAGENTS`, `MAX_SUBAGENTS` (tasks per call), `SUBAGENT_ITERATIONS`, `SUBAGENT_CONCURRENCY`. Subagents share the same Ollama: real speedup depends on `OLLAMA_NUM_PARALLEL` and available memory; with a single slot they run in turns and the gain is mostly a cleaner context. CrewAI does not use `delegate`.

## opencode connection

`opencode.json` (repo root) registers the agent as an OpenAI-compatible provider (`http://127.0.0.1:8000/v1`, key from the `AGENT_API_KEY` variable) and keeps Ollama as a direct provider. With the key exported, open opencode in the repo folder and pick the model **ia-local/ia-agent** (to use your Ollama models directly, add them under `provider.ollama.models` in `opencode.json`):

```bash
export AGENT_API_KEY=$(grep ^AGENT_API_KEY= .env | cut -d= -f2-)
opencode
```

Tool statuses appear as italic lines inside the response.

## Network

- The agent listens on `127.0.0.1:8000` by default (`AGENT_HOST`). Do not expose it to the LAN unless necessary.
- The whole API requires `AGENT_API_KEY` (`Authorization: Bearer ...` or `X-API-Key` header); only `/health` is public. Without a key, the server does not start.

## Internet access

The system prompt includes the date and time and requires `web_research` for data that changes, cross-checking sources, saying "not found" when nothing turns up, and citing with [n] plus a "Sources:" list. All web text arrives inside `<web_content untrusted>` and the model is forbidden from obeying instructions found there.

Every request and redirect goes through the SSRF filter: http/https only, no credentials, DNS resolved, and any non-global IP is blocked (loopback, private, link-local, the `100.64.0.0/10` CGNAT range, IPv4-mapped IPv6). Maximum 5 redirects, 2 MB, limited total time and text content only.

## Limitations

- **Wayland:** screen capture uses `grim` if installed; without it, `mss` only works under X11/XWayland.
- **DNS rebinding:** the resolved IP is validated before connecting, but the connection resolves the name again, so a malicious DNS server could change the answer in between. Not fully covered.
- **Context:** `NUM_CTX=8192` by default. Raise it to `16384` for better research (uses more memory).
- **CrewAI** does not stream tokens: the response arrives whole at the end.
- TTS returns WAV (Piper) even if the client asks for another format. With `TTS_ALLOW_ONLINE=true` it may use gTTS (sends the text to an external service and returns MP3).
- No clipboard access, on purpose.

## Endpoints

All require the API key except `/health`.

| Method and path | Description |
|---|---|
| `GET /health` | Status, engine, model, active tools and whether Ollama responds (public) |
| `POST /chat` | JSON: `content`, `session_id?`, `image_b64?`, `speak?`. Returns `response`, `tools_used`, `audio_base64?` |
| `POST /audio-chat` | Multipart: `file`, `session_id?`, `speak?`. Transcribes, answers and synthesizes if requested |
| `POST /screenshot` | JPEG of the screen (only with `ENABLE_SCREEN=true`) |
| `GET /search?q=` | Web search results |
| `GET /v1/models` | Model `ia-agent` |
| `POST /v1/chat/completions` | OpenAI format, with or without SSE `stream`; `data:` images are described with the vision model |
| `POST /v1/audio/transcriptions` | Multipart: `file`, `language?`. Returns `{"text": ...}` |
| `POST /v1/audio/speech` | JSON: `input`, ... Returns WAV audio |

Example:

```bash
curl -s http://127.0.0.1:8000/chat \
  -H "Authorization: Bearer $AGENT_API_KEY" -H "Content-Type: application/json" \
  -d '{"content": "What is the latest stable Python version?"}'
```
