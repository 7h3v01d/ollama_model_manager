"""
prompt_library.py — SQLite-backed prompt and system-prompt library.

Storage: %APPDATA%/KeystoneAI/prompt_library.db  (Windows)
         ~/.config/KeystoneAI/prompt_library.db   (Linux/Mac)

Schema
------
  prompts
    id          INTEGER PRIMARY KEY
    title       TEXT NOT NULL
    content     TEXT NOT NULL
    kind        TEXT  -- 'prompt' | 'system'
    tags        TEXT  -- comma-separated
    created_at  TEXT
    updated_at  TEXT
    use_count   INTEGER DEFAULT 0
    notes       TEXT DEFAULT ''
"""

import json
import os
import platform
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional


# ── Data class ────────────────────────────────────────────────────────────

@dataclass
class PromptEntry:
    title:      str
    content:    str
    kind:       str = "prompt"         # 'prompt' | 'system'
    tags:       list[str] = field(default_factory=list)
    notes:      str = ""
    id:         Optional[int] = None
    created_at: str = ""
    updated_at: str = ""
    use_count:  int = 0

    @property
    def tags_str(self) -> str:
        return ", ".join(self.tags)

    @staticmethod
    def _parse_tags(raw: str) -> list[str]:
        return [t.strip() for t in (raw or "").split(",") if t.strip()]

    def to_row(self) -> tuple:
        now = datetime.now().isoformat()
        return (
            self.title,
            self.content,
            self.kind,
            self.tags_str,
            self.created_at or now,
            now,
            self.use_count,
            self.notes,
        )

    @staticmethod
    def from_row(row: sqlite3.Row) -> "PromptEntry":
        return PromptEntry(
            id=row["id"],
            title=row["title"],
            content=row["content"],
            kind=row["kind"] or "prompt",
            tags=PromptEntry._parse_tags(row["tags"]),
            notes=row["notes"] or "",
            created_at=row["created_at"] or "",
            updated_at=row["updated_at"] or "",
            use_count=row["use_count"] or 0,
        )


# ── Library ───────────────────────────────────────────────────────────────

class PromptLibrary:
    """Thread-safe SQLite prompt library.  Create one instance per app."""

    VERSION = 1

    def __init__(self):
        self._path = self._default_path()
        self._con: sqlite3.Connection = sqlite3.connect(
            str(self._path), check_same_thread=False)
        self._con.row_factory = sqlite3.Row
        self._migrate()

    # ── Setup ─────────────────────────────────────────────────────────

    @staticmethod
    def _default_path() -> Path:
        if platform.system() == "Windows":
            base = Path(os.environ.get("APPDATA", Path.home()))
        else:
            base = Path.home() / ".config"
        p = base / "KeystoneAI"
        p.mkdir(parents=True, exist_ok=True)
        return p / "prompt_library.db"

    def _migrate(self):
        cur = self._con.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS prompts (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                title       TEXT    NOT NULL,
                content     TEXT    NOT NULL DEFAULT '',
                kind        TEXT    NOT NULL DEFAULT 'prompt',
                tags        TEXT    NOT NULL DEFAULT '',
                created_at  TEXT    NOT NULL DEFAULT '',
                updated_at  TEXT    NOT NULL DEFAULT '',
                use_count   INTEGER NOT NULL DEFAULT 0,
                notes       TEXT    NOT NULL DEFAULT ''
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS meta (
                key   TEXT PRIMARY KEY,
                value TEXT
            )
        """)
        cur.execute(
            "INSERT OR IGNORE INTO meta (key, value) VALUES (?, ?)",
            ("schema_version", str(self.VERSION))
        )
        # Seed with a few useful defaults if empty
        cur.execute("SELECT COUNT(*) FROM prompts")
        if cur.fetchone()[0] == 0:
            self._seed(cur)
        self._con.commit()

    def _seed(self, cur: sqlite3.Cursor):
        now = datetime.now().isoformat()
        seeds = [
            ("Explain simply", "prompt",
             "Explain the following in simple terms a non-technical person could understand:",
             "explain, simplify"),
            ("Summarise", "prompt",
             "Summarise the following text in 3-5 bullet points, focusing on the key takeaways:",
             "summary, bullets"),
            ("Code review", "prompt",
             "Review the following code for bugs, style issues, and potential improvements. "
             "Be specific about each issue found:",
             "code, review"),
            ("Write tests", "prompt",
             "Write comprehensive unit tests for the following code. "
             "Cover happy paths, edge cases, and error conditions:",
             "code, testing"),
            ("Helpful assistant", "system",
             "You are a helpful, accurate, and concise assistant. "
             "Answer clearly and directly. If you are unsure, say so.",
             "general"),
            ("Python expert", "system",
             "You are an expert Python developer with deep knowledge of PyQt6, "
             "async patterns, and software architecture. "
             "Provide idiomatic, production-quality code with clear explanations.",
             "code, python"),
            ("Socratic tutor", "system",
             "You are a Socratic tutor. Rather than giving direct answers, "
             "guide the user to discover solutions through thoughtful questions. "
             "Celebrate their reasoning and gently correct misconceptions.",
             "education, teaching"),
        ]
        for title, kind, content, tags in seeds:
            cur.execute(
                "INSERT INTO prompts (title, content, kind, tags, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (title, content, kind, tags, now, now)
            )

    # ── CRUD ──────────────────────────────────────────────────────────

    def add(self, entry: PromptEntry) -> PromptEntry:
        row = entry.to_row()
        cur = self._con.execute(
            "INSERT INTO prompts (title, content, kind, tags, created_at, updated_at, "
            "use_count, notes) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            row,
        )
        self._con.commit()
        entry.id = cur.lastrowid
        return entry

    def update(self, entry: PromptEntry) -> None:
        assert entry.id is not None
        now = datetime.now().isoformat()
        self._con.execute(
            "UPDATE prompts SET title=?, content=?, kind=?, tags=?, "
            "updated_at=?, use_count=?, notes=? WHERE id=?",
            (entry.title, entry.content, entry.kind, entry.tags_str,
             now, entry.use_count, entry.notes, entry.id),
        )
        self._con.commit()

    def delete(self, entry_id: int) -> None:
        self._con.execute("DELETE FROM prompts WHERE id=?", (entry_id,))
        self._con.commit()

    def record_use(self, entry_id: int) -> None:
        self._con.execute(
            "UPDATE prompts SET use_count = use_count + 1, "
            "updated_at = ? WHERE id = ?",
            (datetime.now().isoformat(), entry_id),
        )
        self._con.commit()

    # ── Queries ───────────────────────────────────────────────────────

    def all(self, kind: str = "") -> list[PromptEntry]:
        if kind:
            rows = self._con.execute(
                "SELECT * FROM prompts WHERE kind=? ORDER BY use_count DESC, title ASC",
                (kind,)
            ).fetchall()
        else:
            rows = self._con.execute(
                "SELECT * FROM prompts ORDER BY use_count DESC, title ASC"
            ).fetchall()
        return [PromptEntry.from_row(r) for r in rows]

    def search(self, query: str, kind: str = "") -> list[PromptEntry]:
        q = f"%{query.lower()}%"
        if kind:
            rows = self._con.execute(
                "SELECT * FROM prompts WHERE kind=? AND "
                "(LOWER(title) LIKE ? OR LOWER(content) LIKE ? OR LOWER(tags) LIKE ?) "
                "ORDER BY use_count DESC, title ASC",
                (kind, q, q, q)
            ).fetchall()
        else:
            rows = self._con.execute(
                "SELECT * FROM prompts WHERE "
                "(LOWER(title) LIKE ? OR LOWER(content) LIKE ? OR LOWER(tags) LIKE ?) "
                "ORDER BY use_count DESC, title ASC",
                (q, q, q)
            ).fetchall()
        return [PromptEntry.from_row(r) for r in rows]

    def by_tag(self, tag: str) -> list[PromptEntry]:
        rows = self._con.execute(
            "SELECT * FROM prompts WHERE tags LIKE ? "
            "ORDER BY use_count DESC, title ASC",
            (f"%{tag}%",)
        ).fetchall()
        return [PromptEntry.from_row(r) for r in rows]

    def all_tags(self) -> list[str]:
        rows = self._con.execute(
            "SELECT DISTINCT tags FROM prompts WHERE tags != ''"
        ).fetchall()
        tags: set[str] = set()
        for row in rows:
            for t in PromptEntry._parse_tags(row[0]):
                tags.add(t)
        return sorted(tags)

    def get(self, entry_id: int) -> Optional[PromptEntry]:
        row = self._con.execute(
            "SELECT * FROM prompts WHERE id=?", (entry_id,)
        ).fetchone()
        return PromptEntry.from_row(row) if row else None

    def db_path(self) -> Path:
        return self._path

    def close(self):
        self._con.close()
