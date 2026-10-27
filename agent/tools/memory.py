from __future__ import annotations

import threading
import time
import uuid
from typing import Any

from pydantic import BaseModel, Field

from agent import llm
from agent.config import Settings
from agent.registry import Ctx, ToolSpec

COLLECTION = "agent_memory"
_lock = threading.Lock()
_clients: dict[str, Any] = {}


class RememberArgs(BaseModel):
    text: str = Field(..., min_length=1, max_length=2000, description="Hecho a recordar, en una frase clara")


class RecallArgs(BaseModel):
    query: str = Field(..., min_length=1, max_length=500, description="Qué quieres recordar")
    k: int = Field(5, ge=1, le=10, description="Número de recuerdos")


def _client(settings: Settings):
    from qdrant_client import QdrantClient

    url = settings.qdrant_url
    with _lock:
        if url not in _clients:
            _clients[url] = QdrantClient(location=":memory:") if url == ":memory:" else QdrantClient(url=url, timeout=10)
        return _clients[url]


def _embed(settings: Settings, text: str) -> list[float]:
    return llm.embed_sync(settings, text)


def _ensure_collection(client, dim: int) -> None:
    from qdrant_client import models

    if not client.collection_exists(COLLECTION):
        client.create_collection(
            collection_name=COLLECTION,
            vectors_config=models.VectorParams(size=dim, distance=models.Distance.COSINE),
        )


def _remember(ctx: Ctx, a: RememberArgs) -> str:
    from qdrant_client import models

    vec = _embed(ctx.settings, a.text)
    client = _client(ctx.settings)
    _ensure_collection(client, len(vec))
    client.upsert(
        collection_name=COLLECTION,
        points=[models.PointStruct(id=str(uuid.uuid4()), vector=vec, payload={"text": a.text, "ts": time.time()})],
    )
    return "Guardado en la memoria."


def _recall(ctx: Ctx, a: RecallArgs) -> str:
    client = _client(ctx.settings)
    if not client.collection_exists(COLLECTION):
        return "La memoria está vacía."
    vec = _embed(ctx.settings, a.query)
    res = client.query_points(collection_name=COLLECTION, query=vec, limit=a.k, with_payload=True)
    if not res.points:
        return "No hay recuerdos relacionados."
    return "\n".join(
        f"[{i}] ({p.score:.2f}) {(p.payload or {}).get('text', '')}" for i, p in enumerate(res.points, 1)
    )


SPECS = [
    ToolSpec(
        "remember", "Guarda un dato en la memoria a largo plazo (Qdrant).",
        RememberArgs, _remember, "memory", "executor", dangerous=True,
    ),
    ToolSpec(
        "recall", "Busca en la memoria a largo plazo datos guardados antes.",
        RecallArgs, _recall, "memory", "executor",
    ),
]
