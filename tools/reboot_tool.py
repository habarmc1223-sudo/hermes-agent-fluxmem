"""
Scheduled Reboot Tool — schedule a server reboot via one-shot cron job.
Requires explicit user confirmation before scheduling.

Usage: schedule_reboot(delay_seconds=60, reason="Scheduled maintenance")
"""

import json
import logging

logger = logging.getLogger(__name__)

SCHEDULE_REBOOT_SCHEMA = {
    "name": "schedule_reboot",
    "description": (
        "Schedule a server reboot after a delay. "
        "Always requires explicit user confirmation before the reboot is scheduled. "
        "Returns: {'scheduled': True, 'delay_seconds': N, 'job_id': '...'} on success, "
        "or {'scheduled': False, 'status': 'approval_required'} if user has not confirmed."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "delay_seconds": {
                "type": "integer",
                "description": "Seconds to wait before reboot (default: 60, range: 10-600). Use 10 for urgent, 120+ for graceful.",
                "default": 60,
                "minimum": 10,
                "maximum": 600,
            },
            "reason": {
                "type": "string",
                "description": "Optional human-readable reason for the reboot (appears in the confirmation message).",
                "default": "",
            },
            "confirmed": {
                "type": "boolean",
                "description": "Must be explicitly set to true by the user to proceed. If false or absent, returns a confirmation prompt.",
                "default": False,
            },
        },
        "required": [],
    },
}


def _schedule_reboot_via_cron(delay_seconds: int) -> dict:
    """Schedule a one-shot cron job that reboots after delay_seconds."""
    from cron.jobs import create_job
    import datetime

    now = datetime.datetime.now()
    run_at = now + datetime.timedelta(seconds=delay_seconds)
    cron_expr = f"{run_at.minute} {run_at.hour} {run_at.day} {run_at.month} *"

    job = create_job(
        schedule=cron_expr,
        prompt="sudo /sbin/shutdown -r +1",
        name=f"reboot-{run_at.strftime('%H%M%S')}",
        repeat=1,
        # no_agent=True removed: scheduled job goes through normal approval at execution time
    )
    return {
        "scheduled": True,
        "delay_seconds": delay_seconds,
        "job_id": job.get("id", "unknown"),
        "run_at": run_at.isoformat(),
    }


def schedule_reboot_tool(args, **kw):
    """Handle schedule_reboot tool calls."""
    delay = int(args.get("delay_seconds", 60))
    reason = args.get("reason", "")
    confirmed = args.get("confirmed", False)

    delay = max(10, min(600, delay))

    # Require explicit user confirmation before scheduling
    if not confirmed:
        msg = f"⚠️ Reboot requested"
        if reason:
            msg += f" — {reason}"
        msg += f"\n\nThis will reboot the server in {delay} seconds. To confirm, call schedule_reboot again with confirmed=true."
        return json.dumps({
            "scheduled": False,
            "status": "approval_required",
            "message": msg,
            "delay_seconds": delay,
        }, ensure_ascii=False)

    try:
        result = _schedule_reboot_via_cron(delay)
        msg = f"🔁 Reboot scheduled in {delay}s"
        if reason:
            msg += f" — {reason}"
        result["message"] = msg
        logger.warning("Reboot scheduled: delay=%ds reason=%r job_id=%s", delay, reason, result.get("job_id"))
        return json.dumps(result, ensure_ascii=False)
    except Exception as e:
        return json.dumps({
            "scheduled": False,
            "error": str(e),
            "message": "Failed to schedule reboot via cron. Try manual: sudo reboot",
        }, ensure_ascii=False)


# --- Registry ---
from tools.registry import registry, tool_error

registry.register(
    name="schedule_reboot",
    toolset="admin",
    schema=SCHEDULE_REBOOT_SCHEMA,
    handler=schedule_reboot_tool,
    check_fn=None,
    emoji="🔁",
)
