"""
Secret Vault — store and use secrets without exposing them to the model.

The model NEVER receives real secret values. Instead:
1. Call secret_vault(action='get', key='MYKEY') → returns an opaque handle: "$VAULT:MYKEY"
2. Build a command using that handle: "curl -H 'Authorization: Bearer $VAULT:MYKEY' ..."
3. The terminal tool calls resolve_secrets_in_command() before execution,
   substituting the real value server-side only.
4. Real values never appear in model context, tool results, or logs.
"""

import json
import logging
import os
import re
import shlex

logger = logging.getLogger(__name__)

_VAULT_HANDLE_PREFIX = "$VAULT:"

# In-memory secret store (profile-scoped, survives session resets)
_secret_vault: dict[str, str] = {}

_ENV_PATH = None

SECRET_VAULT_SCHEMA = {
    "name": "secret_vault",
    "description": (
        "Manage secrets for tool commands without exposing values to the model. "
        "Use action='get' to get an opaque handle (e.g. '$VAULT:TOKEN') — "
        "place this handle literally in your command string and the terminal "
        "will substitute the real value before execution. "
        "The real secret value is NEVER returned to the model. "
        "Use action='set' to store a secret. "
        "Use action='list' to see stored key names (values are never shown). "
        "Use action='unset' to remove a secret."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["set", "get", "list", "unset", "reload"],
                "description": (
                    "'set' stores a secret, 'get' returns an opaque handle for use in commands, "
                    "'list' shows key names only, 'unset' removes, 'reload' re-reads .env"
                ),
            },
            "key": {
                "type": "string",
                "description": "Secret key name (e.g. 'TELEGRAM_TOKEN', 'GITHUB_TOKEN'). Required for set/get/unset.",
            },
            "value": {
                "type": "string",
                "description": "Secret value (only used with action='set'). Will be stored in memory, never echoed back.",
            },
            "from_env": {
                "type": "string",
                "description": "Load from an env var by name (alternative to 'value'). Only used with action='set'.",
            },
        },
        "required": ["action"],
    },
}


def _init_vault():
    global _ENV_PATH
    if _ENV_PATH is not None:
        return
    from hermes_constants import get_hermes_home
    home = get_hermes_home()
    env_path = os.path.join(home, ".env")
    _ENV_PATH = env_path
    if os.path.exists(env_path):
        with open(env_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, val = line.partition("=")
                key = key.strip()
                val = val.strip().strip("\"'")
                if key and val:
                    _secret_vault[key] = val


def _make_handle(key: str) -> str:
    return f"{_VAULT_HANDLE_PREFIX}{key}"


def secret_vault_tool(args, **kw):
    """Handle secret_vault tool calls."""
    _init_vault()
    action = args.get("action", "list")

    if action == "set":
        key = args.get("key", "")
        if not key:
            return json.dumps({"error": "key is required for set"}, ensure_ascii=False)
        value = args.get("value", "")
        from_env = args.get("from_env", "")
        if from_env:
            env_val = os.environ.get(from_env)
            if not env_val:
                return json.dumps({"error": f"env var '{from_env}' not set"}, ensure_ascii=False)
            value = env_val
        if not value:
            return json.dumps({"error": "value or from_env is required for set"}, ensure_ascii=False)
        _secret_vault[key] = value
        logger.info("Secret vault: set %s", key)
        # Return only the handle — real value never leaves the vault
        return json.dumps({
            "key": key,
            "status": "stored",
            "handle": _make_handle(key),
            "usage": f"Use '{_make_handle(key)}' in your command string — it will be substituted at execution time.",
        }, ensure_ascii=False)

    elif action == "get":
        key = args.get("key", "")
        if not key:
            return json.dumps({"error": "key is required for get"}, ensure_ascii=False)
        if key not in _secret_vault:
            return json.dumps({"error": f"secret '{key}' not found"}, ensure_ascii=False)
        # Return only the opaque handle — real value is NEVER returned to the model
        return json.dumps({
            "key": key,
            "handle": _make_handle(key),
            "exists": True,
            "usage": f"Use '{_make_handle(key)}' literally in your command — the terminal substitutes the real value before execution.",
        }, ensure_ascii=False)

    elif action == "list":
        keys = sorted(_secret_vault.keys())
        return json.dumps({
            "keys": keys,
            "count": len(keys),
            "note": "Values are never shown. Use action='get' to obtain a handle for a key.",
        }, ensure_ascii=False)

    elif action == "unset":
        key = args.get("key", "")
        if key in _secret_vault:
            del _secret_vault[key]
            return json.dumps({"key": key, "status": "removed"}, ensure_ascii=False)
        return json.dumps({"error": f"secret '{key}' not found"}, ensure_ascii=False)

    elif action == "reload":
        _secret_vault.clear()
        global _ENV_PATH
        _ENV_PATH = None
        _init_vault()
        return json.dumps({
            "status": "reloaded",
            "count": len(_secret_vault),
        }, ensure_ascii=False)

    return json.dumps({"error": f"Unknown action: {action}"}, ensure_ascii=False)


def resolve_secrets_in_command(command: str) -> str:
    """Replace $VAULT:KEY handles with real values from the vault before execution.

    Called by terminal_tool right before executing a command.
    The substituted command is NEVER logged — only the original handle-based
    command appears in logs.
    """
    _init_vault()

    def _replace_handle(match: re.Match) -> str:
        key = match.group(1)
        val = _secret_vault.get(key)
        if val is not None:
            # Commands are passed to `bash -c`, so quote to prevent injection
            # via secret values containing shell metacharacters.
            return shlex.quote(val)
        return match.group(0)  # leave unresolved handles as-is

    # Match $VAULT:KEY_NAME (key must be [A-Z0-9_]+)
    return re.sub(r"\$VAULT:([A-Z_][A-Z0-9_]*)", _replace_handle, command)


def check_secret_vault() -> bool:
    return True


# --- Registry ---
from tools.registry import registry, tool_error

registry.register(
    name="secret_vault",
    toolset="admin",
    schema=SECRET_VAULT_SCHEMA,
    handler=secret_vault_tool,
    check_fn=check_secret_vault,
    emoji="🔐",
)
