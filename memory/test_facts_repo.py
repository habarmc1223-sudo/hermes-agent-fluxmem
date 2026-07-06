"""pytest tests for facts_repo.py"""
import sqlite3
import tempfile
from pathlib import Path

import pytest

import sys
sys.path.insert(0, str(Path(__file__).parents[1]))
from memory.facts_repo import MemoryRepo, assert_fact, retract_fact, forget_subject, _connect


@pytest.fixture
def tmp_repo():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db = Path(f.name)
    repo = MemoryRepo(allowed_scopes={"wb"}, db_path=db)
    yield repo
    repo.close()
    db.unlink(missing_ok=True)


@pytest.fixture
def tmp_db():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db = Path(f.name)
    con = _connect(db)
    yield con, db
    con.close()
    db.unlink(missing_ok=True)


# ── Core fact operations ─────────────────────────────────────────────────────

def test_assert_creates_fact(tmp_db):
    con, _ = tmp_db
    cur = con.cursor()
    vid = assert_fact(cur, "wb", "competitor:ЦПВС", "price", 1299, "2026-06-01", "parser")
    con.commit()
    row = con.execute("SELECT * FROM facts WHERE version_id=?", (vid,)).fetchone()
    assert row["object"] == "1299"
    assert row["scope"] == "wb"
    assert row["superseded_at"] is None
    assert row["status"] == "active"


def test_assert_supersedes_previous(tmp_db):
    con, _ = tmp_db
    cur = con.cursor()
    assert_fact(cur, "wb", "competitor:ЦПВС", "price", 1299, "2026-06-01", "parser")
    con.commit()
    assert_fact(cur, "wb", "competitor:ЦПВС", "price", 1399, "2026-06-17", "parser")
    con.commit()

    all_rows = con.execute(
        "SELECT version_id, object, superseded_at FROM facts WHERE subject='competitor:ЦПВС' AND predicate='price'"
    ).fetchall()
    assert len(all_rows) == 2

    current = con.execute(
        "SELECT * FROM facts_current WHERE subject='competitor:ЦПВС' AND predicate='price'"
    ).fetchall()
    assert len(current) == 1
    assert current[0]["object"] == "1399"


def test_fact_id_preserved_across_versions(tmp_db):
    """fact_id must stay the same across supersede — version chain integrity."""
    con, _ = tmp_db
    cur = con.cursor()
    assert_fact(cur, "wb", "competitor:ЦПВС", "price", 1299, "2026-06-01", "parser")
    con.commit()
    assert_fact(cur, "wb", "competitor:ЦПВС", "price", 1399, "2026-06-17", "parser")
    con.commit()

    fact_ids = [r[0] for r in con.execute(
        "SELECT fact_id FROM facts WHERE subject='competitor:ЦПВС' AND predicate='price'"
    ).fetchall()]
    assert fact_ids[0] == fact_ids[1], "fact_id must be preserved across versions"


def test_retract_ended_closes_valid_to(tmp_db):
    con, _ = tmp_db
    cur = con.cursor()
    assert_fact(cur, "wb", "tariff:TR1", "rate", 18.5, "2026-01-01", "manual")
    con.commit()
    retract_fact(cur, "wb", "tariff:TR1", "rate", reason="ended")
    con.commit()

    row = con.execute(
        "SELECT superseded_at, valid_to, status FROM facts WHERE subject='tariff:TR1'"
    ).fetchone()
    assert row["superseded_at"] is not None
    assert row["valid_to"] is not None   # world-truth closed
    assert row["status"] == "retracted"


def test_retract_wrong_preserves_valid_to(tmp_db):
    """'wrong' retract should not close valid_to — the record was never valid."""
    con, _ = tmp_db
    cur = con.cursor()
    assert_fact(cur, "wb", "tariff:TR1", "rate", 999, "2026-01-01", "manual")
    con.commit()
    retract_fact(cur, "wb", "tariff:TR1", "rate", reason="wrong")
    con.commit()

    row = con.execute(
        "SELECT superseded_at, valid_to FROM facts WHERE subject='tariff:TR1'"
    ).fetchone()
    assert row["superseded_at"] is not None
    assert row["valid_to"] is None   # world-truth interval NOT touched


def test_retract_removes_from_current_view(tmp_db):
    con, _ = tmp_db
    cur = con.cursor()
    assert_fact(cur, "wb", "competitor:ЦПВС", "price", 1299, "2026-06-01", "parser")
    con.commit()
    retract_fact(cur, "wb", "competitor:ЦПВС", "price")
    con.commit()

    current = con.execute(
        "SELECT * FROM facts_current WHERE subject='competitor:ЦПВС'"
    ).fetchall()
    assert len(current) == 0


def test_forget_nullifies_object(tmp_db):
    con, _ = tmp_db
    cur = con.cursor()
    assert_fact(cur, "personal", "user:marina", "phone", "+7999", "2026-01-01", "manual")
    con.commit()
    forget_subject(cur, "personal", "user:marina")
    con.commit()

    row = con.execute("SELECT object, status FROM facts WHERE subject='user:marina'").fetchone()
    assert row["object"] is None
    assert row["status"] == "forgotten"


# ── Scope isolation ──────────────────────────────────────────────────────────

def test_scope_read_isolation(tmp_db):
    con, db = tmp_db
    cur = con.cursor()
    assert_fact(cur, "wb", "competitor:ЦПВС", "price", 1299, "2026-06-01", "parser")
    assert_fact(cur, "personal", "user:marina", "hobby", "yoga", "2026-01-01", "manual")
    con.commit()

    wb_repo = MemoryRepo(allowed_scopes={"wb"}, db_path=db)
    result = wb_repo.read_current("user:marina")
    assert result == [], "wb scope must not see personal facts"
    wb_repo.close()

    personal_repo = MemoryRepo(allowed_scopes={"personal"}, db_path=db)
    result = personal_repo.read_current("competitor:ЦПВС")
    assert result == [], "personal scope must not see wb facts"
    personal_repo.close()


def test_scope_write_enforcement(tmp_db):
    _, db = tmp_db
    wb_repo = MemoryRepo(allowed_scopes={"wb"}, db_path=db)
    with pytest.raises(PermissionError):
        wb_repo.assert_fact("user:marina", "phone", "+7999", "2026-01-01", "manual", scope="personal")
    wb_repo.close()


# ── Г1: Decision log ─────────────────────────────────────────────────────────

def test_decision_log_and_trace(tmp_repo):
    v1 = tmp_repo.assert_fact("competitor:ЦПВС", "price", 1299, "2026-06-01", "parser")
    v2 = tmp_repo.assert_fact("sku:WB-123", "stock", 43, "2026-06-01", "parser")

    did = tmp_repo.log_decision(
        kind="ad_bid",
        summary="Поднять ставку потому что конкурент поднял цену",
        version_ids=[v1, v2],
        agent="wbc0ads",
    )

    trace = tmp_repo.trace_decision(did)
    assert trace["decision"]["kind"] == "ad_bid"
    assert len(trace["inputs"]) == 2
    subjects = {r["subject"] for r in trace["inputs"]}
    assert "competitor:ЦПВС" in subjects
    assert "sku:WB-123" in subjects


def test_trace_decision_denies_cross_scope_access(tmp_db):
    """A repo scoped to 'wb' must not read a decision logged under 'personal',
    even knowing its decision_id (IDOR-style cross-object reference)."""
    _, db = tmp_db
    personal_repo = MemoryRepo(allowed_scopes={"personal"}, db_path=db)
    vid = personal_repo.assert_fact("user:marina", "phone", "+7999", "2026-01-01", "manual")
    did = personal_repo.log_decision(kind="contact_update", summary="stored phone", version_ids=[vid])
    personal_repo.close()

    wb_repo = MemoryRepo(allowed_scopes={"wb"}, db_path=db)
    with pytest.raises(PermissionError):
        wb_repo.trace_decision(did)
    wb_repo.close()


# ── MemoryRepo convenience ───────────────────────────────────────────────────

def test_repo_read_current(tmp_repo):
    tmp_repo.assert_fact("competitor:ЦПВС", "price", 1299, "2026-06-01", "parser")
    result = tmp_repo.read_current("competitor:ЦПВС", "price")
    assert len(result) == 1
    assert result[0]["object"] == "1299"


def test_route_model_stays_local_when_personal_combined_with_other_scope(tmp_db):
    """route_model must key off *membership*, not exact-set equality: a repo
    scoped to {'personal', 'wb'} still carries personal data and must not
    fail open to the cloud model just because the scope set isn't exactly
    {'personal'}."""
    _, db = tmp_db
    repo = MemoryRepo(allowed_scopes={"personal", "wb"}, db_path=db)
    assert repo.route_model() == "local_lm_studio"
    repo.close()

    wb_only = MemoryRepo(allowed_scopes={"wb"}, db_path=db)
    assert wb_only.route_model() == "deepseek"
    wb_only.close()


def test_repo_history(tmp_repo):
    tmp_repo.assert_fact("competitor:ЦПВС", "price", 1299, "2026-06-01", "parser")
    tmp_repo.assert_fact("competitor:ЦПВС", "price", 1399, "2026-06-17", "parser")
    history = tmp_repo.history("competitor:ЦПВС", "price")
    assert len(history) == 2
