"""
chat_history_db.py — Persistent chat history store for Ollama Manager Pro.

Storage (SQLite):
    Windows : %APPDATA%/7h3v01d/chat_history.db
    Linux   : ~/.config/7h3v01d/chat_history.db

Schema
------
  sessions  — one row per conversation
  messages  — one row per message, FK → sessions
"""
from __future__ import annotations

import json
import os
import platform
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path


# ── Data classes ───────────────────────────────────────────────────────────

@dataclass
class ChatSession:
    id: int
    model: str
    system_prompt: str
    title: str
    created_at: str
    updated_at: str
    message_count: int = 0


@dataclass
class ChatMessage:
    id: int
    session_id: int
    role: str          # user | assistant
    content: str
    created_at: str


# ── Database ───────────────────────────────────────────────────────────────

class ChatHistoryDB:
    """Lightweight SQLite store for chat sessions and messages."""

    SCHEMA_VERSION = 1

    def __init__(self):
        self._path = self._default_path()
        self._con = sqlite3.connect(str(self._path), check_same_thread=False)
        self._con.row_factory = sqlite3.Row
        self._con.execute("PRAGMA journal_mode=WAL")
        self._migrate()

    # ── Setup ──────────────────────────────────────────────────────────────

    @staticmethod
    def _default_path() -> Path:
        if platform.system() == "Windows":
            base = Path(os.environ.get("APPDATA", Path.home()))
        else:
            base = Path.home() / ".config"
        p = base / "7h3v01d"
        p.mkdir(parents=True, exist_ok=True)
        return p / "chat_history.db"

    def _migrate(self):
        cur = self._con.cursor()
        cur.executescript("""
            CREATE TABLE IF NOT EXISTS sessions (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                model         TEXT    NOT NULL DEFAULT '',
                system_prompt TEXT    NOT NULL DEFAULT '',
                title         TEXT    NOT NULL DEFAULT 'Untitled',
                created_at    TEXT    NOT NULL DEFAULT '',
                updated_at    TEXT    NOT NULL DEFAULT ''
            );
            CREATE TABLE IF NOT EXISTS messages (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
                role       TEXT    NOT NULL,
                content    TEXT    NOT NULL DEFAULT '',
                created_at TEXT    NOT NULL DEFAULT ''
            );
            CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id);
            CREATE TABLE IF NOT EXISTS meta (
                key TEXT PRIMARY KEY,
                value TEXT
            );
        """)
        cur.execute("INSERT OR IGNORE INTO meta VALUES ('schema_version', ?)",
                    (str(self.SCHEMA_VERSION),))
        self._con.commit()

    # ── Sessions ───────────────────────────────────────────────────────────

    def list_sessions(self, limit: int = 100) -> list[ChatSession]:
        rows = self._con.execute("""
            SELECT s.*, COUNT(m.id) AS message_count
            FROM sessions s
            LEFT JOIN messages m ON m.session_id = s.id
            GROUP BY s.id
            ORDER BY s.updated_at DESC
            LIMIT ?
        """, (limit,)).fetchall()
        return [ChatSession(
            id=r["id"], model=r["model"], system_prompt=r["system_prompt"],
            title=r["title"], created_at=r["created_at"],
            updated_at=r["updated_at"], message_count=r["message_count"],
        ) for r in rows]

    def create_session(self, model: str, system_prompt: str = "",
                       title: str = "New Chat") -> int:
        now = datetime.now().isoformat(timespec="seconds")
        cur = self._con.execute(
            "INSERT INTO sessions (model, system_prompt, title, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (model, system_prompt, title, now, now),
        )
        self._con.commit()
        return cur.lastrowid

    def update_session_title(self, session_id: int, title: str):
        now = datetime.now().isoformat(timespec="seconds")
        self._con.execute(
            "UPDATE sessions SET title=?, updated_at=? WHERE id=?",
            (title, now, session_id),
        )
        self._con.commit()

    def update_session_system_prompt(self, session_id: int, system_prompt: str):
        now = datetime.now().isoformat(timespec="seconds")
        self._con.execute(
            "UPDATE sessions SET system_prompt=?, updated_at=? WHERE id=?",
            (system_prompt, now, session_id),
        )
        self._con.commit()

    def touch_session(self, session_id: int):
        now = datetime.now().isoformat(timespec="seconds")
        self._con.execute(
            "UPDATE sessions SET updated_at=? WHERE id=?", (now, session_id))
        self._con.commit()

    def delete_session(self, session_id: int):
        self._con.execute("DELETE FROM sessions WHERE id=?", (session_id,))
        self._con.commit()

    def get_session(self, session_id: int) -> ChatSession | None:
        r = self._con.execute(
            "SELECT * FROM sessions WHERE id=?", (session_id,)).fetchone()
        if not r:
            return None
        return ChatSession(id=r["id"], model=r["model"],
                           system_prompt=r["system_prompt"],
                           title=r["title"], created_at=r["created_at"],
                           updated_at=r["updated_at"])

    # ── Messages ───────────────────────────────────────────────────────────

    def add_message(self, session_id: int, role: str, content: str) -> int:
        now = datetime.now().isoformat(timespec="seconds")
        cur = self._con.execute(
            "INSERT INTO messages (session_id, role, content, created_at) VALUES (?,?,?,?)",
            (session_id, role, content, now),
        )
        self.touch_session(session_id)
        self._con.commit()
        return cur.lastrowid

    def get_messages(self, session_id: int) -> list[ChatMessage]:
        rows = self._con.execute(
            "SELECT * FROM messages WHERE session_id=? ORDER BY id ASC",
            (session_id,),
        ).fetchall()
        return [ChatMessage(id=r["id"], session_id=r["session_id"],
                            role=r["role"], content=r["content"],
                            created_at=r["created_at"]) for r in rows]

    def get_messages_as_dicts(self, session_id: int) -> list[dict]:
        return [{"role": m.role, "content": m.content}
                for m in self.get_messages(session_id)]

    # ── Housekeeping ───────────────────────────────────────────────────────

    def prune_old_sessions(self, keep: int = 200):
        """Delete sessions beyond the most recent `keep`."""
        self._con.execute("""
            DELETE FROM sessions WHERE id NOT IN (
                SELECT id FROM sessions ORDER BY updated_at DESC LIMIT ?
            )
        """, (keep,))
        self._con.commit()

    def close(self):
        self._con.close()
