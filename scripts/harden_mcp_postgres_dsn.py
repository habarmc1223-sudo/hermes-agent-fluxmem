#!/usr/bin/env python3
"""Remove the PostgreSQL password from the MCP process argument.

Reads MCP_POSTGRES_DSN from a private env file, moves its password to
PGPASSWORD, and writes a password-free connection URI back. No secret is
accepted as an argument or printed.
"""

from __future__ import annotations

import argparse
import os
import shutil
import tempfile
import time
from pathlib import Path
from urllib.parse import quote, unquote, urlsplit, urlunsplit


def _atomic_write(path: Path, content: str) -> None:
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def harden(env_path: Path, dry_run: bool = False) -> str:
    lines = env_path.read_text(encoding="utf-8").splitlines()
    values: dict[str, tuple[int, str]] = {}
    for index, line in enumerate(lines):
        if not line or line.lstrip().startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key] = (index, value)

    dsn_entry = values.get("MCP_POSTGRES_DSN")
    if not dsn_entry:
        raise ValueError("MCP_POSTGRES_DSN is not configured")
    dsn_index, dsn = dsn_entry
    parsed = urlsplit(dsn)
    if parsed.scheme not in {"postgres", "postgresql"} or not parsed.hostname:
        raise ValueError("MCP_POSTGRES_DSN is not a valid PostgreSQL URI")
    if parsed.password is None:
        return "already_hardened"

    username = quote(unquote(parsed.username or ""), safe="")
    host = f"[{parsed.hostname}]" if ":" in parsed.hostname else parsed.hostname
    port = f":{parsed.port}" if parsed.port else ""
    userinfo = f"{username}@" if username else ""
    safe_dsn = urlunsplit((parsed.scheme, f"{userinfo}{host}{port}", parsed.path, parsed.query, parsed.fragment))
    password = unquote(parsed.password)

    lines[dsn_index] = f"MCP_POSTGRES_DSN={safe_dsn}"
    pgpassword = values.get("PGPASSWORD")
    if pgpassword:
        lines[pgpassword[0]] = f"PGPASSWORD={password}"
    else:
        lines.append(f"PGPASSWORD={password}")

    if dry_run:
        return "would_harden"
    stamp = time.strftime("%Y%m%d-%H%M%S")
    shutil.copy2(env_path, env_path.with_name(f"{env_path.name}.before-pgpassword-{stamp}.bak"))
    _atomic_write(env_path, "\n".join(lines).rstrip() + "\n")
    return "hardened"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--env", type=Path, required=True)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    print(harden(args.env, args.dry_run))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
