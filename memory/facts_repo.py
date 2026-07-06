"""
Bitemporal facts store — domain knowledge layer for WB Digital Corp OS.

Two independent time axes:
  valid_time       — when the fact was true in the world
  transaction_time — when the system recorded / stopped believing it

Separate from FluxMem (agent conversational memory).
FluxMem: PREFERENCE/PROCEDURE/TASK about the agent<>user interaction.
facts_repo: competitor prices, SKU metrics, WB tariffs, SEO positions.

Usage:
    repo = MemoryRepo(scope={'wb'})
    repo.assert_fact('competitor:ЦПВС', 'price', 1299, '2026-06-17')
    rows = repo.read_current('competitor:ЦПВС')
"""
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

DB_PATH = Path.home() / ".hermes" / "state" / "facts.db"

NOW_SQL = "strftime('%Y-%m-%dT%H:%M:%fZ','now')"

FACTS_DDL = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS facts (
    version_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    fact_id        TEXT    NOT NULL,
    scope          TEXT    NOT NULL,
    subject        TEXT    NOT NULL,
    predicate      TEXT    NOT NULL,
    object         TEXT,
    object_type    TEXT    NOT NULL DEFAULT 'string',

    valid_from     TEXT    NOT NULL,
    valid_to       TEXT,

    recorded_at    TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    superseded_at  TEXT,

    status         TEXT    NOT NULL DEFAULT 'active',
    source_kind    TEXT    NOT NULL,
    source_ref     TEXT,
    confidence     REAL    NOT NULL DEFAULT 1.0,
    locale         TEXT    DEFAULT 'ru-RU'
);

CREATE INDEX IF NOT EXISTS idx_facts_current
    ON facts(scope, subject, predicate) WHERE superseded_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_facts_fact_id   ON facts(fact_id);
CREATE INDEX IF NOT EXISTS idx_facts_valid      ON facts(valid_from, valid_to);
CREATE INDEX IF NOT EXISTS idx_facts_subject    ON facts(subject);

CREATE VIEW IF NOT EXISTS facts_current AS
SELECT * FROM facts
WHERE superseded_at IS NULL
  AND status = 'active'
  AND valid_from <= strftime('%Y-%m-%dT%H:%M:%fZ','now')
  AND (valid_to IS NULL OR valid_to > strftime('%Y-%m-%dT%H:%M:%fZ','now'));

-- Г1: decision log
CREATE TABLE IF NOT EXISTS decisions (
    decision_id  TEXT PRIMARY KEY,
    scope        TEXT NOT NULL,
    kind         TEXT NOT NULL,
    summary      TEXT,
    made_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    agent        TEXT
);

CREATE TABLE IF NOT EXISTS decision_inputs (
    decision_id  TEXT NOT NULL REFERENCES decisions(decision_id),
    version_id   INTEGER NOT NULL REFERENCES facts(version_id),
    PRIMARY KEY (decision_id, version_id)
);

-- Extraction cache (Г2 prep)
CREATE TABLE IF NOT EXISTS extraction_cache (
    content_hash TEXT PRIMARY KEY,
    result_json  TEXT NOT NULL,
    created_at   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
"""

# FTS built after initial DDL (separate step to allow ALTER TABLE order)
FTS_DDL = """
CREATE VIRTUAL TABLE IF NOT EXISTS facts_fts USING fts5(
    object, predicate, subject,
    content=facts, content_rowid=version_id,
    tokenize='unicode61'
);
"""


# ─── DB init ────────────────────────────────────────────────────────────────

def _connect(db_path: Path = DB_PATH) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(db_path), timeout=10)
    con.row_factory = sqlite3.Row
    con.executescript(FACTS_DDL)
    try:
        con.executescript(FTS_DDL)
    except sqlite3.OperationalError:
        pass
    return con


# ─── Core operations ────────────────────────────────────────────────────────

def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def assert_fact(
    cur: sqlite3.Cursor,
    scope: str,
    subject: str,
    predicate: str,
    obj: Any,
    valid_from: str,
    source_kind: str,
    source_ref: Optional[str] = None,
    otype: str = "string",
    confidence: float = 1.0,
    locale: str = "ru-RU",
    valid_to: Optional[str] = None,
) -> int:
    """Insert a new version of a fact; supersede the previous one.

    fact_id is preserved from the existing current version so the
    version chain stays linked (fixed bug: old code generated new uuid each time).

    Returns the new version_id.
    """
    now = _now_iso()

    # Preserve fact_id from existing version
    row = cur.execute(
        "SELECT fact_id FROM facts WHERE scope=? AND subject=? AND predicate=? AND superseded_at IS NULL",
        (scope, subject, predicate),
    ).fetchone()
    fact_id = row["fact_id"] if row else uuid.uuid4().hex

    # Supersede current version
    cur.execute(
        "UPDATE facts SET superseded_at=? WHERE scope=? AND subject=? AND predicate=? AND superseded_at IS NULL",
        (now, scope, subject, predicate),
    )

    cur.execute(
        """INSERT INTO facts
           (fact_id, scope, subject, predicate, object, object_type,
            valid_from, valid_to, recorded_at, source_kind, source_ref, confidence, locale)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (fact_id, scope, subject, predicate, str(obj) if obj is not None else None,
         otype, valid_from, valid_to, now, source_kind, source_ref, confidence, locale),
    )
    return cur.lastrowid or 0


def retract_fact(
    cur: sqlite3.Cursor,
    scope: str,
    subject: str,
    predicate: str,
    reason: str = "ended",  # 'ended' = world changed | 'wrong' = was incorrect
) -> None:
    """Mark a fact as no longer current.

    reason='ended': closes valid_to (fact was true but is now over).
    reason='wrong': only supersedes, preserves valid_to for audit
                    (fact was always incorrect — don't backdate world-truth).
    """
    now = _now_iso()
    if reason == "ended":
        cur.execute(
            "UPDATE facts SET superseded_at=?, status='retracted', valid_to=? "
            "WHERE scope=? AND subject=? AND predicate=? AND superseded_at IS NULL",
            (now, now, scope, subject, predicate),
        )
    else:  # 'wrong'
        cur.execute(
            "UPDATE facts SET superseded_at=?, status='retracted' "
            "WHERE scope=? AND subject=? AND predicate=? AND superseded_at IS NULL",
            (now, scope, subject, predicate),
        )


def forget_subject(cur: sqlite3.Cursor, scope: str, subject: str) -> None:
    """Privacy erasure (152-ФЗ). Nullifies content, keeps tombstones."""
    now = _now_iso()
    cur.execute(
        "UPDATE facts SET object=NULL, status='forgotten', "
        "superseded_at=COALESCE(superseded_at,?) "
        "WHERE scope=? AND subject=?",
        (now, scope, subject),
    )


# ─── Time-travel queries ─────────────────────────────────────────────────────

def as_of_valid(
    cur: sqlite3.Cursor,
    scope: str,
    subject: str,
    predicate: str,
    at: str,
) -> Optional[sqlite3.Row]:
    """What was TRUE in the world at time `at` (valid-time travel)."""
    return cur.execute(
        "SELECT * FROM facts WHERE scope=? AND subject=? AND predicate=? "
        "AND superseded_at IS NULL "
        "AND valid_from <= ? AND (valid_to IS NULL OR valid_to > ?)",
        (scope, subject, predicate, at, at),
    ).fetchone()


def as_of_known(
    cur: sqlite3.Cursor,
    scope: str,
    subject: str,
    predicate: str,
    at: str,
) -> Optional[sqlite3.Row]:
    """What did the system BELIEVE at time `at` (transaction-time travel)."""
    return cur.execute(
        "SELECT * FROM facts WHERE scope=? AND subject=? AND predicate=? "
        "AND recorded_at <= ? AND (superseded_at IS NULL OR superseded_at > ?)",
        (scope, subject, predicate, at, at),
    ).fetchone()


# ─── MemoryRepo ──────────────────────────────────────────────────────────────

class MemoryRepo:
    """Scope-isolated gateway to the facts store.

    Workers receive a MemoryRepo with their allowed scopes.
    Scope is enforced at the SQL level — even on writes.

    allowed_scopes examples:
        {'wb'}        → competitor prices, SKU data, tariffs
        {'personal'}  → routed to local LM Studio, not DeepSeek
        {'dev'}       → code / architecture decisions
    """

    SCOPE_TO_MODEL = {
        frozenset({"personal"}): "local_lm_studio",
    }

    def __init__(self, allowed_scopes: set[str], db_path: Path = DB_PATH):
        self.allowed = frozenset(allowed_scopes)
        self._con = _connect(db_path)

    def _check_scope(self, scope: str) -> None:
        if scope not in self.allowed:
            raise PermissionError(f"scope '{scope}' not in allowed {set(self.allowed)}")

    # ── Write ────────────────────────────────────────────────────────────

    def assert_fact(
        self,
        subject: str,
        predicate: str,
        obj: Any,
        valid_from: str,
        source_kind: str,
        scope: Optional[str] = None,
        **kwargs,
    ) -> int:
        scope = scope or next(iter(self.allowed))
        self._check_scope(scope)
        with self._con:
            cur = self._con.cursor()
            vid = assert_fact(cur, scope, subject, predicate, obj, valid_from, source_kind, **kwargs)
        return vid

    def retract(self, subject: str, predicate: str, reason: str = "ended", scope: Optional[str] = None) -> None:
        scope = scope or next(iter(self.allowed))
        self._check_scope(scope)
        with self._con:
            retract_fact(self._con.cursor(), scope, subject, predicate, reason)

    def forget(self, subject: str, scope: Optional[str] = None) -> None:
        scope = scope or next(iter(self.allowed))
        self._check_scope(scope)
        with self._con:
            forget_subject(self._con.cursor(), scope, subject)

    # ── Read ─────────────────────────────────────────────────────────────

    def read_current(self, subject: str, predicate: Optional[str] = None) -> list[dict]:
        ph = ",".join("?" * len(self.allowed))
        q = (
            f"SELECT scope,subject,predicate,object,valid_from,source_ref,confidence "
            f"FROM facts_current WHERE subject=? AND scope IN ({ph})"
        )
        args: list = [subject, *self.allowed]
        if predicate:
            q += " AND predicate=?"
            args.append(predicate)
        return [dict(r) for r in self._con.execute(q, args).fetchall()]

    def search(self, query: str, limit: int = 10) -> list[dict]:
        """FTS search across current facts within allowed scopes."""
        ph = ",".join("?" * len(self.allowed))
        try:
            rows = self._con.execute(
                f"SELECT f.* FROM facts f "
                f"JOIN facts_fts ON facts_fts.rowid = f.version_id "
                f"WHERE facts_fts MATCH ? AND f.scope IN ({ph}) AND f.superseded_at IS NULL "
                f"LIMIT ?",
                (query, *self.allowed, limit),
            ).fetchall()
            return [dict(r) for r in rows]
        except sqlite3.OperationalError:
            return []

    def history(self, subject: str, predicate: str, scope: Optional[str] = None) -> list[dict]:
        scope = scope or next(iter(self.allowed))
        self._check_scope(scope)
        rows = self._con.execute(
            "SELECT version_id, fact_id, object, valid_from, valid_to, recorded_at, superseded_at, status, source_ref "
            "FROM facts WHERE scope=? AND subject=? AND predicate=? ORDER BY recorded_at DESC",
            (scope, subject, predicate),
        ).fetchall()
        return [dict(r) for r in rows]

    # ── Г1: Decision log ─────────────────────────────────────────────────

    def log_decision(
        self,
        kind: str,
        summary: str,
        version_ids: list[int],
        agent: Optional[str] = None,
        scope: Optional[str] = None,
    ) -> str:
        scope = scope or next(iter(self.allowed))
        self._check_scope(scope)
        decision_id = uuid.uuid4().hex
        with self._con:
            self._con.execute(
                "INSERT INTO decisions (decision_id, scope, kind, summary, agent) VALUES (?,?,?,?,?)",
                (decision_id, scope, kind, summary, agent),
            )
            self._con.executemany(
                "INSERT OR IGNORE INTO decision_inputs (decision_id, version_id) VALUES (?,?)",
                [(decision_id, vid) for vid in version_ids],
            )
        return decision_id

    def trace_decision(self, decision_id: str) -> dict:
        d = self._con.execute(
            "SELECT * FROM decisions WHERE decision_id=?", (decision_id,)
        ).fetchone()
        if not d:
            return {}
        inputs = self._con.execute(
            "SELECT f.subject, f.predicate, f.object, f.source_ref, f.valid_from "
            "FROM decision_inputs di JOIN facts f ON f.version_id=di.version_id "
            "WHERE di.decision_id=?",
            (decision_id,),
        ).fetchall()
        return {
            "decision": dict(d),
            "inputs": [dict(r) for r in inputs],
        }

    def route_model(self) -> str:
        return self.SCOPE_TO_MODEL.get(self.allowed, "deepseek")

    def close(self) -> None:
        self._con.close()


# ─── Retention (call from weekly Hermes cron) ────────────────────────────────

def archive_old_facts(db_path: Path = DB_PATH, older_than_days: int = 730) -> int:
    """Move superseded facts older than N days to facts_archive. Returns row count."""
    con = _connect(db_path)
    con.execute("""
        CREATE TABLE IF NOT EXISTS facts_archive AS SELECT * FROM facts WHERE 0
    """)
    cur = con.execute(
        "INSERT INTO facts_archive SELECT * FROM facts "
        "WHERE superseded_at IS NOT NULL "
        "AND superseded_at < datetime('now', ? || ' days')",
        (f"-{older_than_days}",),
    )
    archived = cur.rowcount
    if archived:
        con.execute(
            "DELETE FROM facts WHERE version_id IN (SELECT version_id FROM facts_archive)"
        )
        con.commit()
    con.close()
    return archived
