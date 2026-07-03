#!/usr/bin/env python3
"""Move a literal PostgreSQL MCP DSN from config.yaml into the private .env.

The secret is never accepted as a CLI argument or printed. The migration is
idempotent and uses atomic replacement for both files.
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import tempfile
import time
from pathlib import Path

_DSN_LINE = re.compile(r"^(?P<indent>\s*-\s*)(?P<dsn>postgres(?:ql)?://\S+)\s*$", re.MULTILINE)
_ENV_NAME = "MCP_POSTGRES_DSN"


def _atomic_write(path: Path, content: str, mode: int | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        if mode is not None:
            os.chmod(temporary, mode)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def migrate(config_path: Path, env_path: Path, dry_run: bool = False) -> str:
    config = config_path.read_text(encoding="utf-8")
    matches = list(_DSN_LINE.finditer(config))
    if not matches:
        if f"${{{_ENV_NAME}}}" in config:
            return "already_migrated"
        raise ValueError("No literal PostgreSQL DSN found in config")
    if len(matches) != 1:
        raise ValueError(f"Expected one literal PostgreSQL DSN, found {len(matches)}")

    match = matches[0]
    dsn = match.group("dsn")
    env_text = env_path.read_text(encoding="utf-8") if env_path.exists() else ""
    env_lines = env_text.splitlines()
    replacement = f"{_ENV_NAME}={dsn}"
    found = False
    for index, line in enumerate(env_lines):
        if line.startswith(f"{_ENV_NAME}="):
            env_lines[index] = replacement
            found = True
            break
    if not found:
        env_lines.append(replacement)
    updated_env = "\n".join(env_lines).rstrip() + "\n"
    updated_config = config[: match.start()] + match.group("indent") + f"${{{_ENV_NAME}}}" + config[match.end() :]

    if dry_run:
        return "would_migrate"

    stamp = time.strftime("%Y%m%d-%H%M%S")
    shutil.copy2(config_path, config_path.with_name(f"{config_path.name}.before-dsn-{stamp}.bak"))
    if env_path.exists():
        shutil.copy2(env_path, env_path.with_name(f"{env_path.name}.before-dsn-{stamp}.bak"))
    _atomic_write(env_path, updated_env, 0o600)
    _atomic_write(config_path, updated_config, config_path.stat().st_mode & 0o777)
    return "migrated"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--env", type=Path, required=True)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    print(migrate(args.config, args.env, args.dry_run))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
