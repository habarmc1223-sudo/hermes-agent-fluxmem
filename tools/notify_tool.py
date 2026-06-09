"""
Agent-Initiated Notifications — send alerts to the user when background processes
complete, crash, or need attention. Integrates with terminal(background=true).

Usage: notify(message='bot1 crashed, restarting', level='warning')
The cron watchdog and background process monitor use this automatically.
"""

import json
import logging
import os

logger = logging.getLogger(__name__)

NOTIFY_SCHEMA = {
    "name": "notify",
    "description": (
        "Send an async notification to the user. Use this to alert the user about "
        "completed background tasks, crashed processes, or anything that needs attention "
        "when you're in the middle of another task. "
        "The notification appears in the user's home channel (Telegram/etc). "
        "Levels: 'info' (default), 'warning', 'error', 'success'. "
        "Notifications are fire-and-forget — the agent continues working."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "message": {
                "type": "string",
                "description": "Notification message text. Keep concise — the user gets this in their chat.",
            },
            "level": {
                "type": "string",
                "enum": ["info", "warning", "error", "success"],
                "description": "Importance level. 'error' is urgent, 'info' is FYI.",
                "default": "info",
            },
            "data": {
                "type": "string",
                "description": "Optional JSON-encoded metadata (exit_code, pid, etc.) — shown as details.",
                "default": "",
            },
        },
        "required": ["message"],
    },
}

_ICONS = {
    "info": "ℹ️",
    "warning": "⚠️",
    "error": "🚨",
    "success": "✅",
}


def _send_telegram_notification(message: str, level: str, data: str = ""):
    """Send a notification via Telegram Bot API (if configured)."""
    try:
        # Try to use the gateway's send infrastructure
        from tools.send_message_tool import send_message_tool
        icon = _ICONS.get(level, "ℹ️")
        full_msg = f"{icon} **{level.upper()}**\n{message}"
        if data:
            full_msg += f"\n`{data}`"
        
        # Use the existing send_message tool internally
        result = send_message_tool({"action": "send", "message": full_msg}, **{})
        return result
    except Exception as e:
        logger.warning("Failed to send notification via gateway: %s", e)
        return json.dumps({"error": str(e)})


def notify_tool(args, **kw):
    """Handle notify tool calls."""
    message = args.get("message", "")
    level = args.get("level", "info")
    data = args.get("data", "")

    if not message:
        return json.dumps({"error": "message is required"}, ensure_ascii=False)

    if level not in ("info", "warning", "error", "success"):
        level = "info"

    # Fire-and-forget
    import threading
    t = threading.Thread(
        target=_send_telegram_notification,
        args=(message, level, data),
        daemon=True,
    )
    t.start()

    return json.dumps({
        "dispatched": True,
        "level": level,
        "message": message,
    }, ensure_ascii=False)


def check_notify() -> bool:
    """Always available. May silently fail if no gateway configured."""
    return True


# --- Registry ---
from tools.registry import registry, tool_error

registry.register(
    name="notify",
    toolset="messaging",
    schema=NOTIFY_SCHEMA,
    handler=notify_tool,
    check_fn=check_notify,
    emoji="🔔",
)
