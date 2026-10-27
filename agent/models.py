import re
from typing import Any

from pydantic import BaseModel, Field

SESSION_PATTERN = r"^[A-Za-z0-9_-]{1,64}$"
SESSION_RE = re.compile(SESSION_PATTERN)
MODEL_ID = "ia-agent"


class ChatRequest(BaseModel):
    content: str = Field(..., min_length=1, max_length=20000)
    session_id: str | None = Field(None, pattern=SESSION_PATTERN)
    image_b64: str | None = Field(None, max_length=14_000_000)
    speak: bool = False


class ChatResponse(BaseModel):
    response: str
    session_id: str
    tools_used: list[str] = []
    audio_base64: str | None = None
    audio_mime: str | None = None
    audio_error: str | None = None


class AudioChatResponse(ChatResponse):
    transcript: str = ""


class OAMessage(BaseModel):
    role: str = Field(..., max_length=32)
    content: str | list[dict[str, Any]] | None = None


class ChatCompletionRequest(BaseModel):
    model: str = Field(MODEL_ID, max_length=200)
    messages: list[OAMessage] = Field(..., min_length=1, max_length=200)
    stream: bool = False


class SpeechRequest(BaseModel):
    model: str = Field("tts-1", max_length=100)
    input: str = Field(..., min_length=1, max_length=5000)
    voice: str = Field("alloy", max_length=100)
    response_format: str = Field("wav", max_length=20)
    speed: float = Field(1.0, ge=0.25, le=4.0)
