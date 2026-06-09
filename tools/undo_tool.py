"""
Multi-Step Undo — restore files to a previous checkpoint.

Provides LLM access to CheckpointManager's snapshot store so the agent
can undo file mutations across multiple tool calls. Supports listing
available checkpoints, viewing diffs, and restoring to any checkpoint.

Usage:
  undo_checkpoint(action='list', workdir='/path')       — list checkpoints
  undo_checkpoint(action='diff', hash='abc123', ...)    — show diff
  undo_checkpoint(action='restore', hash='abc123', ...) — restore files
"""

import json
import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

UNDO_SCHEMA = {
    "name": "undo_checkpoint",
    "description": (
        "List, diff, or restore filesystem checkpoints. "
        "Checkpoints are automatic git snapshots taken before each file-mutating "
        "operation (write_file, patch, destructive terminal commands). "
        "Use action='list' to see available snapshots (most recent first), "
        "action='diff' to see what changed since a checkpoint, "
        "or action='restore' to roll back to a previous state. "
        "A pre-rollback snapshot is taken automatically, so restore is undoable."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["list", "diff", "restore"],
                "description": (
                    "'list' — show available checkpoints with hashes and reasons. "
                    "'diff' — show git diff between a checkpoint and current state. "
                    "'restore' — roll back files to a checkpoint state."
                ),
            },
            "hash": {
                "type": "string",
                "description": "Checkpoint hash (full or short SHA). Required for 'diff' and 'restore'. Omit for 'list'.",
            },
            "workdir": {
                "type": "string",
                "description": (
                    "Working directory path. Defaults to current working directory "
                    "(os.getcwd()). Pass explicitly when working outside the current dir."
                ),
            },
            "file_path": {
                "type": "string",
                "description": "Restore only this specific file (relative path). Optional for 'restore'. Ignored for 'list' and 'diff'.",
            },
        },
        "required": ["action"],
    },
}


def _get_workdir(args: dict) -> str:
    """Resolve workdir from args or default."""
    return args.get("workdir") or os.getenv("TERMINAL_CWD") or os.getcwd()


def undo_checkpoint_handler(args: dict[str, Any], **kw) -> str:
    """Handle undo_checkpoint tool calls."""
    from tools.checkpoint_manager import CheckpointManager, CHECKPOINT_BASE

    action = args.get("action", "list")
    workdir = _get_workdir(args)
    commit_hash = args.get("hash", "")
    file_path = args.get("file_path")

    mgr = CheckpointManager(enabled=True, max_snapshots=50)

    if action == "list":
        checkpoints = mgr.list_checkpoints(workdir)
        if not checkpoints:
            return json.dumps({
                "success": False,
                "message": f"No checkpoints found for {workdir}. Checkpoints are created automatically during this session before file-write and destructive terminal operations.",
                "workdir": workdir,
            }, ensure_ascii=False)

        result = {
            "success": True,
            "workdir": workdir,
            "count": len(checkpoints),
            "checkpoints": [
                {
                    "hash": cp["hash"],
                    "short_hash": cp["short_hash"],
                    "timestamp": cp["timestamp"],
                    "reason": cp["reason"],
                    "files_changed": cp.get("files_changed", 0),
                    "insertions": cp.get("insertions", 0),
                    "deletions": cp.get("deletions", 0),
                }
                for cp in checkpoints
            ],
            "usage": 'Use undo_checkpoint(action="diff", hash="<short_hash>", workdir="...") to see changes, or undo_checkpoint(action="restore", hash="<short_hash>", workdir="...") to roll back.',
        }
        return json.dumps(result, ensure_ascii=False, default=str)

    if action == "diff":
        if not commit_hash:
            return json.dumps({"success": False, "error": "'hash' is required for diff action"}, ensure_ascii=False)

        diff_result = mgr.diff(workdir, commit_hash)
        if not diff_result.get("success"):
            return json.dumps(diff_result, ensure_ascii=False)

        return json.dumps({
            "success": True,
            "workdir": workdir,
            "checkpoint": commit_hash,
            "stat": diff_result.get("stat", ""),
            "diff": diff_result.get("diff", ""),
            "note": "Lines with '+' were added since this checkpoint; lines with '-' were removed. Restore will revert these changes.",
        }, ensure_ascii=False, default=str)

    if action == "restore":
        if not commit_hash:
            return json.dumps({"success": False, "error": "'hash' is required for restore action"}, ensure_ascii=False)

        restore_kwargs = {}
        if file_path:
            restore_kwargs["file_path"] = file_path

        result = mgr.restore(workdir, commit_hash, **restore_kwargs)
        if not result.get("success"):
            return json.dumps(result, ensure_ascii=False)

        return json.dumps({
            "success": True,
            "message": f"Restored files in {result['directory']} to checkpoint {result['restored_to']} ({result['reason']})",
            "restored_to": result["restored_to"],
            "reason": result["reason"],
            "directory": result["directory"],
            "note": "A pre-rollback snapshot was taken automatically. Use undo_checkpoint(action='list') to see it.",
        }, ensure_ascii=False, default=str)

    return json.dumps({"success": False, "error": f"Unknown action: {action}"}, ensure_ascii=False)


def check_undo() -> bool:
    """Always available (fallback to empty result if no checkpoints)."""
    return True


# --- Registry ---
from tools.registry import registry

registry.register(
    name="undo_checkpoint",
    toolset="admin",
    schema=UNDO_SCHEMA,
    handler=undo_checkpoint_handler,
    check_fn=check_undo,
    emoji="⏪",
)
