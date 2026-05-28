#!/usr/bin/env python3
"""
#1 Telegram Agent Gateway — invoke Hermes agents from Telegram bot.

Usage from bot: /agent <profile> <task>
  /agent orchestrator "проанализируй конкурентов"
  /agent researcher "найди тренды WB за неделю"
  /agent programmer "почини баг в bot.py:4500"

Integration: bot1 imports this module or calls via subprocess.
"""

import asyncio
import json
import os
import urllib.request
import urllib.parse
from datetime import datetime
from pathlib import Path
from typing import Optional

AGENT_PROFILES = ["orchestrator", "researcher", "programmer", "ops", "reviewer"]

# Profile → system prompt mapping
PROFILES_DIR = Path(__file__).parent.parent / "profiles"

def _load_profile(profile: str) -> str:
    """Load agent profile SOUL."""
    path = PROFILES_DIR / f"{profile}.md"
    if path.exists():
        return path.read_text(encoding="utf-8")
    return f"You are the {profile} agent. Execute tasks with precision."

async def execute_agent_task(profile: str, task: str, user_id: int = None) -> str:
    """Execute a task through the specified agent profile via DeepSeek."""
    from openai import AsyncOpenAI

    if profile not in AGENT_PROFILES:
        return f"Unknown profile: {profile}. Available: {', '.join(AGENT_PROFILES)}"

    soul = _load_profile(profile)
    client = AsyncOpenAI(
        api_key=os.getenv("DEEPSEEK_API_KEY"),
        base_url="https://api.deepseek.com/v1"
    )

    messages = [
        {"role": "system", "content": f"{soul}\n\nExecute the user's task efficiently. Be concise."},
        {"role": "user", "content": task},
    ]

    try:
        resp = await client.chat.completions.create(
            model="deepseek-chat",
            messages=messages,
            temperature=0.3, max_tokens=1500,
        )
        result = resp.choices[0].message.content
        return f"🤖 [{profile}]\n\n{result}"
    except Exception as e:
        return f"❌ [{profile}] Error: {e}"


async def send_to_telegram(token: str, chat_id: int, text: str):
    """Send result back to Telegram."""
    data = urllib.parse.urlencode({
        "chat_id": chat_id, "text": text[:4000], "parse_mode": "HTML"
    }).encode()
    urllib.request.urlopen(
        urllib.request.Request(f"https://api.telegram.org/bot{token}/sendMessage", data),
        timeout=10
    )


async def handle_agent_command(update, context):
    """Bot handler for /agent command. Import and register in bot.py."""
    args = context.args or []
    if len(args) < 2:
        await update.effective_message.reply_text(
            "Использование: /agent <profile> <задача>\n"
            "Профили: " + ", ".join(AGENT_PROFILES) + "\n"
            "Пример: /agent orchestrator разбери конкурентов"
        )
        return

    profile = args[0].lower()
    task = " ".join(args[1:])

    await update.effective_message.reply_text(f"🤖 [{profile}] работает...")
    result = await execute_agent_task(profile, task)
    await update.effective_message.reply_text(result)

    # Save to feedback store
    _save_feedback_entry(profile, task, result, update.effective_user.id)


def _save_feedback_entry(profile: str, task: str, result: str, user_id: int):
    """Save agent interaction for feedback loop."""
    entry = {
        "profile": profile,
        "task": task[:200],
        "result": result[:200],
        "user_id": user_id,
        "timestamp": datetime.now().isoformat(),
    }
    try:
        path = Path(os.getenv("HERMES_STATE_DIR", Path.home() / ".hermes" / "state"))
        path.mkdir(parents=True, exist_ok=True)
        fd = os.open(path / "feedback.jsonl", os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        with os.fdopen(fd, "a") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception:
        pass
