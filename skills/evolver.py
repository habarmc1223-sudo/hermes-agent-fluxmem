"""
Self-Evolving Skills Engine — auto-improves SKILL.md based on usage metrics.

Runs periodically. For each skill:
1. Reads SKILL.md + usage metrics
2. Asks DeepSeek: "How can this skill be improved?"
3. Saves suggested improvements for human review
4. Applies approved changes

Usage: python3 skills/evolver.py [--apply]
"""

import json
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

SKILLS_DIR = Path(__file__).parent
EVOLUTION_DIR = SKILLS_DIR / "evolution"
EVOLUTION_DIR.mkdir(exist_ok=True)


def get_skill_metrics(skill_dir: Path) -> dict:
    """Collect usage metrics for a skill."""
    metrics = {
        "skill": skill_dir.name,
        "has_skill_md": (skill_dir / "SKILL.md").exists(),
        "files": [f.name for f in skill_dir.iterdir() if f.is_file()],
        "last_modified": datetime.fromtimestamp(
            max(f.stat().st_mtime for f in skill_dir.iterdir() if f.is_file())
        ).isoformat() if any(skill_dir.iterdir()) else "unknown",
    }
    # Read evolution history if exists
    history_file = EVOLUTION_DIR / f"{skill_dir.name}.jsonl"
    if history_file.exists():
        history = []
        for line in history_file.read_text().splitlines():
            try:
                history.append(json.loads(line))
            except json.JSONDecodeError:
                pass
        metrics["evolution_count"] = len(history)
        metrics["last_evolved"] = history[-1]["date"] if history else None
    else:
        metrics["evolution_count"] = 0
    return metrics


def suggest_improvements(skill_dir: Path, client=None) -> str:
    """Ask DeepSeek for skill improvement suggestions."""
    skill_md = skill_dir / "SKILL.md"
    if not skill_md.exists():
        return ""

    content = skill_md.read_text(encoding="utf-8", errors="replace")
    metrics = get_skill_metrics(skill_dir)

    prompt = f"""Analyze this Hermes agent skill and suggest improvements.

Skill: {metrics['skill']}
Last modified: {metrics['last_modified']}
Evolution count: {metrics['evolution_count']}

Current SKILL.md:
{content[:2000]}

Suggest 2-3 specific improvements. Format:
## Improvements for {metrics['skill']}
### 1. [Title]
- What: [description]
- Why: [reasoning]
- Change: [specific SKILL.md diff]

### 2. ...
"""
    if client:
        try:
            resp = client.chat.completions.create(
                model="deepseek-chat",
                messages=[{"role": "user", "content": prompt}],
                temperature=0.2, max_tokens=500,
            )
            return resp.choices[0].message.content
        except Exception as e:
            return f"Error: {e}"
    return prompt  # Dry run — return the prompt


def evolve_skill(skill_dir: Path, client=None, apply: bool = False) -> Optional[str]:
    """Run one evolution cycle for a skill."""
    if not (skill_dir / "SKILL.md").exists():
        return None

    suggestions = suggest_improvements(skill_dir, client)

    # Save suggestions
    history_file = EVOLUTION_DIR / f"{skill_dir.name}.jsonl"
    record = {
        "skill": skill_dir.name,
        "date": datetime.now().isoformat(),
        "suggestions": suggestions,
        "applied": apply,
    }
    with open(history_file, "a") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")

    if apply and suggestions:
        # Append suggestions as comments to SKILL.md
        skill_md = skill_dir / "SKILL.md"
        original = skill_md.read_text(encoding="utf-8", errors="replace")
        comment = f"\n\n<!-- Evolution {datetime.now():%Y-%m-%d}\n{suggestions}\n-->"
        skill_md.write_text(original + comment, encoding="utf-8")

    return suggestions


def evolve_all(client=None, apply: bool = False):
    """Run evolution for all installed skills."""
    results = {}
    for skill_dir in sorted(SKILLS_DIR.iterdir()):
        if not skill_dir.is_dir() or skill_dir.name.startswith(".") or skill_dir.name.startswith("_"):
            continue
        if skill_dir.name in ("evolution", "index-cache"):
            continue
        try:
            result = evolve_skill(skill_dir, client, apply)
            if result:
                results[skill_dir.name] = "evolved" if apply else "suggested"
        except Exception as e:
            results[skill_dir.name] = f"error: {e}"
    return results


if __name__ == "__main__":
    apply = "--apply" in sys.argv
    if apply:
        from openai import OpenAI
        from dotenv import load_dotenv
        load_dotenv()
        client = OpenAI(api_key=os.getenv("DEEPSEEK_API_KEY"), base_url="https://api.deepseek.com/v1")
    else:
        client = None

    results = evolve_all(client, apply)
    for name, status in results.items():
        print(f"  {status}: {name}")
    print(f"\nTotal: {len(results)} skills processed")
    if not apply:
        print("Dry run — use --apply to write changes")
