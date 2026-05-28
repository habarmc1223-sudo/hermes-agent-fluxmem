"""
Unified Memory Store — persistent user + project context.

Backed by SQLite (via HermesState) + optional ChromaDB for semantic search.
Replaces ephemeral conversation-only memory with long-term persistence.
"""

import json
import os
import sqlite3
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

STATE_DIR = Path(os.getenv("HERMES_STATE_DIR", Path.home() / ".hermes" / "state"))
STATE_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = STATE_DIR / "memory.db"


class MemoryStore:
    """Persistent key-value + semantic memory store."""

    def __init__(self, db_path: str = None):
        self.db_path = db_path or str(DB_PATH)
        self._init_db()

    def _init_db(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS memory_entries (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    key TEXT UNIQUE NOT NULL,
                    value TEXT NOT NULL,
                    category TEXT DEFAULT 'general',
                    project TEXT DEFAULT '',
                    user_id INTEGER DEFAULT 1,
                    importance INTEGER DEFAULT 5,
                    created_at TEXT DEFAULT (datetime('now')),
                    updated_at TEXT DEFAULT (datetime('now')),
                    access_count INTEGER DEFAULT 0,
                    last_accessed TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_memory_key ON memory_entries(key);
                CREATE INDEX IF NOT EXISTS idx_memory_category ON memory_entries(category);
                CREATE INDEX IF NOT EXISTS idx_memory_project ON memory_entries(project);
                CREATE TABLE IF NOT EXISTS memory_vectors (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    memory_id INTEGER REFERENCES memory_entries(id),
                    embedding BLOB,
                    model TEXT DEFAULT 'text-embedding-3-small'
                );
            """)
            conn.commit()

    def set(self, key: str, value: str, category: str = "general", project: str = "", importance: int = 5):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                INSERT INTO memory_entries (key, value, category, project, importance, updated_at)
                VALUES (?, ?, ?, ?, ?, datetime('now'))
                ON CONFLICT(key) DO UPDATE SET
                    value=excluded.value, category=excluded.category,
                    project=excluded.project, importance=excluded.importance,
                    updated_at=datetime('now'), access_count=access_count+1
            """, (key, value, category, project, importance))
            conn.commit()

    def get(self, key: str) -> Optional[str]:
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT value, access_count FROM memory_entries WHERE key=?",
                (key,)
            ).fetchone()
            if row:
                conn.execute(
                    "UPDATE memory_entries SET access_count=?, last_accessed=datetime('now') WHERE key=?",
                    (row["access_count"] + 1, key)
                )
                conn.commit()
                return row["value"]
        return None

    def get_by_category(self, category: str, limit: int = 20) -> list:
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT key, value, importance, updated_at FROM memory_entries WHERE category=? ORDER BY importance DESC, updated_at DESC LIMIT ?",
                (category, limit)
            ).fetchall()
            return [dict(r) for r in rows]

    def get_by_project(self, project: str, limit: int = 50) -> list:
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT key, value, category, importance FROM memory_entries WHERE project=? ORDER BY importance DESC LIMIT ?",
                (project, limit)
            ).fetchall()
            return [dict(r) for r in rows]

    def forget(self, key: str):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("DELETE FROM memory_entries WHERE key=?", (key,))
            conn.commit()

    def stats(self) -> dict:
        with sqlite3.connect(self.db_path) as conn:
            total = conn.execute("SELECT COUNT(*) FROM memory_entries").fetchone()[0]
            categories = conn.execute(
                "SELECT category, COUNT(*) as cnt FROM memory_entries GROUP BY category"
            ).fetchall()
            return {"total_entries": total, "by_category": dict(categories)}
