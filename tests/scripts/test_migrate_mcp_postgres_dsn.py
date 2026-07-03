from pathlib import Path

import pytest

from scripts.migrate_mcp_postgres_dsn import migrate


def test_moves_dsn_to_env_without_returning_secret(tmp_path: Path):
    config = tmp_path / "config.yaml"
    env = tmp_path / ".env"
    config.write_text(
        "mcp_servers:\n  postgres:\n    args:\n      - postgresql://user:secret@db/app\n",
        encoding="utf-8",
    )
    env.write_text("OTHER=value\n", encoding="utf-8")

    assert migrate(config, env) == "migrated"
    assert "${MCP_POSTGRES_DSN}" in config.read_text(encoding="utf-8")
    assert "postgresql://user:secret@db/app" not in config.read_text(encoding="utf-8")
    assert "MCP_POSTGRES_DSN=postgresql://user:secret@db/app" in env.read_text(encoding="utf-8")
    assert migrate(config, env) == "already_migrated"


def test_dry_run_does_not_change_files(tmp_path: Path):
    config = tmp_path / "config.yaml"
    env = tmp_path / ".env"
    original = "args:\n  - postgres://user:secret@db/app\n"
    config.write_text(original, encoding="utf-8")
    assert migrate(config, env, dry_run=True) == "would_migrate"
    assert config.read_text(encoding="utf-8") == original
    assert not env.exists()


def test_refuses_ambiguous_multiple_dsns(tmp_path: Path):
    config = tmp_path / "config.yaml"
    env = tmp_path / ".env"
    config.write_text(
        "args:\n  - postgres://one:secret@db/app\n  - postgres://two:secret@db/app\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="Expected one"):
        migrate(config, env)
