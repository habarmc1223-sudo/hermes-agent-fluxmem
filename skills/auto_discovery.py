#!/usr/bin/env python3
"""
#5 Skills Auto-Discovery — scan skills/ directory and auto-register without restart.

Detects new/modified SKILL.md files, validates them, and makes them available
to the agent runtime without requiring a restart.

Usage:
    python3 skills/auto_discovery.py          # scan once
    python3 skills/auto_discovery.py --watch  # watch for changes
"""

import json
import os
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

SKILLS_DIR = Path(__file__).parent
REGISTRY_PATH = SKILLS_DIR / "registry.json"


def parse_skill_md(path: Path) -> Optional[dict]:
    """Parse SKILL.md frontmatter and extract skill metadata."""
    if not path.exists():
        return None

    content = path.read_text(encoding="utf-8", errors="replace")
    meta = {}

    # Parse YAML frontmatter (between --- markers)
    fm_match = re.match(r'^---\s*\n(.*?)\n---', content, re.DOTALL)
    if fm_match:
        for line in fm_match.group(1).split("\n"):
            line = line.strip()
            if ":" in line:
                key, _, value = line.partition(":")
                meta[key.strip()] = value.strip().strip('"').strip("'")

    # Extract description (first non-empty line after frontmatter)
    body = content[fm_match.end():] if fm_match else content
    for line in body.split("\n"):
        line = line.strip()
        if line and not line.startswith("#"):
            meta["description"] = line[:200]
            break

    meta["path"] = str(path.relative_to(SKILLS_DIR.parent))
    meta["name"] = meta.get("name", path.parent.name)
    meta["version"] = meta.get("version", "0.0.0")
    meta["last_scanned"] = datetime.now().isoformat()
    meta["size_bytes"] = path.stat().st_size
    return meta


def discover_skills() -> dict:
    """Scan all SKILL.md files and build registry."""
    skills = {}
    for skill_dir in SKILLS_DIR.iterdir():
        if not skill_dir.is_dir() or skill_dir.name.startswith(".") or skill_dir.name.startswith("_"):
            continue
        if skill_dir.name in ("evolution", "index-cache"):
            continue

        skill_md = skill_dir / "SKILL.md"
        if skill_md.exists():
            meta = parse_skill_md(skill_md)
            if meta:
                skills[meta["name"]] = meta

    # Save registry
    registry = {
        "generated_at": datetime.now().isoformat(),
        "total_skills": len(skills),
        "skills": skills,
    }
    with open(REGISTRY_PATH, "w") as f:
        json.dump(registry, f, indent=2, ensure_ascii=False)

    return registry


def get_registry() -> dict:
    """Load cached registry, or discover if missing."""
    if REGISTRY_PATH.exists():
        cache_age = time.time() - REGISTRY_PATH.stat().st_mtime
        if cache_age < 3600:  # Cache valid for 1 hour
            with open(REGISTRY_PATH) as f:
                return json.load(f)
    return discover_skills()


def watch_skills(interval: int = 60):
    """Watch for new or modified SKILL.md files."""
    print(f"Watching {SKILLS_DIR} every {interval}s...")
    seen = {}
    while True:
        for skill_dir in SKILLS_DIR.iterdir():
            if not skill_dir.is_dir() or skill_dir.name.startswith("."):
                continue
            skill_md = skill_dir / "SKILL.md"
            if not skill_md.exists():
                continue
            mtime = skill_md.stat().st_mtime
            prev = seen.get(str(skill_dir))
            if prev != mtime:
                seen[str(skill_dir)] = mtime
                meta = parse_skill_md(skill_md)
                status = "🆕 new" if prev is None else "🔄 updated"
                print(f"[{datetime.now():%H:%M:%S}] {status}: {meta['name']} v{meta['version']}")
                discover_skills()  # Update registry
        time.sleep(interval)


if __name__ == "__main__":
    import sys
    if "--watch" in sys.argv:
        watch_skills()
    else:
        registry = discover_skills()
        print(f"Discovered {registry['total_skills']} skills:")
        for name, meta in registry["skills"].items():
            print(f"  {name} v{meta['version']} — {meta.get('description', 'no description')[:80]}")
