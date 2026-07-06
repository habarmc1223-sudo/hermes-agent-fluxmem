"""Migrate existing WB JSON snapshots into facts.db.

Run once:  python3 migrate_snapshots.py

Renames nothing (snapshots stay in place as read-only backup).
"""
import json
import sys
from pathlib import Path

STATE_DIR = Path.home() / ".hermes" / "state"

# Add hermes-agent to path so facts_repo is importable
sys.path.insert(0, str(Path(__file__).parents[1]))
from memory.facts_repo import MemoryRepo


def load_json(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text())
    except Exception:
        return None


def migrate_finance(repo: MemoryRepo, data: dict) -> int:
    date = data.get("date", "2026-01-01")
    count = 0
    for predicate, value in [
        ("revenue", data.get("revenue")),
        ("profit", data.get("profit")),
        ("fines", data.get("fines")),
    ]:
        if value is not None:
            repo.assert_fact(
                subject="wb:finance",
                predicate=predicate,
                obj=value,
                valid_from=date + "T00:00:00Z",
                source_kind="import",
                source_ref="wbc-finance-snapshot.json",
                otype="number",
                scope="wb",
            )
            count += 1
    return count


def migrate_ads(repo: MemoryRepo, data: dict) -> int:
    date = data.get("date", "2026-01-01")
    count = 0
    for predicate, value in [
        ("ads_spend", data.get("spend")),
        ("ads_revenue", data.get("revenue")),
        ("ads_drr", data.get("drr")),
        ("ads_romi", data.get("romi")),
    ]:
        if value is not None:
            repo.assert_fact(
                subject="wb:ads",
                predicate=predicate,
                obj=value,
                valid_from=date + "T00:00:00Z",
                source_kind="import",
                source_ref="wbc-ads-snapshot.json",
                otype="number",
                scope="wb",
            )
            count += 1
    return count


def migrate_seo(repo: MemoryRepo, data: dict) -> int:
    date = data.get("date", "2026-01-01")
    positions = data.get("positions", [])
    count = 0
    for pos in positions:
        nm_id = pos.get("nm_id")
        keyword = pos.get("keyword", "")
        avg_pos = pos.get("avg_pos")
        if nm_id and avg_pos is not None:
            repo.assert_fact(
                subject=f"sku:{nm_id}",
                predicate=f"seo_position:{keyword}",
                obj=avg_pos,
                valid_from=date + "T00:00:00Z",
                source_kind="import",
                source_ref="wbc-seo-snapshot.json",
                otype="number",
                scope="wb",
            )
            count += 1
    return count


def main():
    repo = MemoryRepo(allowed_scopes={"wb"})
    total = 0

    snapshots = {
        "wbc-finance-snapshot.json": migrate_finance,
        "wbc-ads-snapshot.json": migrate_ads,
        "wbc-seo-snapshot.json": migrate_seo,
    }

    for filename, fn in snapshots.items():
        path = STATE_DIR / filename
        if not path.exists():
            print(f"  skip (not found): {filename}")
            continue
        data = load_json(path)
        if not data:
            print(f"  skip (parse error): {filename}")
            continue
        count = fn(repo, data)
        print(f"  {filename}: {count} facts")
        total += count

    repo.close()
    print(f"Total: {total} facts migrated → {Path.home() / '.hermes' / 'state' / 'facts.db'}")


if __name__ == "__main__":
    main()
