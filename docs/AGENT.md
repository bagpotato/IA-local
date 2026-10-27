# Agente local multimodal

Agente que corre en el **host** (venv de Python) junto a Ollama y expone una API compatible con OpenAI para clientes como opencode. Docker se usa solo para los contenedores efímeros del sandbox de código; Qdrant corre embebido (carpeta `data/qdrant`), sin servidor.

```
opencode (host) ──► http://127.0.0.1:8000/v1 ──► agente (host) ──► Ollama (host)
                                                                   ├─► Qdrant (embebido, data/qdrant)
                                                                   └─► sandbox: docker run --rm --network none …
```

## Instalación

Requisitos: Python 3.11+, Docker, Ollama funcionando en el host y [opencode](https://opencode.ai) como cliente.

```bash
bash scripts/install.sh      # venv, dependencias, elección de modelos de Ollama, imagen del sandbox, voz de Piper, .env
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

Requiere Docker y la imagen `python:3.12-slim` (sandbox); la memoria usa Qdrant embebido, sin servidor.

**3. CrewAI** — mismas herramientas y mismo registro; un router decide qué roles (research, vision, executor) hacen falta.

```env
AGENT_ENGINE=crewai
```

```bash
.venv/bin/python -m pip install -r requirements-crewai.txt
```

Modelos: no hay ninguno por defecto. `install.sh` te pregunta `LLM_MODEL` (obligatorio, con tool-calling), `VISION_MODEL` y `EMBED_MODEL`, y los guarda en `.env`. Mistral 7B no sirve para tool-calling fiable.

## Herramientas

| Grupo | Herramientas |
|---|---|
| Web | `web_search`, `web_news`, `fetch_page`, `web_research` |
| Ficheros (`ENABLE_FILES`) | `list_files`, `read_file`, `write_file`*, `run_command`*, `describe_image` |
| Código (`ENABLE_CODE`) | `run_python`* |
| Pantalla/webcam (`ENABLE_SCREEN`) | `look_at_screen`, `look_through_webcam` |
| Memoria (`ENABLE_MEMORY`) | `remember`*, `recall` |
| Subagentes (`ENABLE_SUBAGENTS`) | `delegate` |

\* peligrosas: se bloquean tras tocar contenido web o de pantalla en el mismo turno (`TAINT_LOCK=true`). Si el agente lo menciona, repite la orden en un mensaje nuevo.

Límites: el workspace es `~/ia-workspace`; `run_command` solo admite `ls pwd cat head tail wc grep uniq date df du uname stat file`, sin shell ni rutas fuera del workspace; `run_python` usa un contenedor sin red, de solo lectura y sin montajes.

## Subagentes y paralelismo

Para ir más rápido y con menos ruido en el contexto principal (solo motor `native`):

- **`delegate`**: el agente reparte 2 o más tareas independientes (investigar varios temas, comparar opciones...) entre subagentes que corren en paralelo y recibe un resultado breve de cada uno. Cada subagente tiene su propio contexto, un máximo de `SUBAGENT_ITERATIONS` pasos y solo herramientas no peligrosas (sin `write_file`, `run_command`, `run_python`, `remember`) y sin poder delegar. Si un subagente lee web o pantalla, el taint lock se aplica también al agente principal. Sus resultados llegan como `<subagent_result untrusted>`.
- **Herramientas en paralelo**: si el modelo pide varias herramientas en un mismo paso, las no peligrosas se ejecutan a la vez; las peligrosas, después y una a una.

Ajustes en `.env`: `ENABLE_SUBAGENTS`, `MAX_SUBAGENTS` (tareas por llamada), `SUBAGENT_ITERATIONS`, `SUBAGENT_CONCURRENCY`. Los subagentes comparten el mismo Ollama: la ganancia de velocidad real depende de `OLLAMA_NUM_PARALLEL` y de la memoria disponible; con 1 solo hueco se ejecutan por turnos y se gana sobre todo en contexto limpio. CrewAI no usa `delegate`.

## Conexión con opencode

`opencode.json` (raíz del repo) registra el agente como proveedor OpenAI-compatible (`http://127.0.0.1:8000/v1`, clave desde la variable `AGENT_API_KEY`) y deja Ollama como proveedor directo. Con la clave exportada, abre opencode en la carpeta del repo y elige el modelo **ia-local/ia-agent** (para usar tus modelos de Ollama directamente, añádelos en `provider.ollama.models` de `opencode.json`):

```bash
export AGENT_API_KEY=$(grep ^AGENT_API_KEY= .env | cut -d= -f2-)
opencode
```

Los estados de herramienta aparecen como líneas en cursiva dentro de la respuesta.

## Red, firewall y Tailscale

- El agente escucha por defecto en `127.0.0.1:8000` (`AGENT_HOST`). Para usarlo desde otros dispositivos, pon `AGENT_HOST` con la IP de Tailscale (`tailscale ip -4`) y no abras el 8000 a la LAN.
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
