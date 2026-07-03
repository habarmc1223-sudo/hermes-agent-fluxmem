import json
from pathlib import Path

from knowledge.obsidian_index import KnowledgeIndex


def _embed(text: str) -> list[float]:
    lowered = text.lower()
    return [float("финанс" in lowered), float("реклам" in lowered), 1.0]


def test_incremental_sync_hybrid_search_and_citations(tmp_path: Path):
    root = tmp_path / "wb-company"
    root.mkdir()
    note = root / "weekly.md"
    note.write_text("# Финансы\nМаржа за неделю выросла.\n## Реклама\nДРР снизился.", encoding="utf-8")
    index = KnowledgeIndex(tmp_path / "index.db", embedder=_embed)
    try:
        assert index.sync([root]) == {"updated": 1, "skipped": 0, "deleted": 0}
        assert index.sync([root]) == {"updated": 0, "skipped": 1, "deleted": 0}
        hits = index.search("какая маржа", 3)
        assert hits[0].path == "wb-company/weekly.md"
        assert hits[0].citation == "[[wb-company/weekly.md#Финансы]]"
        assert "Маржа" in hits[0].content
        trace = index.db.execute("SELECT * FROM query_traces").fetchone()
        assert trace["semantic_used"] == 1
    finally:
        index.close()


def test_deleted_notes_are_removed_and_eval_is_measurable(tmp_path: Path):
    root = tmp_path / "knowledge"
    root.mkdir()
    note = root / "decision.md"
    note.write_text("# Решение\nИспользовать parent retrieval.", encoding="utf-8")
    index = KnowledgeIndex(tmp_path / "index.db")
    try:
        index.sync([root])
        report = index.evaluate([{"query": "parent retrieval", "expected_path": "knowledge/decision.md"}])
        assert report == {
            "cases": 1,
            "recall_at_k": 1.0,
            "mean_reciprocal_rank": 1.0,
            "failures": [],
        }
        note.unlink()
        assert index.sync([root])["deleted"] == 1
        assert index.search("parent retrieval") == []
    finally:
        index.close()


def test_hidden_directories_and_symlinks_are_not_indexed(tmp_path: Path):
    root = tmp_path / "vault"
    hidden = root / ".private"
    hidden.mkdir(parents=True)
    (hidden / "secret.md").write_text("token", encoding="utf-8")
    index = KnowledgeIndex(tmp_path / "index.db")
    try:
        assert index.sync([root])["updated"] == 0
        assert index.db.execute("SELECT count(*) FROM documents").fetchone()[0] == 0
    finally:
        index.close()


def test_utf8_bom_does_not_hide_first_heading(tmp_path: Path):
    root = tmp_path / "vault"
    root.mkdir()
    (root / "note.md").write_text("\ufeff# Заголовок\nТекст", encoding="utf-8")
    index = KnowledgeIndex(tmp_path / "index.db")
    try:
        index.sync([root])
        assert index.search("Текст")[0].heading == "Заголовок"
    finally:
        index.close()


def test_enabling_embeddings_backfills_unchanged_documents(tmp_path: Path):
    root = tmp_path / "knowledge"
    root.mkdir()
    (root / "note.md").write_text("# Note\nStable text", encoding="utf-8")
    plain = KnowledgeIndex(tmp_path / "index.db")
    plain.sync([root])
    plain.close()

    embedded = KnowledgeIndex(tmp_path / "index.db", embedder=lambda _: [1.0, 0.0])
    try:
        result = embedded.sync([root])
        assert result["updated"] == 1
        assert embedded.db.execute("SELECT embedding FROM chunks").fetchone()[0] == "[1.0, 0.0]"
    finally:
        embedded.close()


def test_duplicate_content_prefers_shortest_canonical_path(tmp_path: Path):
    root = tmp_path / "wb-company"
    nested = root / "wb-company"
    nested.mkdir(parents=True)
    content = "# Реклама\nРешения по рекламным кампаниям."
    (root / "ads.md").write_text(content, encoding="utf-8")
    (nested / "ads.md").write_text(content, encoding="utf-8")
    index = KnowledgeIndex(tmp_path / "index.db")
    try:
        index.sync([root])
        paths = [row[0] for row in index.db.execute("SELECT path FROM documents")]
        assert paths == ["wb-company/ads.md"]
        assert index.search("Какие решения приняты по рекламе?")[0].path == "wb-company/ads.md"
    finally:
        index.close()


def test_repeated_mirror_directories_are_skipped(tmp_path: Path):
    root = tmp_path / "wb-company"
    mirror = root / "wb-company"
    repeated = root / "snapshots" / "snapshots"
    mirror.mkdir(parents=True)
    repeated.mkdir(parents=True)
    (root / "index.md").write_text("# Canonical", encoding="utf-8")
    (mirror / "index.md").write_text("# Mirrored but changed", encoding="utf-8")
    (repeated / "finance.md").write_text("# Repeated", encoding="utf-8")
    index = KnowledgeIndex(tmp_path / "index.db")
    try:
        index.sync([root])
        paths = [row[0] for row in index.db.execute("SELECT path FROM documents")]
        assert paths == ["wb-company/index.md"]
    finally:
        index.close()


def test_numeric_identifier_matches_path_and_results_are_path_diverse(tmp_path: Path):
    root = tmp_path / "products"
    root.mkdir()
    (root / "nm-123456789.md").write_text(
        "# Product\nSummary\n## SEO\nKeywords", encoding="utf-8"
    )
    (root / "index.md").write_text("# Product directory\nSummary", encoding="utf-8")
    index = KnowledgeIndex(tmp_path / "index.db")
    try:
        index.sync([root])
        hits = index.search("карточка товара nm 123456789", limit=5)
        assert hits[0].path == "products/nm-123456789.md"
        assert len({hit.path for hit in hits}) == len(hits)
    finally:
        index.close()
