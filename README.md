# Local AI Deployment Guide with Ollama, Docker, and Tailscale

![Skills](https://skillicons.dev/icons?i=docker,linux,bash,yaml&theme=dark)

This repository contains the configuration needed to deploy a private, secure Artificial Intelligence environment accessible from anywhere without opening ports on your router.

## Table of Contents
1. [Prerequisites](#prerequisites)
2. [Installing Docker](#1-installing-docker)
3. [Installing Ollama](#2-installing-ollama)
4. [Web Interface with Docker](#3-web-interface-with-docker)
5. [Remote Access with Tailscale (Critical)](#4-remote-access-with-tailscale-critical)
6. [Usage and Useful Commands](#5-usage-and-useful-commands)

---

## Prerequisites
* **Operating System:** Linux (Ubuntu recommended), Windows (WSL2) or macOS.
* **Hardware:** Minimum 8GB of RAM (16GB or more recommended for 7B+ models).
* **GPU (Optional):** NVIDIA with up-to-date drivers for hardware acceleration.

---

## 1. Installing Docker
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

## 2. Installing Ollama
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

## 3. Web Interface with Docker
We will use **Open WebUI** to get an optimized user experience, similar to commercial platforms but with 100% local execution.

Create a `docker-compose.yaml` file at the root of this project:

```yaml
services:
  open-webui:
    image: ghcr.io/open-webui/open-webui:main
    container_name: open-webui
    restart: always
    ports:
      - "3000:8080"
    environment:
      - OLLAMA_BASE_URL=http://host.docker.internal:11434
    extra_hosts:
      - "host.docker.internal:host-gateway"
    volumes:
      - open-webui:/app/backend/data

volumes:
  open-webui:
```

To start the container, run: `docker compose up -d`

---

## 4. Remote Access with Tailscale (Critical)
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

## 5. Usage and Useful Commands

### Model Management
Models must be downloaded manually before first use:
```bash
ollama pull llama3       # Balanced model from Meta
ollama pull mistral (not used in the repo)     # Optimized for efficiency
ollama pull llava (not used in the repo)       # Multimodal model (supports images)
```

### Interface Maintenance
* **View activity logs:** `docker logs -f open-webui`
* **Update to the latest version:**
  ```bash
  docker compose pull
  docker compose up -d
  ```

### Network Diagnostics
To confirm remote access is working, verify that you can ping the Tailscale IP from an external device connected to the same Tailscale account.
