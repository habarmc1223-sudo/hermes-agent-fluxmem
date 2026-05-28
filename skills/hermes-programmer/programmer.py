"""
Hermes Programmer Agent — DeepSeek-powered coding executor.

Supports:
- Multi-provider: DeepSeek → OpenRouter → local (with circuit breaker)
- Task delegation: build, debug, refactor, review, test
- Structured prompts with file context injection
- Result streaming back to Hermes workflow

Usage:
    from agent.programmer import ProgrammerAgent
    agent = ProgrammerAgent()
    result = await agent.execute_task("refactor", files=["bot.py:4500:4600"])
"""

import json
import os
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

import yaml  # type: ignore
from openai import AsyncOpenAI


@dataclass
class TaskResult:
    task_id: str
    task_type: str
    status: str  # success | failed | partial
    model_used: str
    provider_used: str
    response: str
    files_touched: list = field(default_factory=list)
    elapsed_ms: int = 0
    error: Optional[str] = None


class ProviderRouter:
    """Multi-provider router with circuit breaker and fallback chain."""

    def __init__(self, config_path: str = None):
        config_path = config_path or os.path.join(
            os.path.dirname(os.path.dirname(__file__)), "config", "providers.yaml"
        )
        with open(config_path) as f:
            self.config = yaml.safe_load(f)

        self._clients: dict = {}
        self._failures: dict = {}
        self._circuit_open: dict = {}

    def _get_client(self, provider: str) -> AsyncOpenAI:
        if provider not in self._clients:
            cfg = self.config["providers"][provider]
            api_key = os.getenv(cfg["api_key_env"], "")
            base = cfg["base_url"].replace("${LOCAL_LLM_URL:-http://localhost:11434}",
                                             os.getenv("LOCAL_LLM_URL", "http://localhost:11434"))
            self._clients[provider] = AsyncOpenAI(api_key=api_key, base_url=base)
        return self._clients[provider]

    def _is_open(self, provider: str) -> bool:
        cb = self.config["fallback"]["circuit_breaker"]
        opened = self._circuit_open.get(provider)
        if opened and time.monotonic() - opened < cb["open_seconds"]:
            return False
        if opened:
            self._circuit_open.pop(provider, None)
            self._failures[provider] = 0
        return True

    def _record_failure(self, provider: str):
        self._failures[provider] = self._failures.get(provider, 0) + 1
        cb = self.config["fallback"]["circuit_breaker"]
        if self._failures[provider] >= cb["failure_threshold"]:
            self._circuit_open[provider] = time.monotonic()

    def _record_success(self, provider: str):
        self._failures[provider] = 0

    async def call(self, role: str, messages: list, **kwargs) -> tuple:
        """Route call through fallback chain. Returns (response_text, provider, model)."""
        role_cfg = self.config["roles"].get(role, self.config["roles"]["chat"])
        primary = role_cfg["primary"]

        for provider in self.config["fallback"]["order"]:
            if not self._is_open(provider):
                continue

            cfg = self.config["providers"][provider]
            model_key = role_cfg.get("model", "chat")
            model = cfg["models"].get(model_key, cfg["models"]["chat"])

            try:
                client = self._get_client(provider)
                resp = await client.chat.completions.create(
                    model=model,
                    messages=messages,
                    temperature=role_cfg.get("temperature", 0.2),
                    max_tokens=role_cfg.get("max_tokens", 2000),
                    **kwargs,
                )
                self._record_success(provider)
                return resp.choices[0].message.content, provider, model
            except Exception as e:
                self._record_failure(provider)
                last_error = str(e)
                continue

        raise RuntimeError(f"All providers failed. Last error: {last_error}")


class ProgrammerAgent:
    """DeepSeek-powered coding agent for Hermes workflow."""

    def __init__(self, config_path: str = None):
        self.router = ProviderRouter(config_path)
        self.config = self.router.config
        self._history: list = []

    async def execute_task(
        self,
        task_type: str,
        description: str = "",
        files: list = None,
        context: str = "",
        **kwargs,
    ) -> TaskResult:
        """Execute a coding task and return structured result."""
        task_id = f"prog-{int(time.monotonic() * 1000)}"
        t0 = time.monotonic()

        # Build prompt from template
        system_prompt = self.config["tasks"].get(task_type, {}).get(
            "system_prompt", "You are a software engineer."
        )

        # Build file context
        files_text = ""
        if files:
            for f in files[:5]:
                try:
                    if ":" in f:
                        path, lines = f.split(":", 1)
                        start, end = lines.split("-") if "-" in lines else (lines, str(int(lines) + 50))
                        content = Path(path).read_text(encoding="utf-8", errors="replace")
                        content_lines = content.split("\n")
                        excerpt = "\n".join(content_lines[int(start) - 1:int(end)])
                        files_text += f"\n### {path}:{start}-{end}\n```\n{excerpt}\n```\n"
                    else:
                        content = Path(f).read_text(encoding="utf-8", errors="replace")[:2000]
                        files_text += f"\n### {f}\n```\n{content}\n```\n"
                except Exception:
                    files_text += f"\n### {f}\n[unable to read]\n"

        user_prompt = f"""Task: {task_type}
Description: {description or 'Execute the task as described'}
Context: {context or 'Standard execution'}

Files:
{files_text or 'No specific files provided — work from context'}

Instructions:
1. Analyze the task and files
2. Provide specific, actionable output
3. For code changes: show diff or complete file content
4. For reviews: list findings by priority
5. For tests: write complete, runnable tests
"""
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]

        try:
            response, provider, model = await self.router.call(task_type, messages)
            elapsed = int((time.monotonic() - t0) * 1000)
            result = TaskResult(
                task_id=task_id,
                task_type=task_type,
                status="success",
                model_used=model,
                provider_used=provider,
                response=response,
                files_touched=files or [],
                elapsed_ms=elapsed,
            )
        except Exception as e:
            elapsed = int((time.monotonic() - t0) * 1000)
            result = TaskResult(
                task_id=task_id,
                task_type=task_type,
                status="failed",
                model_used="none",
                provider_used="none",
                response="",
                elapsed_ms=elapsed,
                error=str(e),
            )

        self._history.append(result)
        return result

    def get_history(self, limit: int = 10) -> list:
        return self._history[-limit:]
