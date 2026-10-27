# IA-local

![Skills](https://skillicons.dev/icons?i=docker,linux,bash,python,fastapi&theme=dark)

Private, local AI environment: **Ollama** runs the models, **Open WebUI** is the chat interface, and a **local multimodal agent** (FastAPI, OpenAI-compatible API) adds web research, file tools, a Docker code sandbox, memory (Qdrant), screen/webcam vision and voice. Remote access goes through **Tailscale**, with no open ports on your router.

```
Open WebUI (Docker) --> http://host.docker.internal:8000/v1 --> agent (host) --> Ollama
                                                                  |- Qdrant (Docker, 127.0.0.1:6333)
                                                                  `- sandbox: docker run --rm --network none ...
```

Full agent documentation (modes, tools, security, endpoints): [docs/AGENT.md](docs/AGENT.md)

## Table of Contents
1. [Repository layout](#1-repository-layout)
2. [Installing Docker](#2-installing-docker)
3. [Installing Ollama](#3-installing-ollama)
4. [Basic stack: Ollama + Open WebUI](#4-basic-stack-ollama--open-webui)
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
| `docker-compose.yml` | Basic stack: Ollama + Open WebUI (data in `ollama_data/` and `webui_data/`) |
| `docker-compose.override.yml` | Points Open WebUI at the agent and adds Qdrant. Requires `AGENT_API_KEY` in `.env` |
| `instalar.sh` | Starts the basic stack and pulls the small models (`qwen2.5-coder:1.5b`, Gemma-3-1B) into the Ollama container |
| `coder_shell.py` | Minimal CLI that queries `qwen2.5-coder:1.5b` on `localhost:11434` (needs `requests`) |
| `agent/` | The agent: engines (native / CrewAI), tools, security, storage and tests |
| `scripts/install.sh`, `scripts/run.sh` | Install and run the agent (venv, models, sandbox image, Piper voice, `.env`) |
| `.env.example` | Agent configuration template |
| `docs/AGENT.md` | Detailed agent documentation (in Spanish) |

> `docker-compose.override.yml` is loaded automatically by `docker compose up -d`. Without `AGENT_API_KEY` defined in `.env`, the command fails. Run `bash scripts/install.sh` first (it generates the key), or run only the basic stack with `docker compose -f docker-compose.yml up -d`.

---

## 2. Installing Docker
Docker is essential to isolate the services and to simplify updating the web interface and other system components.

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

### Network Configuration for Ollama
For Ollama to accept external connections (required for the Docker web interface and remote access), edit the service:

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

## 4. Basic stack: Ollama + Open WebUI
`docker-compose.yml` starts Ollama and **Open WebUI** (100% local, similar to commercial chat UIs):

```bash
docker compose -f docker-compose.yml up -d
bash instalar.sh        # optional: pulls the small models
```

Open WebUI is served on `http://localhost:3000`. Update it with `docker compose pull && docker compose up -d`.

> The Ollama container publishes `11434` on all interfaces. Limit it with your firewall or bind it to `127.0.0.1:11434:11434` if you do not need LAN access.

---

## 5. Local agent
The agent runs on the host (Python venv) next to Ollama and exposes an OpenAI-compatible API on port `8000` that Open WebUI consumes (model **ia-agent**).

```bash
bash scripts/install.sh      # venv, dependencies, Ollama models, sandbox image, Piper voice, .env
docker compose up -d         # Open WebUI + Qdrant (reads docker-compose.yml and the override)
bash scripts/run.sh          # starts the agent on :8000
```

* `install.sh` creates `.env` with a random `AGENT_API_KEY` and does not print it. The whole API requires it except `/health`.
* Engines: `AGENT_ENGINE=native` (default) or `crewai` (`pip install -r requirements-crewai.txt`).
* Tools: web (`web_search`, `web_news`, `fetch_page`, `web_research`), files, `run_python` sandbox, screen/webcam, memory. Dangerous tools are locked after touching untrusted web content in the same turn (`TAINT_LOCK`).
* In Open WebUI, set **Task Model** (*Settings -> Admin -> Interface*) to a plain Ollama model such as `llama3.1:8b`, not `ia-agent`.

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
Defaults come from `.env` (`LLM_MODEL`, `VISION_MODEL`, `EMBED_MODEL`). `scripts/install.sh` pulls them:

```bash
ollama pull llama3.1:8b          # agent LLM (tool-calling); alternative: qwen2.5:7b
ollama pull llava                # vision
ollama pull nomic-embed-text     # memory embeddings
```

Mistral 7B is not suitable for reliable tool-calling. `instalar.sh` pulls `qwen2.5-coder:1.5b` and Gemma-3-1B for the basic stack.

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
   `http://[TAILSCALE-IP]:3000`

---

## 9. Useful commands
* **Open WebUI logs:** `docker logs -f open-webui`
* **Update images:** `docker compose pull && docker compose up -d`
* **Agent health:** `curl http://127.0.0.1:8000/health`
* **Diagnostics:** ping the Tailscale IP from another device on the same account.
