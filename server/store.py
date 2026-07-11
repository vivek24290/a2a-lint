"""
Session store for shareable permalinks — a SQLite file, nothing more.
A session is a snapshot of an inspection result; the permalink renders it
instantly and offers a live re-inspect.
"""
import json
import os
import secrets
import sqlite3
import threading
import time


class SessionStore:
    def __init__(self, path: str):
        directory = os.path.dirname(path)
        if directory:
            os.makedirs(directory, exist_ok=True)
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._lock = threading.Lock()
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS sessions "
            "(id TEXT PRIMARY KEY, created REAL, payload TEXT)"
        )
        self._conn.commit()

    def save(self, payload: dict) -> str:
        session_id = secrets.token_urlsafe(6)
        with self._lock:
            self._conn.execute(
                "INSERT INTO sessions VALUES (?, ?, ?)",
                (session_id, time.time(), json.dumps(payload)),
            )
            self._conn.commit()
        return session_id

    def load(self, session_id: str) -> dict | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT payload FROM sessions WHERE id = ?", (session_id,)
            ).fetchone()
        return json.loads(row[0]) if row else None
