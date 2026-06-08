"""
Per-task Approval Bypass — allow specific commands for the current session only.
Allowlist is stored in session state, cleared on /reset.

Usage: allow(command_pattern='docker,kill') — skip approval prompts for commands
whose first token (argv[0]) exactly matches one of the given names.

Security: patterns are matched against argv[0] (the command binary) with exact
whole-token comparison, not substring search. This prevents 'docker' from
accidentally allowing 'curl -H from-docker.example.com'.
Hardline-blocked commands (rm -rf /, shutdown, reboot, etc.) are ALWAYS blocked
regardless of the allowlist — the hardline check runs before this one.
"""

import json
import logging
import os
import re
import shlex

logger = logging.getLogger(__name__)

# Broad patterns that would allow too many commands — reject at add time.
_OVERBROAD_PATTERNS = frozenset({"sudo", "sh", "bash", "zsh", "fish", "python", "python3", "perl", "ruby", "node"})

# In-memory allowlist — keyed by session_key
# Structure: {session_key: [str exact patterns]}
_allowlist: dict[str, list[str]] = {}

ALLOW_SCHEMA = {
    "name": "allow",
    "description": (
        "Temporarily bypass approval prompts for specific commands in this session. "
        "Pattern is matched against the command binary name (argv[0]) exactly — "
        "'docker' allows only commands whose first token is 'docker', nothing else. "
        "Hardline-blocked commands (rm -rf /, shutdown, reboot, etc.) remain blocked always. "
        "Use allow(action='list') to see current allowlist. "
        "Use allow(action='clear') to clear all. "
        "Use allow(action='revoke', command_pattern='docker') to remove a pattern. "
        "The allowlist resets on /reset or new session."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["add", "list", "clear", "revoke"],
                "description": "'add' adds a pattern, 'list' shows current patterns, 'clear' removes all, 'revoke' removes specific pattern",
                "default": "add",
            },
            "command_pattern": {
                "type": "string",
                "description": "Comma-separated command binary names to allow (e.g. 'docker,kill'). Required for add and revoke.",
            },
        },
        "required": [],
    },
}


def _get_session_key() -> str:
    try:
        from tools.approval import get_current_session_key
        return get_current_session_key(default="default")
    except Exception:
        return "default"


def _ensure_list(session_key: str) -> list:
    if session_key not in _allowlist:
        _allowlist[session_key] = []
    return _allowlist[session_key]


def _parse_argv0(command: str) -> str:
    """Extract the command binary name (argv[0]) from a shell command string."""
    try:
        tokens = shlex.split(command)
    except ValueError:
        tokens = command.split()
    if not tokens:
        return ""
    return os.path.basename(tokens[0]).lower()


def is_command_allowed(command: str) -> bool:
    """Check if command's argv[0] exactly matches an allowlisted pattern."""
    session_key = _get_session_key()
    if session_key not in _allowlist:
        return False
    argv0 = _parse_argv0(command)
    if not argv0:
        return False
    for pattern in _allowlist[session_key]:
        if argv0 == pattern.lower():
            return True
    return False


def allow_tool(args, **kw):
    """Handle allow tool calls."""
    action = args.get("action", "add")
    session_key = _get_session_key()
    lst = _ensure_list(session_key)

    if action == "add":
        patterns_str = args.get("command_pattern", "")
        if not patterns_str:
            return json.dumps({"error": "command_pattern is required for add"}, ensure_ascii=False)

        added = []
        rejected = []
        existing = {p.lower() for p in lst}

        for raw in patterns_str.split(","):
            p = raw.strip().lower()
            if not p:
                continue
            # Reject overbroad patterns that would allow shell interpreters
            if p in _OVERBROAD_PATTERNS:
                rejected.append({"pattern": p, "reason": "too broad — would allow arbitrary code execution"})
                continue
            # Reject patterns containing path separators or shell metacharacters
            if re.search(r"[/\\;&|<>$`!]", p):
                rejected.append({"pattern": p, "reason": "contains shell metacharacters"})
                continue
            if p not in existing:
                lst.append(p)
                existing.add(p)
                added.append(p)

        result: dict = {
            "status": "ok",
            "session_key": session_key,
            "allowed_patterns": list(lst),
            "added": added,
            "note": "Patterns match argv[0] (binary name) exactly. Hardline blocks are never bypassed.",
        }
        if rejected:
            result["rejected"] = rejected
        return json.dumps(result, ensure_ascii=False)

    elif action == "list":
        return json.dumps({
            "session_key": session_key,
            "allowed_patterns": list(lst),
            "count": len(lst),
        }, ensure_ascii=False)

    elif action == "clear":
        _allowlist[session_key] = []
        return json.dumps({"status": "cleared", "session_key": session_key}, ensure_ascii=False)

    elif action == "revoke":
        patterns_str = args.get("command_pattern", "")
        if not patterns_str:
            return json.dumps({"error": "command_pattern is required for revoke"}, ensure_ascii=False)
        to_remove = {p.strip().lower() for p in patterns_str.split(",") if p.strip()}
        before = len(lst)
        _allowlist[session_key] = [p for p in lst if p.lower() not in to_remove]
        removed = before - len(_allowlist[session_key])
        return json.dumps({
            "status": "ok",
            "removed_count": removed,
            "remaining": list(_allowlist[session_key]),
        }, ensure_ascii=False)

    return json.dumps({"error": f"Unknown action: {action}"}, ensure_ascii=False)


def check_allow() -> bool:
    return True


# --- Registry ---
from tools.registry import registry, tool_error

registry.register(
    name="allow",
    toolset="admin",
    schema=ALLOW_SCHEMA,
    handler=allow_tool,
    check_fn=check_allow,
    emoji="✅",
)
