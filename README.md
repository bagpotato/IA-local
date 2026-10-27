# IA-local

![Skills](https://skillicons.dev/icons?i=docker,linux,bash,python,fastapi&theme=dark)

Private, local AI environment: **Ollama** runs the models, **opencode** is the client, and a **local multimodal agent** (FastAPI, OpenAI-compatible API) adds web research, file tools, a Docker code sandbox, memory (embedded Qdrant), screen/webcam vision and voice. Remote access goes through **Tailscale**, with no open ports on your router.

```
opencode (host) --> http://127.0.0.1:8000/v1 --> agent (host) --> Ollama (host)
                                                              |- Qdrant (embedded, data/qdrant)
                                                              `- sandbox: docker run --rm --network none ...
```

Full agent documentation (modes, tools, security, endpoints): [docs/AGENT.md](docs/AGENT.md)

## Table of Contents
1. [Repository layout](#1-repository-layout)
2. [Installing Docker](#2-installing-docker)
3. [Installing Ollama](#3-installing-ollama)
4. [Memory (Qdrant embedded)](#4-memory-qdrant-embedded)
5. [Local agent](#5-local-agent)
6. [Tests](#6-tests)
7. [Models](#7-models)
8. [Remote Access with Tailscale](#8-remote-access-with-tailscale-critical)
9. [Useful commands](#9-useful-commands)

## Prerequisites
* **OS:** Linux (Ubuntu recommended), Windows (WSL2) or macOS.
* **Hardware:** 8 GB RAM minimum (16 GB or more for 7B+ models).
* **GPU (optional):** NVIDIA with up-to-date drivers.
* **Agent:** Python 3.11+, Docker, Ollama.

---

## 1. Repository layout

| Path | Description |
|---|---|
| `opencode.json` | opencode config: agent (`ia-local/ia-agent`) and Ollama as providers |
| `agent/` | The agent: engines (native / CrewAI), tools, security, storage and tests |
| `scripts/install.sh`, `scripts/run.sh` | Install and run the agent (venv, models, sandbox image, Piper voice, `.env`) |
| `.env.example` | Agent configuration template |
| `docs/AGENT.md` | Detailed agent documentation (in Spanish) |

Docker is only used for the ephemeral `run_python` sandbox containers. Ollama and Qdrant (embedded, stored in `data/qdrant`) run on the host without Docker.

---

## 2. Installing Docker
Docker is only needed for the `run_python` code sandbox (optional: without it, set `ENABLE_CODE=false`).

### On Linux (Ubuntu/Debian)
```bash
# Update system and install dependencies
sudo apt update && sudo apt upgrade -y
sudo apt install ca-certificates curl gnupg lsb-release

# Add Docker's official GPG key
sudo install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
sudo chmod a+r /etc/apt/keyrings/docker.gpg

# Set up the official repository
echo \
  "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu \
  $(. /etc/os-release && echo "$VERSION_CODENAME") stable" | \
  sudo tee /etc/apt/sources.list.d/docker.list > /dev/null

# Install Docker Engine and Compose
sudo apt update
sudo apt install docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin

# Allow running without root (Optional)
sudo usermod -aG docker $USER
```

---

## 3. Installing Ollama
Ollama is the engine that runs large language models (LLMs) efficiently on the local server.

### Direct Installation
```bash
curl -fsSL https://ollama.com/install.sh | sh
```

### Network Configuration for Ollama (optional)
The agent talks to Ollama on `127.0.0.1:11434`, so this is not needed. Only if other machines must reach Ollama directly, edit the service:

1. Run: `sudo systemctl edit ollama.service`
2. Insert the following content:
   ```ini
   [Service]
   Environment="OLLAMA_HOST=0.0.0.0"
   Environment="OLLAMA_ORIGINS=*"
   ```
3. Restart the service to apply the changes:
   ```bash
   sudo systemctl daemon-reload
   sudo systemctl restart ollama
   ```

---

## 4. Memory (Qdrant embedded)
Agent memory uses Qdrant in embedded mode: no server and no Docker, data is stored in the folder set by `QDRANT_URL` (default `data/qdrant`). Only one agent process can open that folder at a time. To use a Qdrant server instead, set `QDRANT_URL=http://host:6333`.

---

## 5. Local agent
The agent runs on the host (Python venv) next to Ollama and exposes an OpenAI-compatible API on `127.0.0.1:8000` that **opencode** consumes (model `ia-local/ia-agent`, see `opencode.json`; add your Ollama models under `provider.ollama.models` to use them directly).

```bash
bash scripts/install.sh      # venv, dependencies, asks which Ollama models to use, sandbox image, Piper voice, .env
bash scripts/run.sh          # starts the agent on :8000
export AGENT_API_KEY=$(grep ^AGENT_API_KEY= .env | cut -d= -f2-)
opencode                     # https://opencode.ai
```

* `install.sh` creates `.env` with a random `AGENT_API_KEY` and does not print it. The whole API requires it except `/health`.
* Subagents: the `delegate` tool splits independent tasks across parallel sub-agents (safe tools only), and safe tool calls in one step run concurrently. Tune with `SUBAGENT_*` in `.env` (see docs/AGENT.md).
* Engines: `AGENT_ENGINE=native` (default) or `crewai` (`pip install -r requirements-crewai.txt`).
* Tools: web (`web_search`, `web_news`, `fetch_page`, `web_research`), files, `run_python` sandbox, screen/webcam, memory. Dangerous tools are locked after touching untrusted web content in the same turn (`TAINT_LOCK`).

See [docs/AGENT.md](docs/AGENT.md) for modes, security model (SSRF filter, command allowlist, workspace limits), endpoints and limitations.

---

## 6. Tests
They need neither network nor Ollama.

```bash
.venv/bin/python -m pytest          # Windows: .venv\Scripts\python -m pytest
```

On Windows, the tests that need symlinks or `ls` are skipped automatically.

---

## 7. Models
No model is preselected. `scripts/install.sh` asks which Ollama models to use and writes them to `.env` (offering `ollama pull` if they are missing):

| Variable | Use | Required |
|---|---|---|
| `LLM_MODEL` | Agent LLM; it must support tool-calling | yes |
| `VISION_MODEL` | Describe images and screen | no |
| `EMBED_MODEL` | Memory embeddings | no |

You can also edit them in `.env` at any time. The agent refuses to start if `LLM_MODEL` is empty. Not every model handles tool-calling reliably (Mistral 7B does not).

---

## 8. Remote Access with Tailscale (Critical)
Tailscale is the key piece for the project's security and portability. It creates a private virtual network (Mesh VPN) that allows accessing the server from anywhere without exposing the machine to the internet.

### Benefits of Tailscale
* **Security:** No need to open ports on the router (Port Forwarding), eliminating external attack vectors.
* **Static Internal IP:** The server receives a fixed IP inside the private network (e.g. 100.x.y.z) that does not change even when moving the machine physically.
* **E2E Encryption:** All traffic between your devices is end-to-end encrypted.

### Setup:
1. **Install on the server:**
   ```bash
   curl -fsSL https://tailscale.com/install.sh | sh
   sudo tailscale up
   ```
2. **Get the private IP:**
   Run `tailscale ip -4`. Use this address to connect from other devices.
3. **Remote access:**
   Install Tailscale on your mobile device or laptop. Once active, you can access the web interface via:
   `http://[TAILSCALE-IP]:8000` (set `AGENT_HOST` to the Tailscale IP and point opencode's `baseURL` at it)

---

## 9. Useful commands
* **Agent health:** `curl http://127.0.0.1:8000/health`
* **Diagnostics:** ping the Tailscale IP from another device on the same account.
