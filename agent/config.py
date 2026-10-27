from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", extra="ignore", populate_by_name=True, validate_default=True
    )

    # Servidor
    agent_api_key: str = Field("", alias="AGENT_API_KEY")
    agent_host: str = Field("0.0.0.0", alias="AGENT_HOST")
    agent_port: int = Field(8000, alias="AGENT_PORT", ge=1, le=65535)
    agent_engine: Literal["native", "crewai"] = Field("native", alias="AGENT_ENGINE")
    agent_debug: bool = Field(False, alias="AGENT_DEBUG")
    agent_db_path: Path = Field(Path("data/agent.db"), alias="AGENT_DB_PATH")
    max_concurrent: int = Field(2, alias="MAX_CONCURRENT", ge=1, le=16)
    history_turns: int = Field(20, alias="HISTORY_TURNS", ge=0, le=100)

    # Ollama y modelos
    ollama_host: str = Field("http://127.0.0.1:11434", alias="OLLAMA_HOST")
    llm_model: str = Field("", alias="LLM_MODEL")
    vision_model: str = Field("", alias="VISION_MODEL")
    router_model: str = Field("", alias="ROUTER_MODEL")
    embed_model: str = Field("", alias="EMBED_MODEL")
    num_ctx: int = Field(8192, alias="NUM_CTX", ge=1024)
    max_iterations: int = Field(6, alias="MAX_ITERATIONS", ge=1, le=20)
    request_timeout: float = Field(300.0, alias="REQUEST_TIMEOUT", gt=0)

    # Grupos de herramientas
    enable_web: bool = Field(True, alias="ENABLE_WEB")
    enable_files: bool = Field(True, alias="ENABLE_FILES")
    enable_code: bool = Field(False, alias="ENABLE_CODE")
    enable_memory: bool = Field(False, alias="ENABLE_MEMORY")
    enable_screen: bool = Field(False, alias="ENABLE_SCREEN")
    taint_lock: bool = Field(True, alias="TAINT_LOCK")

    # Subagentes
    enable_subagents: bool = Field(True, alias="ENABLE_SUBAGENTS")
    max_subagents: int = Field(4, alias="MAX_SUBAGENTS", ge=1, le=8)
    subagent_iterations: int = Field(4, alias="SUBAGENT_ITERATIONS", ge=1, le=10)
    subagent_concurrency: int = Field(2, alias="SUBAGENT_CONCURRENCY", ge=1, le=8)

    # Web
    page_chars: int = Field(4000, alias="PAGE_CHARS", ge=500, le=20000)

    # Ficheros y límites
    workspace_dir: Path = Field(Path("~/ia-workspace"), alias="WORKSPACE_DIR")
    max_file_bytes: int = Field(1_000_000, alias="MAX_FILE_BYTES", ge=1024)
    max_image_bytes: int = Field(8_000_000, alias="MAX_IMAGE_BYTES", ge=1024)
    max_audio_bytes: int = Field(25_000_000, alias="MAX_AUDIO_BYTES", ge=1024)

    # Código
    sandbox_image: str = Field("python:3.12-slim", alias="SANDBOX_IMAGE")
    code_timeout: float = Field(20.0, alias="CODE_TIMEOUT", gt=0, le=120)

    # Memoria
    qdrant_url: str = Field("data/qdrant", alias="QDRANT_URL")

    # Visión
    webcam_index: int = Field(0, alias="WEBCAM_INDEX", ge=0)

    # Audio
    whisper_model: str = Field("base", alias="WHISPER_MODEL")
    whisper_device: str = Field("auto", alias="WHISPER_DEVICE")
    piper_voice: str = Field("es_ES-davefx-medium", alias="PIPER_VOICE")
    piper_data_dir: Path = Field(Path("voices"), alias="PIPER_DATA_DIR")
    tts_allow_online: bool = Field(False, alias="TTS_ALLOW_ONLINE")
    tts_lang: str = Field("es", alias="TTS_LANG")

    @field_validator("agent_engine", mode="before")
    @classmethod
    def _engine_lower(cls, v):
        return str(v).strip().lower()

    @field_validator("ollama_host", mode="after")
    @classmethod
    def _norm_host(cls, v: str) -> str:
        v = v.strip().rstrip("/")
        if "://" not in v:
            v = "http://" + v
        return v.replace("//0.0.0.0", "//127.0.0.1")

    @field_validator("workspace_dir", "piper_data_dir", "agent_db_path", mode="after")
    @classmethod
    def _expand(cls, v: Path) -> Path:
        return v.expanduser()

    @property
    def router_model_name(self) -> str:
        return self.router_model or self.llm_model


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
