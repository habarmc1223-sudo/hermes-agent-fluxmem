"""
Plan/Echo Mode — agent generates a plan and waits for approval before executing tools.

When plan mode is active:
  - The agent runs normally through its conversation loop
  - But BEFORE calling any tool, it checks ``plan_mode`` flag
  - If ``plan_mode`` is True and tools would be called, it generates a textual plan
    describing what it intends to do and waits for user approval
  - After user says "go" or runs ``/go``, ``plan_mode`` is set to False and the
    agent re-enters the loop with full tool access

Implementation notes:
  - The mode flag lives on the AIAgent instance, managed via set_plan_mode()
  - The conversation loop checks agent.plan_mode before executing tool calls
  - When plan_mode and tools are pending, the agent outputs a plan message and
    sets agent.pending_plan = True (the loop breaks before tool execution)
  - On the next user message (or /go), pending_plan is cleared and plan_mode
    toggled off for that one turn
"""

from __future__ import annotations

import json
import logging
from typing import Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Per-session plan state (global dict keyed by session_id)
# ---------------------------------------------------------------------------
_plan_state: dict[str, dict] = {}
"""Structure:
    {
        session_id: {
            "enabled": bool,
            "pending_plan": bool,  # True when agent has output a plan and is waiting
        }
    }
"""


def get_plan_state(session_id: str) -> dict:
    """Get or initialise plan state for a session."""
    if session_id not in _plan_state:
        _plan_state[session_id] = {"enabled": False, "pending_plan": False}
    return _plan_state[session_id]


def set_plan_mode(session_id: str, enabled: bool) -> dict:
    """Enable or disable plan mode for a session."""
    state = get_plan_state(session_id)
    state["enabled"] = enabled
    if not enabled:
        state["pending_plan"] = False
    return {
        "session_id": session_id,
        "plan_mode": state["enabled"],
        "pending_plan": state["pending_plan"],
    }


def is_plan_mode(session_id: str) -> bool:
    """Check if plan mode is enabled."""
    return get_plan_state(session_id)["enabled"]


def set_pending_plan(session_id: str, pending: bool) -> None:
    """Mark or unmark a pending plan."""
    get_plan_state(session_id)["pending_plan"] = pending


def is_pending_plan(session_id: str) -> bool:
    """Check if a plan is pending approval."""
    return get_plan_state(session_id)["pending_plan"]


def toggle_plan_mode(session_id: str) -> dict:
    """Toggle plan mode on/off."""
    state = get_plan_state(session_id)
    return set_plan_mode(session_id, not state["enabled"])


def status(session_id: str) -> dict:
    """Return current plan mode status."""
    state = get_plan_state(session_id)
    return {
        "session_id": session_id,
        "plan_mode": state["enabled"],
        "pending_plan": state["pending_plan"],
        "message": "Plan mode is ON — agent will show a plan before executing any tools."
        if state["enabled"]
        else "Plan mode is OFF — agent executes tools immediately.",
    }


# ---------------------------------------------------------------------------
# System prompt injection — tells the LLM about plan mode
# ---------------------------------------------------------------------------
PLAN_MODE_SYSTEM_PROMPT = (
    "\n\n## Plan Mode\n"
    "Plan mode is CURRENTLY ACTIVE. "
    "You must follow this protocol:\n"
    "1. When given a task, FIRST think about what needs to be done.\n"
    "2. Then output a CLEAR PLAN describing the steps you would take, "
    "including which tools you would call and what files you would modify.\n"
    "3. DO NOT call any tools in plan mode — only describe the plan textually.\n"
    "4. End your plan with the question: **Execute this plan?** or similar.\n"
    "5. Wait for the user to approve or modify the plan.\n"
    "6. When the user says 'go', 'execute', 'proceed', 'да', 'продолжай', "
    "or runs /go, you will get full tool access for the next turn.\n"
    "7. If the user modifies the plan, incorporate their feedback and output "
    "an updated plan before executing."
)


def build_plan_mode_system_prompt(session_id: str) -> str:
    """Return the plan mode system prompt fragment if active, else empty string."""
    if is_plan_mode(session_id) and not is_pending_plan(session_id):
        return PLAN_MODE_SYSTEM_PROMPT
    return ""
