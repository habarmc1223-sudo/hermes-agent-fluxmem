from pathlib import Path

from scripts.harden_mcp_postgres_dsn import harden


def test_moves_password_out_of_uri(tmp_path: Path):
    env = tmp_path / ".env"
    env.write_text(
        "MCP_POSTGRES_DSN=postgresql://user:p%40ss@127.0.0.1:5432/app?sslmode=disable\n",
        encoding="utf-8",
    )
    assert harden(env) == "hardened"
    result = env.read_text(encoding="utf-8")
    assert "MCP_POSTGRES_DSN=postgresql://user@127.0.0.1:5432/app?sslmode=disable" in result
    assert "PGPASSWORD=p@ss" in result
    assert "p%40ss@" not in result
    assert harden(env) == "already_hardened"


def test_dry_run_is_non_mutating(tmp_path: Path):
    env = tmp_path / ".env"
    original = "MCP_POSTGRES_DSN=postgres://u:p@db/app\n"
    env.write_text(original, encoding="utf-8")
    assert harden(env, dry_run=True) == "would_harden"
    assert env.read_text(encoding="utf-8") == original
