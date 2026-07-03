"""Incremental Markdown index with hybrid retrieval and source citations.

The index is deliberately separate from conversational memory.  It only reads
operator-configured roots, stores derived chunks in a profile-scoped SQLite
database, and treats indexed text as untrusted data.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, Sequence

from hermes_constants import get_hermes_home

Embedder = Callable[[str], Sequence[float]]
_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*$", re.MULTILINE)
_SECRET_NAME = re.compile(r"(?:^|/)(?:\.env(?:\..*)?|auth\.json|credentials?\.json)$", re.I)
_STOP_WORDS = {
    "а", "без", "в", "где", "для", "и", "из", "или", "как", "какие", "какой",
    "когда", "на", "о", "об", "от", "по", "при", "с", "со", "что", "это",
    "the", "a", "an", "and", "for", "from", "in", "of", "on", "or", "to", "what",
}


@dataclass(frozen=True)
class SearchHit:
    path: str
    heading: str
    content: str
    score: float
    citation: str


def _hash(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _vector_json(vector: Sequence[float] | None) -> str | None:
    return json.dumps([float(v) for v in vector]) if vector is not None else None


def _cosine(left: Sequence[float], right: Sequence[float]) -> float:
    if len(left) != len(right) or not left:
        return 0.0
    dot = sum(a * b for a, b in zip(left, right))
    norms = math.sqrt(sum(a * a for a in left)) * math.sqrt(sum(b * b for b in right))
    return dot / norms if norms else 0.0


def _sections(text: str) -> list[tuple[str, str]]:
    """Split a note by headings; each returned chunk is a parent section."""
    matches = list(_HEADING.finditer(text))
    if not matches:
        stripped = text.strip()
        return [("Document", stripped)] if stripped else []
    sections: list[tuple[str, str]] = []
    preface = text[: matches[0].start()].strip()
    if preface:
        sections.append(("Document", preface))
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        body = text[match.end() : end].strip()
        content = f"{match.group(0)}\n{body}".strip()
        if content:
            sections.append((match.group(2).strip(), content))
    return sections


class KnowledgeIndex:
    def __init__(self, db_path: Path | None = None, embedder: Embedder | None = None):
        self.db_path = db_path or get_hermes_home() / "knowledge" / "index.sqlite3"
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.embedder = embedder
        self.db = sqlite3.connect(self.db_path)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys=ON")
        self._init_schema()

    def close(self) -> None:
        self.db.close()

    def _init_schema(self) -> None:
        self.db.executescript(
            """
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS documents (
              path TEXT PRIMARY KEY, content_hash TEXT NOT NULL, modified_ns INTEGER NOT NULL,
              indexed_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS chunks (
              id INTEGER PRIMARY KEY, path TEXT NOT NULL REFERENCES documents(path) ON DELETE CASCADE,
              ordinal INTEGER NOT NULL, heading TEXT NOT NULL, content TEXT NOT NULL,
              embedding TEXT, UNIQUE(path, ordinal)
            );
            CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
              heading, content, content='chunks', content_rowid='id', tokenize='unicode61'
            );
            CREATE TRIGGER IF NOT EXISTS chunks_ai AFTER INSERT ON chunks BEGIN
              INSERT INTO chunks_fts(rowid, heading, content) VALUES (new.id, new.heading, new.content);
            END;
            CREATE TRIGGER IF NOT EXISTS chunks_ad AFTER DELETE ON chunks BEGIN
              INSERT INTO chunks_fts(chunks_fts, rowid, heading, content)
              VALUES ('delete', old.id, old.heading, old.content);
            END;
            CREATE TABLE IF NOT EXISTS query_traces (
              id INTEGER PRIMARY KEY, query_hash TEXT NOT NULL, duration_ms INTEGER NOT NULL,
              result_count INTEGER NOT NULL, lexical_used INTEGER NOT NULL,
              semantic_used INTEGER NOT NULL, created_at REAL NOT NULL
            );
            """
        )
        self.db.commit()

    @staticmethod
    def _safe_files(root: Path) -> Iterable[Path]:
        resolved_root = root.resolve(strict=True)
        for path in resolved_root.rglob("*.md"):
            if path.is_symlink() or _SECRET_NAME.search(path.as_posix()):
                continue
            resolved = path.resolve(strict=True)
            if resolved_root not in resolved.parents:
                continue
            if any(part.startswith(".") for part in resolved.relative_to(resolved_root).parts):
                continue
            yield resolved

    def sync(self, roots: Iterable[Path]) -> dict[str, int]:
        allowed_roots = [Path(root).resolve(strict=True) for root in roots]
        seen: set[str] = set()
        seen_hashes: set[str] = set()
        updated = skipped = 0
        for root in allowed_roots:
            paths = sorted(
                self._safe_files(root),
                key=lambda item: (len(item.relative_to(root).parts), item.as_posix()),
            )
            for path in paths:
                source_relative = path.relative_to(root)
                parts = source_relative.parts
                if (parts and parts[0] == root.name) or any(
                    parts[index] == parts[index - 1] for index in range(1, len(parts))
                ):
                    continue
                relative = f"{root.name}/{source_relative.as_posix()}"
                raw = path.read_bytes()
                digest = _hash(raw)
                if digest in seen_hashes:
                    continue
                seen_hashes.add(digest)
                seen.add(relative)
                stat = path.stat()
                existing = self.db.execute(
                    """SELECT d.content_hash,
                              NOT EXISTS(SELECT 1 FROM chunks c WHERE c.path=d.path AND c.embedding IS NULL)
                              AS embeddings_ready
                       FROM documents d WHERE d.path = ?""",
                    (relative,),
                ).fetchone()
                if (
                    existing
                    and existing["content_hash"] == digest
                    and (self.embedder is None or existing["embeddings_ready"])
                ):
                    skipped += 1
                    continue
                text = raw.decode("utf-8", errors="replace").lstrip("\ufeff")
                chunks = _sections(text)
                with self.db:
                    self.db.execute("DELETE FROM documents WHERE path = ?", (relative,))
                    self.db.execute(
                        "INSERT INTO documents(path, content_hash, modified_ns, indexed_at) VALUES(?,?,?,?)",
                        (relative, digest, stat.st_mtime_ns, time.time()),
                    )
                    for ordinal, (heading, content) in enumerate(chunks):
                        embedding = self.embedder(content) if self.embedder else None
                        self.db.execute(
                            "INSERT INTO chunks(path, ordinal, heading, content, embedding) VALUES(?,?,?,?,?)",
                            (relative, ordinal, heading, content, _vector_json(embedding)),
                        )
                updated += 1

        prefixes = tuple(f"{root.name}/" for root in allowed_roots)
        deleted = 0
        for row in self.db.execute("SELECT path FROM documents").fetchall():
            stored = row["path"]
            if stored.startswith(prefixes) and stored not in seen:
                with self.db:
                    self.db.execute("DELETE FROM documents WHERE path = ?", (stored,))
                deleted += 1
        return {"updated": updated, "skipped": skipped, "deleted": deleted}

    def search(self, query: str, limit: int = 5) -> list[SearchHit]:
        started = time.perf_counter()
        query = query.strip()
        if not query:
            return []
        limit = max(1, min(int(limit), 20))
        candidates: dict[int, tuple[sqlite3.Row, float]] = {}
        lexical_used = False
        tokens = [
            token for token in re.findall(r"[\w-]+", query.lower(), flags=re.UNICODE)
            if len(token) > 1 and token not in _STOP_WORDS
        ]
        if tokens:
            terms: list[str] = []
            for token in tokens[:12]:
                clean = token.replace(chr(34), "")
                terms.append(f'"{clean}"')
                if len(clean) > 5 and re.fullmatch(r"[а-яё]+", clean):
                    terms.append(f'"{clean[:-2]}"*')
            fts_query = " OR ".join(terms)
            rows = self.db.execute(
                """SELECT c.*, bm25(chunks_fts, 3.0, 1.0) AS rank
                   FROM chunks_fts JOIN chunks c ON c.id = chunks_fts.rowid
                   WHERE chunks_fts MATCH ? ORDER BY rank LIMIT ?""",
                (fts_query, limit * 4),
            ).fetchall()
            for rank, row in enumerate(rows, start=1):
                candidates[row["id"]] = (row, 1.0 / (60 + rank))
            lexical_used = bool(rows)

            identifiers = [token for token in tokens if len(token) >= 6 and token.isdigit()]
            for identifier in identifiers:
                path_rows = self.db.execute(
                    "SELECT * FROM chunks WHERE path LIKE ? ORDER BY ordinal LIMIT ?",
                    (f"%{identifier}%", limit * 2),
                ).fetchall()
                for rank, row in enumerate(path_rows, start=1):
                    previous = candidates.get(row["id"], (row, 0.0))[1]
                    candidates[row["id"]] = (row, previous + 1.0 / (10 + rank))

        semantic_used = False
        if self.embedder:
            query_vector = self.embedder(query)
            for row in self.db.execute("SELECT * FROM chunks WHERE embedding IS NOT NULL"):
                similarity = _cosine(query_vector, json.loads(row["embedding"]))
                if similarity <= 0:
                    continue
                previous = candidates.get(row["id"], (row, 0.0))[1]
                candidates[row["id"]] = (row, previous + similarity / 10.0)
            semantic_used = True

        ranked: list[tuple[sqlite3.Row, float]] = []
        ranked_paths: set[str] = set()
        for item in sorted(candidates.values(), key=lambda candidate: candidate[1], reverse=True):
            if item[0]["path"] in ranked_paths:
                continue
            ranked.append(item)
            ranked_paths.add(item[0]["path"])
            if len(ranked) == limit:
                break
        hits = [
            SearchHit(
                path=row["path"], heading=row["heading"], content=row["content"],
                score=round(score, 6), citation=f"[[{row['path']}#{row['heading']}]]",
            )
            for row, score in ranked
        ]
        duration_ms = int((time.perf_counter() - started) * 1000)
        with self.db:
            self.db.execute(
                """INSERT INTO query_traces(query_hash,duration_ms,result_count,lexical_used,semantic_used,created_at)
                   VALUES(?,?,?,?,?,?)""",
                (_hash(query.encode()), duration_ms, len(hits), lexical_used, semantic_used, time.time()),
            )
        return hits

    def evaluate(self, cases: Iterable[dict]) -> dict[str, object]:
        total = passed = 0
        reciprocal_rank = 0.0
        failures: list[dict[str, object]] = []
        for case in cases:
            total += 1
            expected = str(case["expected_path"])
            hits = self.search(str(case["query"]), limit=int(case.get("limit", 5)))
            rank = next((i for i, hit in enumerate(hits, 1) if hit.path == expected), None)
            if rank:
                passed += 1
                reciprocal_rank += 1.0 / rank
            else:
                failures.append(
                    {
                        "query": str(case["query"]),
                        "expected_path": expected,
                        "returned_paths": [hit.path for hit in hits],
                    }
                )
        return {
            "cases": total,
            "recall_at_k": passed / total if total else 0.0,
            "mean_reciprocal_rank": reciprocal_rank / total if total else 0.0,
            "failures": failures,
        }


def openai_embedder() -> Embedder | None:
    api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not api_key:
        return None
    from openai import OpenAI

    client = OpenAI(api_key=api_key)
    model = os.environ.get("HERMES_KNOWLEDGE_EMBEDDING_MODEL", "text-embedding-3-small")

    def embed(text: str) -> Sequence[float]:
        return client.embeddings.create(model=model, input=text[:24_000]).data[0].embedding

    return embed
