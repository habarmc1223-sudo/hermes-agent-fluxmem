#!/usr/bin/env python3
"""
#8 Agent Feedback Loop — collects user feedback, tunes agent behavior.

Sources: 👍/👎 Telegram buttons, task success/failure, user corrections.
Feeds into: profile scoring, prompt improvement, agent selection.

Usage:
    feedback = FeedbackLoop()
    feedback.record("programmer", "build auth endpoint", rating=1)  # 👍
    feedback.record("researcher", "find trends", rating=-1)         # 👎
    suggestions = feedback.suggest_improvements()
"""

import json
import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

STATE_DIR = Path(os.getenv("HERMES_STATE_DIR", Path.home() / ".hermes" / "state"))
FEEDBACK_PATH = STATE_DIR / "feedback.jsonl"


class FeedbackEntry:
    def __init__(self, profile: str, task: str, rating: int = 0, comment: str = "",
                 user_id: int = 0, result_snippet: str = ""):
        self.profile = profile
        self.task = task[:200]
        self.rating = rating  # 1 = 👍, -1 = 👎, 0 = no feedback
        self.comment = comment
        self.user_id = user_id
        self.result_snippet = result_snippet[:300]
        self.timestamp = datetime.now().isoformat()


class FeedbackLoop:
    """Collects + analyzes agent feedback for continuous improvement."""

    def __init__(self):
        STATE_DIR.mkdir(parents=True, exist_ok=True)

    def record(self, profile: str, task: str, rating: int = 0, comment: str = "",
               user_id: int = 0, result_snippet: str = "") -> FeedbackEntry:
        entry = FeedbackEntry(profile, task, rating, comment, user_id, result_snippet)
        with open(FEEDBACK_PATH, "a") as f:
            f.write(json.dumps(entry.__dict__, ensure_ascii=False) + "\n")
        return entry

    def get_recent(self, profile: str = None, days: int = 7, limit: int = 50) -> list:
        if not FEEDBACK_PATH.exists():
            return []

        cutoff = (datetime.now() - timedelta(days=days)).isoformat()
        entries = []
        for line in FEEDBACK_PATH.read_text().splitlines():
            try:
                e = json.loads(line)
                if profile and e.get("profile") != profile:
                    continue
                if e.get("timestamp", "") >= cutoff:
                    entries.append(e)
            except json.JSONDecodeError:
                pass
        return entries[-limit:]

    def get_profile_score(self, profile: str) -> dict:
        entries = self.get_recent(profile, days=30, limit=200)
        if not entries:
            return {"profile": profile, "score": 0, "samples": 0, "trend": "no_data"}

        ratings = [e.get("rating", 0) for e in entries]
        positive = sum(1 for r in ratings if r > 0)
        negative = sum(1 for r in ratings if r < 0)

        recent = ratings[-20:]
        older = ratings[:-20] if len(ratings) > 20 else []

        return {
            "profile": profile,
            "score": round((positive / len(ratings) * 100) if ratings else 0, 1),
            "samples": len(ratings),
            "positive": positive,
            "negative": negative,
            "trend": "improving" if (sum(recent) / len(recent) if recent else 0) > (sum(older) / len(older) if older else 0) else "declining" if older else "stable",
        }

    def suggest_improvements(self) -> str:
        """Analyze feedback patterns and suggest agent improvements."""
        profiles = ["orchestrator", "researcher", "programmer", "ops", "reviewer"]
        report = ["## Agent Feedback Report\n"]

        for profile in profiles:
            score = self.get_profile_score(profile)
            if score["samples"] < 3:
                continue
            emoji = "🟢" if score["score"] > 70 else "🟡" if score["score"] > 40 else "🔴"
            report.append(
                f"### {emoji} {profile}: {score['score']}% ({score['samples']} ratings, {score['trend']})"
            )

        return "\n".join(report) if len(report) > 1 else "Not enough feedback data"

    def get_best_profile(self, task_type: str) -> str:
        """Recommend the best agent profile for a task type based on feedback."""
        scores = {}
        for profile in ["orchestrator", "researcher", "programmer", "ops", "reviewer"]:
            s = self.get_profile_score(profile)
            if s["samples"] >= 3:
                scores[profile] = s["score"]

        # Fallback mapping
        fallback = {
            "code": "programmer", "debug": "programmer", "research": "researcher",
            "deploy": "ops", "monitor": "ops", "review": "reviewer",
            "plan": "orchestrator",
        }

        best = max(scores, key=scores.get) if scores else None
        return best or fallback.get(task_type, "orchestrator")
