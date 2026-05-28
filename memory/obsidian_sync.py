#!/usr/bin/env python3
"""
#4 Obsidian Memory Sync — bidirectional sync between Hermes memory and Obsidian vault.

Hermes memory (SQLite) ↔ Obsidian vault (GitHub: habarmc1223-sudo/obsidian-vault)

Sync directions:
- memory → vault: export agent knowledge as Markdown notes
- vault → memory: import vault pages as context for agents

Usage: python3 memory/obsidian_sync.py [--export|--import|--both]
"""

import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

MEMORY_STORE = None  # Lazy import
VAULT_CLONE = Path("/tmp/obsidian-vault")


def _get_store():
    global MEMORY_STORE
    if MEMORY_STORE is None:
        from memory.memory_store import MemoryStore
        MEMORY_STORE = MemoryStore()
    return MEMORY_STORE


def _ensure_vault():
    """Clone vault if not present."""
    if not (VAULT_CLONE / ".git").exists():
        token = os.getenv("GITHUB_TOKEN") or os.getenv("GH_TOKEN", "")
        if token:
            import subprocess
            subprocess.run(
                ["git", "clone", f"https://{token}@github.com/habarmc1223-sudo/obsidian-vault.git",
                 str(VAULT_CLONE)], capture_output=True, timeout=30
            )
    return VAULT_CLONE.exists()


def export_to_vault():
    """Export Hermes memory entries to Obsidian vault."""
    store = _get_store()
    vault = VAULT_CLONE / "AI-Wiki" / "Hermes-Memory"
    vault.mkdir(parents=True, exist_ok=True)

    date_str = datetime.now().strftime("%Y-%m-%d")
    exported = 0

    # Export project context
    for project in ["bot1", "wb-copilot"]:
        entries = store.get_by_project(project, limit=30)
        if entries:
            lines = [f"# {project} — Agent Knowledge\n", f"*Synced: {date_str}*\n"]
            for e in entries:
                importance = "⭐" * min(5, e.get("importance", 3))
                lines.append(f"### {e['key']} {importance}")
                lines.append(f"Category: {e.get('category', 'general')}")
                lines.append(f"\n{e['value']}\n")
                lines.append("---\n")

            (vault / f"{project}-knowledge.md").write_text(
                "\n".join(lines), encoding="utf-8"
            )
            exported += 1
            print(f"  ✅ {project}: {len(entries)} entries → vault")

    # Export user context
    user_facts = store.get_by_category("user_knowledge", limit=50)
    if user_facts:
        lines = [f"# User Facts — Auto-Synced\n", f"*Last sync: {date_str}*\n"]
        for f in user_facts:
            lines.append(f"- {f['value'][:200]}")
        (vault / "user-facts.md").write_text("\n".join(lines), encoding="utf-8")
        exported += 1
        print(f"  ✅ User facts: {len(user_facts)} entries → vault")

    # Export agent decisions
    decisions = store.get_by_category("decisions", limit=50)
    if decisions:
        lines = [f"# Agent Decisions Log\n", f"*Last sync: {date_str}*\n"]
        for d in decisions:
            lines.append(f"### {d['key']}")
            lines.append(f"{d['value']}\n---\n")
        (vault / "agent-decisions.md").write_text("\n".join(lines), encoding="utf-8")
        exported += 1
        print(f"  ✅ Decisions: {len(decisions)} entries → vault")

    return exported


def import_from_vault():
    """Import Obsidian vault pages into Hermes memory context."""
    store = _get_store()
    imported = 0

    ai_wiki = VAULT_CLONE / "AI-Wiki"
    if not ai_wiki.exists():
        print("  ⚠️ AI-Wiki not found in vault")
        return 0

    for md_file in ai_wiki.glob("*.md"):
        if md_file.name.startswith("Hermes-"):
            continue  # Skip already-exported files

        content = md_file.read_text(encoding="utf-8", errors="replace")
        if len(content) < 50:
            continue

        key = f"vault:{md_file.stem}"
        store.set(key, content[:2000], category="vault_knowledge", project="general", importance=4)
        imported += 1

    print(f"  ✅ Imported {imported} vault pages → memory")
    return imported


def sync_both():
    """Full bidirectional sync."""
    print("=== Hermes ↔ Obsidian Sync ===")
    if not _ensure_vault():
        print("  ❌ Vault not accessible")
        return

    print("\n📤 Exporting memory → vault...")
    exported = export_to_vault()

    print("\n📥 Importing vault → memory...")
    imported = import_from_vault()

    # Commit and push if there are changes
    if exported:
        import subprocess
        subprocess.run(["git", "-C", str(VAULT_CLONE), "add", "-A"], capture_output=True)
        subprocess.run([
            "git", "-C", str(VAULT_CLONE), "commit", "-m",
            f"Hermes memory sync: {datetime.now():%Y-%m-%d %H:%M}"
        ], capture_output=True)
        subprocess.run(["git", "-C", str(VAULT_CLONE), "push"], capture_output=True, timeout=15)
        print("  ✅ Changes pushed to vault")

    print(f"\nDone: {exported} exported, {imported} imported")


if __name__ == "__main__":
    if "--export" in sys.argv:
        _ensure_vault() and export_to_vault()
    elif "--import" in sys.argv:
        _ensure_vault() and import_from_vault()
    else:
        sync_both()
