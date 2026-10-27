from __future__ import annotations

import sqlite3
import threading
import time
from pathlib import Path


class Store:
    """Historial de conversaciones en SQLite."""

    def __init__(self, path: str | Path) -> None:
        path = str(path)
        if path != ":memory:":
            Path(path).expanduser().parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(path, check_same_thread=False)
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute(
                "CREATE TABLE IF NOT EXISTS turns ("
                "id INTEGER PRIMARY KEY AUTOINCREMENT,"
                "session_id TEXT NOT NULL,"
                "role TEXT NOT NULL,"
                "content TEXT NOT NULL,"
                "ts REAL NOT NULL)"
            )
            self._conn.execute("CREATE INDEX IF NOT EXISTS idx_turns_session ON turns(session_id, id)")
            self._conn.commit()

    def add_turn(self, session_id: str, role: str, content: str) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO turns(session_id, role, content, ts) VALUES (?, ?, ?, ?)",
                (session_id, role, content, time.time()),
            )
            self._conn.commit()

    def history(self, session_id: str, limit: int = 20) -> list[dict]:
        if limit <= 0:
            return []
        with self._lock:
            rows = self._conn.execute(
                "SELECT role, content FROM ("
                "SELECT id, role, content FROM turns WHERE session_id = ? ORDER BY id DESC LIMIT ?"
                ") ORDER BY id ASC",
                (session_id, limit),
            ).fetchall()
        return [{"role": r, "content": c} for r, c in rows]

    def clear(self, session_id: str) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM turns WHERE session_id = ?", (session_id,))
            self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()
