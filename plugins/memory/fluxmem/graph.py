"""
FluxMem — асинхронная графовая память на SQLite.

Таблицы:
  memory_nodes     — узлы графа (ENTITY, FACT, TASK, PROCEDURE, CONTEXT, PREFERENCE, OUTCOME, PATTERN)
  memory_edges     — рёбра между узлами (RELATES_TO, ENABLES, PRODUCES, CONFLICTS_WITH, ABSTRACTS, REFINED_BY)
  procedural_index — зрелые процедуры, готовые к автозапуску

Затухание (decay): узлы неподтверждённые (strength < 0.5) со временем слабеют.
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# ── Константы ────────────────────────────────────────────────────────────────

NODE_TYPES = ("ENTITY", "FACT", "TASK", "PROCEDURE", "CONTEXT", "PREFERENCE", "OUTCOME", "PATTERN")
EDGE_TYPES = ("RELATES_TO", "ENABLES", "PRODUCES", "CONFLICTS_WITH", "ABSTRACTS", "REFINED_BY")

# Пороги
DECAY_THRESHOLD_INTERACTIONS = 30
DECAY_FACTOR = 0.95
PRUNE_THRESHOLD = 0.05
CONSOLIDATION_THRESHOLD = 0.85
NEW_NODE_STRENGTH = 0.3
NEW_EDGE_STRENGTH = 0.3
FEEDBACK_STRENGTHEN = 0.15
FEEDBACK_WEAKEN = -0.20

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS memory_nodes (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    type        TEXT    NOT NULL CHECK(type IN ('ENTITY','FACT','TASK','PROCEDURE','CONTEXT','PREFERENCE','OUTCOME','PATTERN')),
    label       TEXT    NOT NULL,
    strength    REAL   NOT NULL DEFAULT 0.3,
    metadata    TEXT   NOT NULL DEFAULT '{}',
    user_id     TEXT   NOT NULL DEFAULT 'default',
    session_key TEXT   DEFAULT NULL,
    created_at  TEXT   NOT NULL DEFAULT (datetime('now')),
    last_accessed TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS memory_edges (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id   INTEGER NOT NULL REFERENCES memory_nodes(id) ON DELETE CASCADE,
    target_id   INTEGER NOT NULL REFERENCES memory_nodes(id) ON DELETE CASCADE,
    type        TEXT    NOT NULL CHECK(type IN ('RELATES_TO','ENABLES','PRODUCES','CONFLICTS_WITH','ABSTRACTS','REFINED_BY')),
    strength    REAL   NOT NULL DEFAULT 0.3,
    created_at  TEXT   NOT NULL DEFAULT (datetime('now')),
    UNIQUE(source_id, target_id, type)
);

CREATE TABLE IF NOT EXISTS procedural_index (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    codename          TEXT   NOT NULL,
    trigger_conditions TEXT  NOT NULL DEFAULT '{}',
    core_steps        TEXT  NOT NULL DEFAULT '[]',
    strength          REAL  NOT NULL DEFAULT 0.85,
    status            TEXT  NOT NULL DEFAULT 'DEVELOPING' CHECK(status IN ('DEVELOPING','MATURE','DEPRECATED')),
    user_id           TEXT  NOT NULL DEFAULT 'default',
    created_at        TEXT  NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_nodes_user ON memory_nodes(user_id);
CREATE INDEX IF NOT EXISTS idx_nodes_type ON memory_nodes(type);
CREATE INDEX IF NOT EXISTS idx_edges_source ON memory_edges(source_id);
CREATE INDEX IF NOT EXISTS idx_edges_target ON memory_edges(target_id);
CREATE INDEX IF NOT EXISTS idx_proc_user ON procedural_index(user_id);
"""


class MemoryGraph:
    """CRUD-доступ к графу памяти на SQLite."""

    def __init__(self, db_path: str):
        self._db_path = db_path
        self._local = threading.local()

    # ── Connection management ──────────────────────────────────────────────

    def _get_conn(self) -> sqlite3.Connection:
        """Thread-local connection (Hermes может вызывать из разных потоков)."""
        if not hasattr(self._local, "conn") or self._local.conn is None:
            conn = sqlite3.connect(self._db_path)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA foreign_keys=ON")
            self._local.conn = conn
        return self._local.conn

    def init_schema(self) -> None:
        """Создать таблицы если не существует."""
        conn = self._get_conn()
        conn.executescript(SCHEMA_SQL)
        conn.commit()

    # ── Nodes ───────────────────────────────────────────────────────────────

    def create_node(
        self,
        node_type: str,
        label: str,
        user_id: str = "default",
        strength: float = NEW_NODE_STRENGTH,
        metadata: Optional[Dict] = None,
        session_key: Optional[str] = None,
    ) -> int:
        if node_type not in NODE_TYPES:
            raise ValueError(f"Invalid node type: {node_type}")
        conn = self._get_conn()
        cur = conn.execute(
            "INSERT INTO memory_nodes (type, label, strength, metadata, user_id, session_key)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (node_type, label, strength, json.dumps(metadata or {}), user_id, session_key),
        )
        conn.commit()
        return cur.lastrowid

    def get_node(self, node_id: int) -> Optional[Dict]:
        conn = self._get_conn()
        conn.execute(
            "UPDATE memory_nodes SET last_accessed=datetime('now') WHERE id=?",
            (node_id,),
        )
        conn.commit()
        cur = conn.execute("SELECT * FROM memory_nodes WHERE id=?", (node_id,))
        row = cur.fetchone()
        return dict(row) if row else None

    def find_node_by_label(
        self, label: str, user_id: str = "default",
        node_type: Optional[str] = None,
    ) -> Optional[Dict]:
        conn = self._get_conn()
        if node_type:
            cur = conn.execute(
                "SELECT * FROM memory_nodes WHERE label=? AND user_id=? AND type=?",
                (label, user_id, node_type),
            )
        else:
            cur = conn.execute(
                "SELECT * FROM memory_nodes WHERE label=? AND user_id=?",
                (label, user_id),
            )
        row = cur.fetchone()
        return dict(row) if row else None

    def search_nodes(
        self, query: str, user_id: str = "default",
        node_type: Optional[str] = None, limit: int = 10,
    ) -> List[Dict]:
        conn = self._get_conn()
        if node_type:
            cur = conn.execute(
                "SELECT * FROM memory_nodes WHERE user_id=? AND type=? AND label LIKE ?"
                " ORDER BY strength DESC LIMIT ?",
                (user_id, node_type, f"%{query}%", limit),
            )
        else:
            cur = conn.execute(
                "SELECT * FROM memory_nodes WHERE user_id=? AND label LIKE ?"
                " ORDER BY strength DESC LIMIT ?",
                (user_id, f"%{query}%", limit),
            )
        return [dict(r) for r in cur.fetchall()]

    def get_user_nodes(
        self, user_id: str = "default",
        node_type: Optional[str] = None, limit: int = 50,
    ) -> List[Dict]:
        conn = self._get_conn()
        if node_type:
            cur = conn.execute(
                "SELECT * FROM memory_nodes WHERE user_id=? AND type=?"
                " ORDER BY strength DESC LIMIT ?",
                (user_id, node_type, limit),
            )
        else:
            cur = conn.execute(
                "SELECT * FROM memory_nodes WHERE user_id=?"
                " ORDER BY strength DESC LIMIT ?",
                (user_id, limit),
            )
        return [dict(r) for r in cur.fetchall()]

    def update_node_strength(self, node_id: int, delta: float) -> None:
        conn = self._get_conn()
        conn.execute(
            "UPDATE memory_nodes SET strength = MIN(1.0, MAX(0.0, strength + ?)) WHERE id=?",
            (delta, node_id),
        )
        conn.commit()

    def set_node_strength(self, node_id: int, strength: float) -> None:
        conn = self._get_conn()
        conn.execute(
            "UPDATE memory_nodes SET strength=? WHERE id=?",
            (min(1.0, max(0.0, strength)), node_id),
        )
        conn.commit()

    # ── Edges ───────────────────────────────────────────────────────────────

    def upsert_edge(
        self, source_id: int, target_id: int,
        edge_type: str, strength: float = NEW_EDGE_STRENGTH,
    ) -> int:
        if edge_type not in EDGE_TYPES:
            raise ValueError(f"Invalid edge type: {edge_type}")
        if source_id == target_id:
            return 0
        conn = self._get_conn()
        # Normalize direction for undirected edges
        if edge_type in ("RELATES_TO", "CONFLICTS_WITH"):
            src, tgt = sorted([source_id, target_id])
        else:
            src, tgt = source_id, target_id
        cur = conn.execute(
            "INSERT INTO memory_edges (source_id, target_id, type, strength)"
            " VALUES (?, ?, ?, ?)"
            " ON CONFLICT(source_id, target_id, type)"
            " DO UPDATE SET strength = MIN(1.0, strength + ?)",
            (src, tgt, edge_type, strength, strength),
        )
        conn.commit()
        return cur.lastrowid or 0

    def get_edges(self, node_id: int, limit: int = 20) -> List[Dict]:
        conn = self._get_conn()
        cur = conn.execute(
            "SELECT * FROM memory_edges WHERE source_id=? OR target_id=?"
            " ORDER BY strength DESC LIMIT ?",
            (node_id, node_id, limit),
        )
        return [dict(r) for r in cur.fetchall()]

    def adjust_edge_strength(self, edge_id: int, delta: float) -> None:
        conn = self._get_conn()
        conn.execute(
            "UPDATE memory_edges SET strength = MIN(1.0, MAX(0.0, strength + ?)) WHERE id=?",
            (delta, edge_id),
        )
        conn.commit()

    def prune_edges(self, threshold: float = PRUNE_THRESHOLD) -> int:
        conn = self._get_conn()
        cur = conn.execute(
            "DELETE FROM memory_edges WHERE strength < ?",
            (threshold,),
        )
        conn.commit()
        return cur.rowcount

    # ── Graph traversal ─────────────────────────────────────────────────────

    def traverse(self, start_labels: List[str], user_id: str = "default",
                 max_depth: int = 3, max_nodes: int = 10) -> List[Dict]:
        """
        BFS traversal от узлов, чей label совпадает с start_labels.
        Возвращает узлы + рёбра в порядке релевантности.
        """
        conn = self._get_conn()
        visited_node_ids = set()
        result_nodes = []

        # Найти стартовые узлы по label
        for label in start_labels:
            cur = conn.execute(
                "SELECT * FROM memory_nodes WHERE user_id=? AND label LIKE ?"
                " ORDER BY strength DESC LIMIT 5",
                (user_id, f"%{label}%"),
            )
            for row in cur.fetchall():
                if row["id"] not in visited_node_ids:
                    visited_node_ids.add(row["id"])
                    result_nodes.append(dict(row))

        # BFS на depth=1 (соседи)
        frontier = list(visited_node_ids)
        for _ in range(max_depth):
            if not frontier or len(result_nodes) >= max_nodes:
                break
            next_frontier = []
            for nid in frontier:
                cur = conn.execute(
                    "SELECT n.* FROM memory_nodes n"
                    " JOIN memory_edges e ON (e.source_id=n.id OR e.target_id=n.id)"
                    " WHERE (e.source_id=? OR e.target_id=?) AND n.id != ?"
                    " AND n.strength >= ?"
                    " ORDER BY e.strength DESC LIMIT 3",
                    (nid, nid, nid, PRUNE_THRESHOLD),
                )
                for row in cur.fetchall():
                    if row["id"] not in visited_node_ids:
                        visited_node_ids.add(row["id"])
                        result_nodes.append(dict(row))
                        next_frontier.append(row["id"])
                if len(result_nodes) >= max_nodes:
                    break
            frontier = next_frontier

        # Сортировка по strength
        result_nodes.sort(key=lambda n: n["strength"], reverse=True)
        return result_nodes[:max_nodes]

    # ── Decay ───────────────────────────────────────────────────────────────

    def apply_decay(self, user_id: str = "default") -> int:
        """Ослабить неподтверждённые узлы (strength < 0.5) на DECAY_FACTOR."""
        conn = self._get_conn()
        cur = conn.execute(
            "UPDATE memory_nodes SET strength = ROUND(strength * ?, 4)"
            " WHERE user_id=? AND strength < 0.5 AND strength > 0.05",
            (DECAY_FACTOR, user_id),
        )
        conn.commit()
        return cur.rowcount

    # ── Stats ───────────────────────────────────────────────────────────────

    def get_stats(self, user_id: str = "default") -> Dict:
        conn = self._get_conn()
        cur = conn.execute(
            "SELECT COUNT(*) as cnt FROM memory_nodes WHERE user_id=?", (user_id,)
        )
        total_nodes = cur.fetchone()["cnt"]

        cur = conn.execute(
            "SELECT COUNT(*) as cnt FROM memory_edges"
            " WHERE source_id IN (SELECT id FROM memory_nodes WHERE user_id=?)"
            " OR target_id IN (SELECT id FROM memory_nodes WHERE user_id=?)",
            (user_id, user_id),
        )
        total_edges = cur.fetchone()["cnt"]

        cur = conn.execute(
            "SELECT COUNT(*) as cnt FROM procedural_index WHERE user_id=? AND status='MATURE'",
            (user_id,),
        )
        mature = cur.fetchone()["cnt"]

        # By type
        cur = conn.execute(
            "SELECT type, COUNT(*) as cnt FROM memory_nodes WHERE user_id=? GROUP BY type",
            (user_id,),
        )
        by_type = {r["type"]: r["cnt"] for r in cur.fetchall()}

        return {
            "total_nodes": total_nodes,
            "total_edges": total_edges,
            "mature_procedures": mature,
            "by_type": by_type,
        }

    # ── Procedures ──────────────────────────────────────────────────────────

    def create_procedure(
        self, codename: str, trigger: Dict, steps: List[str],
        user_id: str = "default",
    ) -> int:
        conn = self._get_conn()
        cur = conn.execute(
            "INSERT INTO procedural_index (codename, trigger_conditions, core_steps, strength, user_id)"
            " VALUES (?, ?, ?, ?, ?)",
            (codename, json.dumps(trigger), json.dumps(steps), CONSOLIDATION_THRESHOLD, user_id),
        )
        conn.commit()
        return cur.lastrowid

    def list_procedures(self, user_id: str = "default") -> List[Dict]:
        conn = self._get_conn()
        cur = conn.execute(
            "SELECT * FROM procedural_index WHERE user_id=? ORDER BY strength DESC",
            (user_id,),
        )
        return [dict(r) for r in cur.fetchall()]

    def find_matching_procedure(self, trigger: Dict, user_id: str = "default") -> Optional[Dict]:
        conn = self._get_conn()
        cur = conn.execute(
            "SELECT * FROM procedural_index WHERE user_id=? AND status='MATURE'"
            " ORDER BY strength DESC LIMIT 20",
            (user_id,),
        )
        for row in cur.fetchall():
            stored_trigger = json.loads(row["trigger_conditions"])
            # Простое совпадение: все ключи trigger есть в stored_trigger c теми же значениями
            if all(stored_trigger.get(k) == v for k, v in trigger.items()):
                return dict(row)
        return None

    def set_procedure_status(self, proc_id: int, status: str) -> None:
        conn = self._get_conn()
        conn.execute(
            "UPDATE procedural_index SET status=? WHERE id=?",
            (status, proc_id),
        )
        conn.commit()

    def update_procedure_strength(self, proc_id: int, delta: float) -> None:
        conn = self._get_conn()
        conn.execute(
            "UPDATE procedural_index SET strength = MIN(1.0, MAX(0.0, strength + ?)) WHERE id=?",
            (delta, proc_id),
        )
        conn.commit()

    # ── Export ──────────────────────────────────────────────────────────────

    def export_json(self, user_id: str = "default") -> Dict:
        nodes = self.get_user_nodes(user_id, limit=500)
        edges = []
        conn = self._get_conn()
        for n in nodes:
            cur = conn.execute(
                "SELECT * FROM memory_edges WHERE source_id=? OR target_id=?", (n["id"], n["id"])
            )
            for r in cur.fetchall():
                edges.append(dict(r))
        procs = self.list_procedures(user_id)
        return {
            "nodes": nodes,
            "edges": edges,
            "procedural_index": procs,
            "meta": self.get_stats(user_id),
        }
