"""
Systemd Service Management Tool — query and control systemd services.
Provides structured access to service status, logs, start/stop/restart.

Usage: systemctl(action='status|start|stop|restart|logs', service='bot1', lines=50)
"""

import json
import logging
import shlex
import subprocess

logger = logging.getLogger(__name__)

SYSTEMCTL_SCHEMA = {
    "name": "systemctl",
    "description": (
        "Manage systemd services: view status, start, stop, restart, read logs. "
        "Uses 'sudo systemctl' internally. "
        "Returns structured JSON with service info or error."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["status", "start", "stop", "restart", "logs", "is-active", "is-enabled"],
                "description": (
                    "Operation: status (full status), start, stop, restart, "
                    "logs (last N lines via journalctl), is-active, is-enabled"
                ),
            },
            "service": {
                "type": "string",
                "description": "Systemd service name (e.g. 'bot1', 'nginx', 'docker'). Required for all actions.",
            },
            "lines": {
                "type": "integer",
                "description": "Number of log lines to return (only for action='logs'). Default 50, max 500.",
                "default": 50,
                "minimum": 1,
                "maximum": 500,
            },
        },
        "required": ["action", "service"],
    },
}


def _run_cmd(cmd: list, timeout: int = 15) -> tuple[str, str, int]:
    """Run a command and return (stdout, stderr, exit_code)."""
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return result.stdout, result.stderr, result.returncode
    except subprocess.TimeoutExpired:
        return "", "Command timed out", -1
    except FileNotFoundError:
        return "", "Command not found (systemctl not available)", -1


import re as _re

# Valid systemd unit names: letters, digits, @, :, _, ., - only.
# Reject anything starting with '-' (flag injection) or exceeding 256 chars.
_SERVICE_NAME_RE = _re.compile(r'^[A-Za-z0-9@:_.\-]+$')
_SERVICE_MAX_LEN = 256


def _validate_service(service: str) -> str | None:
    """Return None if valid, or an error message if not."""
    if not service:
        return "service name is required"
    if len(service) > _SERVICE_MAX_LEN:
        return f"service name too long (max {_SERVICE_MAX_LEN} chars)"
    if service.startswith("-"):
        return "service name must not start with '-' (flag injection)"
    if not _SERVICE_NAME_RE.match(service):
        return f"service name contains invalid characters (allowed: A-Za-z0-9@:_.-)"
    return None


def systemctl_tool(args, **kw):
    """Handle systemctl tool calls."""
    action = args.get("action", "status")
    service = args.get("service", "")
    lines = int(args.get("lines", 50))

    err_msg = _validate_service(service)
    if err_msg:
        return json.dumps({"error": err_msg}, ensure_ascii=False)

    try:
        if action == "status":
            # '--' terminates flag parsing; service name treated as positional arg
            out, err, code = _run_cmd(["sudo", "systemctl", "status", "--no-pager", "--", service])
            if code == 0:
                info = _parse_status_output(out, service)
                return json.dumps(info, ensure_ascii=False, default=str)
            return json.dumps({
                "service": service,
                "error": err or out,
                "exit_code": code,
            }, ensure_ascii=False)

        elif action == "logs":
            out, err, code = _run_cmd(
                ["sudo", "journalctl", "--no-pager", "-n", str(lines), "-u", "--", service]
            )
            return json.dumps({
                "service": service,
                "logs": out.splitlines() if out else [],
                "total_lines": len(out.splitlines()) if out else 0,
                "error": err or None,
            }, ensure_ascii=False)

        elif action in ("start", "stop", "restart"):
            out, err, code = _run_cmd(["sudo", "systemctl", action, "--", service])
            if code == 0:
                status_out, _, _ = _run_cmd(["sudo", "systemctl", "is-active", "--", service])
                return json.dumps({
                    "service": service,
                    "action": action,
                    "success": True,
                    "new_status": status_out.strip(),
                }, ensure_ascii=False)
            return json.dumps({
                "service": service,
                "action": action,
                "success": False,
                "error": err or out,
            }, ensure_ascii=False)

        elif action == "is-active":
            out, err, code = _run_cmd(["sudo", "systemctl", "is-active", "--", service])
            return json.dumps({"service": service, "active": out.strip()}, ensure_ascii=False)

        elif action == "is-enabled":
            out, err, code = _run_cmd(["sudo", "systemctl", "is-enabled", "--", service])
            return json.dumps({"service": service, "enabled": out.strip()}, ensure_ascii=False)

        else:
            return json.dumps({"error": f"Unknown action: {action}"}, ensure_ascii=False)

    except Exception as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False)


def _parse_status_output(out: str, service: str) -> dict:
    """Parse 'systemctl status' output into structured fields."""
    result = {
        "service": service,
        "active": None,
        "pid": None,
        "uptime": None,
        "memory": None,
        "cpu": None,
        "status_lines": [],
    }
    for line in out.splitlines():
        stripped = line.strip()
        if "Active:" in stripped and "ago" in stripped:
            result["active"] = stripped
        elif "Main PID:" in stripped:
            import re
            m = re.search(r"Main PID:\s*(\d+)", stripped)
            if m:
                result["pid"] = int(m.group(1))
        elif "Memory:" in stripped:
            result["memory"] = stripped
        elif "CPU:" in stripped:
            result["cpu"] = stripped
        result["status_lines"].append(stripped)

    if result["active"] is None and out:
        result["active"] = "unknown (parse failed)"
    return result


def _check_systemctl() -> bool:
    """Check if sudo systemctl is available on this system."""
    try:
        r = subprocess.run(
            ["sudo", "-n", "systemctl", "--version"],
            capture_output=True, text=True, timeout=5,
        )
        return r.returncode == 0
    except Exception:
        return False


# --- Registry ---
from tools.registry import registry, tool_error

registry.register(
    name="systemctl",
    toolset="admin",
    schema=SYSTEMCTL_SCHEMA,
    handler=systemctl_tool,
    check_fn=_check_systemctl,
    emoji="⚙️",
)
