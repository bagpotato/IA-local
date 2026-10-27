# Agente local multimodal

Agente que corre en el **host** (venv de Python) junto a Ollama y expone una API compatible con OpenAI para Open WebUI. Docker se usa solo para Open WebUI, Qdrant y los contenedores efímeros del sandbox de código.

```
Open WebUI (Docker) ──► http://host.docker.internal:8000/v1 ──► agente (host) ──► Ollama (host)
                                                                   ├─► Qdrant (Docker, 127.0.0.1:6333)
                                                                   └─► sandbox: docker run --rm --network none …
```

## Instalación

Requisitos: Python 3.11+, Docker, Ollama funcionando en el host.

```bash
bash scripts/install.sh      # venv, dependencias, modelos de Ollama, imagen del sandbox, voz de Piper, .env
docker compose up -d         # Open WebUI + Qdrant (lee docker-compose.yml y docker-compose.override.yml)
bash scripts/run.sh          # arranca el agente en :8000
```

`install.sh` crea `.env` con una `AGENT_API_KEY` aleatoria y no la imprime. Para verla: `grep AGENT_API_KEY .env`. Asegúrate de que `.env`, `.venv/`, `data/` y `voices/` estén en tu `.gitignore`.

Tests (sin red ni Ollama): `.venv/bin/python -m pytest`

## Modos

**1. Mínimo** — bucle de tool-calling directo contra Ollama, sin CrewAI, solo web y ficheros.

```env
AGENT_ENGINE=native
ENABLE_CODE=false
ENABLE_MEMORY=false
ENABLE_SCREEN=false
```

**2. Completo y seguro** — añade sandbox de código, memoria y visión de pantalla.

```env
AGENT_ENGINE=native
ENABLE_CODE=true
ENABLE_MEMORY=true
ENABLE_SCREEN=true
```

Requiere `docker compose up -d` (Qdrant) y la imagen `python:3.12-slim`.

**3. CrewAI** — mismas herramientas y mismo registro; un router decide qué roles (research, vision, executor) hacen falta.

```env
AGENT_ENGINE=crewai
```

```bash
.venv/bin/python -m pip install -r requirements-crewai.txt
```

Modelo por defecto: `llama3.1:8b`. Alternativa: `qwen2.5` (`LLM_MODEL=qwen2.5:7b`). Mistral 7B no sirve para tool-calling fiable.

## Herramientas

| Grupo | Herramientas |
|---|---|
| Web | `web_search`, `web_news`, `fetch_page`, `web_research` |
| Ficheros (`ENABLE_FILES`) | `list_files`, `read_file`, `write_file`*, `run_command`*, `describe_image` |
| Código (`ENABLE_CODE`) | `run_python`* |
| Pantalla/webcam (`ENABLE_SCREEN`) | `look_at_screen`, `look_through_webcam` |
| Memoria (`ENABLE_MEMORY`) | `remember`*, `recall` |

\* peligrosas: se bloquean tras tocar contenido web o de pantalla en el mismo turno (`TAINT_LOCK=true`). Si el agente lo menciona, repite la orden en un mensaje nuevo.

Límites: el workspace es `~/ia-workspace`; `run_command` solo admite `ls pwd cat head tail wc grep uniq date df du uname stat file`, sin shell ni rutas fuera del workspace; `run_python` usa un contenedor sin red, de solo lectura y sin montajes.

## Conexión con Open WebUI

`docker-compose.override.yml` ya apunta Open WebUI al agente (`OPENAI_API_BASE_URL`, clave, STT y TTS tipo OpenAI). Tras `docker compose up -d` aparece el modelo **ia-agent**.

**Task model (importante).** Open WebUI envía los títulos de chat, las etiquetas y las sugerencias al mismo modelo que estés usando, y no deben disparar búsquedas web. En *Ajustes → Admin → Interfaz* pon como **Task Model** (local) un modelo normal de Ollama, por ejemplo `llama3.1:8b`, no `ia-agent`.

Los estados de herramienta aparecen como líneas en cursiva dentro de la respuesta.

## Red, firewall y Tailscale

- El agente escucha en `0.0.0.0:8000` para que Docker lo alcance. No abras el 8000 a la LAN sin necesidad: limita el acceso al puente de Docker y a Tailscale, y deja que los dispositivos remotos entren por Open WebUI (puerto 3000 por Tailscale).
- Ejemplo con ufw: `sudo ufw deny 8000/tcp` y `sudo ufw allow in on docker0 to any port 8000 proto tcp` (ajusta el nombre de la interfaz a tu sistema).
- Si no necesitas que Docker llegue por la interfaz externa, `AGENT_HOST=172.17.0.1` (IP de `docker0`) reduce la exposición.
- Toda la API exige `AGENT_API_KEY` (cabecera `Authorization: Bearer …` o `X-API-Key`); solo `/health` es público. Sin clave, el servidor no arranca.

## Acceso a internet

El prompt del sistema incluye fecha y hora y obliga a usar `web_research` para datos que cambian, contrastar fuentes, decir "no lo he encontrado" y citar con [n] más una lista "Fuentes:". Todo el texto web llega dentro de `<web_content untrusted>` y el modelo tiene prohibido obedecer instrucciones que aparezcan ahí.

Cada petición y redirección pasa por el filtro SSRF: solo http/https, sin credenciales, DNS resuelto y bloqueo de cualquier IP no global (loopback, privadas, link-local, `100.64.0.0/10` de Tailscale, IPv4 mapeada en IPv6). Máximo 5 redirecciones, 2 MB, tiempo total limitado y solo contenido de texto.

## Limitaciones

- **Wayland:** la captura de pantalla usa `grim` si está instalado; sin él, `mss` solo funciona bajo X11/XWayland.
- **Rebinding de DNS:** se valida la IP resuelta antes de conectar, pero la conexión vuelve a resolver el nombre, así que un servidor DNS malicioso podría cambiar la respuesta entre ambas. No está cubierto del todo.
- **Contexto:** `NUM_CTX=8192` por defecto. Sube a `16384` para investigar mejor (consume más memoria).
- **CrewAI** no hace streaming de tokens: la respuesta llega entera al final.
- El TTS devuelve WAV (Piper) aunque el cliente pida otro formato. Con `TTS_ALLOW_ONLINE=true` puede usar gTTS (envía el texto a un servicio externo y devuelve MP3).
- Sin portapapeles, a propósito.

## Endpoints

Todos exigen API key salvo `/health`.

| Método y ruta | Descripción |
|---|---|
| `GET /health` | Estado, motor, modelo, herramientas activas y si Ollama responde (público) |
| `POST /chat` | JSON: `content`, `session_id?`, `image_b64?`, `speak?`. Devuelve `response`, `tools_used`, `audio_base64?` |
| `POST /audio-chat` | Multipart: `file`, `session_id?`, `speak?`. Transcribe, responde y sintetiza si se pide |
| `POST /screenshot` | JPEG de la pantalla (solo con `ENABLE_SCREEN=true`) |
| `GET /search?q=` | Resultados de búsqueda web |
| `GET /v1/models` | Modelo `ia-agent` |
| `POST /v1/chat/completions` | Formato OpenAI, con `stream` SSE o sin él; las imágenes `data:` se describen con el modelo de visión |
| `POST /v1/audio/transcriptions` | Multipart: `file`, `language?`. Devuelve `{"text": …}` |
| `POST /v1/audio/speech` | JSON: `input`, … Devuelve audio WAV |

Ejemplo:

```bash
curl -s http://127.0.0.1:8000/chat \
  -H "Authorization: Bearer $AGENT_API_KEY" -H "Content-Type: application/json" \
  -d '{"content": "¿Qué versión estable de Python hay ahora?"}'
```
