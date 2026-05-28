#!/usr/bin/env python3
"""
#2 Multi-agent Coordination Bus — lightweight message-passing between agents.

Orchestrator → researcher | programmer | ops | reviewer → results → orchestrator.

Usage:
    bus = CoordinationBus()
    result = await bus.delegate("researcher", "найди тренды WB")
    results = await bus.parallel([
        ("researcher", "тренды WB"),
        ("programmer", "почини баг в bot.py"),
    ])
"""

import asyncio
import json
import os
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional


@dataclass
class AgentMessage:
    id: str
    from_agent: str
    to_agent: str
    task: str
    result: Optional[str] = None
    status: str = "pending"  # pending | running | done | failed
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    error: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "id": self.id, "from": self.from_agent, "to": self.to_agent,
            "task": self.task, "result": self.result, "status": self.status,
            "started_at": self.started_at, "completed_at": self.completed_at,
            "error": self.error,
        }


class CoordinationBus:
    """Lightweight message bus for inter-agent communication."""

    AGENTS = ["orchestrator", "researcher", "programmer", "ops", "reviewer"]
    _history: list = []
    _messages: dict = {}
    _locks: dict = {}

    def __init__(self, log_path: str = None):
        self.log_path = log_path or str(
            Path(os.getenv("HERMES_STATE_DIR", Path.home() / ".hermes" / "state")) / "bus_log.jsonl"
        )

    async def delegate(self, to_agent: str, task: str, from_agent: str = "orchestrator") -> AgentMessage:
        """Send a task to an agent and wait for result."""
        if to_agent not in self.AGENTS:
            return AgentMessage(
                id="", from_agent=from_agent, to_agent=to_agent,
                task=task, status="failed", error=f"Unknown agent: {to_agent}"
            )

        msg = AgentMessage(
            id=f"msg-{int(time.monotonic()*1000)}",
            from_agent=from_agent, to_agent=to_agent,
            task=task, status="running", started_at=datetime.now().isoformat(),
        )

        # Execute via DeepSeek with the agent's profile
        try:
            from gateway.telegram_gateway import _load_profile
            from openai import AsyncOpenAI

            soul = _load_profile(to_agent)
            client = AsyncOpenAI(
                api_key=os.getenv("DEEPSEEK_API_KEY"),
                base_url="https://api.deepseek.com/v1",
            )
            resp = await client.chat.completions.create(
                model="deepseek-chat",
                messages=[
                    {"role": "system", "content": f"{soul}\n\nDelegated by: {from_agent}. Execute and return result."},
                    {"role": "user", "content": task},
                ],
                temperature=0.3, max_tokens=1000,
            )
            msg.result = resp.choices[0].message.content
            msg.status = "done"
        except Exception as e:
            msg.status = "failed"
            msg.error = str(e)

        msg.completed_at = datetime.now().isoformat()
        self._history.append(msg.to_dict())
        self._log(msg)
        return msg

    async def parallel(self, tasks: list[tuple]) -> list[AgentMessage]:
        """Execute multiple agent tasks in parallel."""
        coros = [self.delegate(agent, task) for agent, task in tasks]
        return await asyncio.gather(*coros, return_exceptions=True)

    async def workflow(self, plan: dict, from_agent: str = "orchestrator") -> dict:
        """Execute a workflow plan: {phase: [(agent, task), ...]}"""
        results = {}
        for phase, tasks in plan.items():
            msgs = await self.parallel(tasks)
            results[phase] = [m.to_dict() if isinstance(m, AgentMessage) else str(m) for m in msgs]
        return results

    def get_history(self, limit: int = 20) -> list:
        return self._history[-limit:]

    def stats(self) -> dict:
        """Agent usage statistics."""
        counts = {a: 0 for a in self.AGENTS}
        success = {a: 0 for a in self.AGENTS}
        for entry in self._history:
            agent = entry.get("to", "")
            if agent in counts:
                counts[agent] += 1
                if entry.get("status") == "done":
                    success[agent] += 1
        return {
            "total_tasks": len(self._history),
            "by_agent": counts,
            "success_rate": {a: (success[a] / counts[a] * 100) if counts[a] else 0 for a in self.AGENTS},
        }

    def _log(self, msg: AgentMessage):
        Path(self.log_path).parent.mkdir(parents=True, exist_ok=True)
        with open(self.log_path, "a") as f:
            f.write(json.dumps(msg.to_dict(), ensure_ascii=False) + "\n")
