"""Security regression tests for operator example helpers."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

SDK_ROOT = Path(__file__).resolve().parents[2]


def _load_settle_module():
    path = (
        Path(__file__).resolve().parents[1] / "examples" / "agent-server" / "scripts" / "settle.py"
    )
    spec = importlib.util.spec_from_file_location("agent_server_settle", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_settle_env_file_rejects_parent_traversal():
    settle = _load_settle_module()
    with pytest.raises(ValueError, match="bare file name"):
        settle.resolve_env_file("../other-project/.env")


def test_settle_env_file_rejects_symlink_escape(tmp_path, monkeypatch):
    settle = _load_settle_module()
    root = tmp_path / "agent-server"
    root.mkdir()
    outside = tmp_path / "secret.env"
    outside.write_text("PRIVATE_KEY=secret")
    (root / ".env.link").symlink_to(outside)
    monkeypatch.setattr(settle, "ROOT", root)

    with pytest.raises(ValueError, match="resolves outside"):
        settle.resolve_env_file(".env.link")


def test_settle_env_file_accepts_bare_name(tmp_path, monkeypatch):
    settle = _load_settle_module()
    monkeypatch.setattr(settle, "ROOT", tmp_path)
    assert settle.resolve_env_file(".env.qa") == tmp_path / ".env.qa"


def test_examples_do_not_ship_a_default_wallet_password():
    for examples in (
        SDK_ROOT / "python" / "examples",
        SDK_ROOT / "typescript" / "examples",
    ):
        for path in examples.rglob("*"):
            if path.is_file() and not {".venv", "node_modules", "dist"}.intersection(
                path.parts
            ):
                assert "demo-password" not in path.read_text(errors="ignore"), path


def test_direct_search_is_opt_in_loopback_only_and_hides_errors():
    for relative in (
        "python/examples/agent-server/src/service.py",
        "python/examples/agent-server/src/service_mount.py",
    ):
        source = (SDK_ROOT / relative).read_text()
        assert source.index("if ENABLE_DEBUG_SEARCH:") < source.index(
            '@app.post("/search"'
        )
        assert 'HOST = os.getenv("HOST", "127.0.0.1")' in source
        assert 'detail="Search failed"' in source

    source = (
        SDK_ROOT / "typescript/examples/agent-server/src/service.ts"
    ).read_text()
    assert 'const HOST = process.env.HOST ?? "127.0.0.1"' in source
    assert "const searchRoutes = ENABLE_DEBUG_SEARCH" in source
    assert '{ error: "Search failed" }' in source


def test_per_ip_limit_runs_before_global_limit():
    checks = (
        (
            "python/examples/agent-server/src/erc8183_server.py",
            "negotiate_limiter.check(client_ip)",
            'global_negotiate_limiter.check("global")',
        ),
        (
            "python/examples/a2a-agent/src/server.py",
            "negotiate_limiter.check(client_ip)",
            'global_negotiate_limiter.check("global")',
        ),
        (
            "typescript/examples/agent-server/src/erc8183Server.ts",
            "negotiateLimiter.check(clientIp);",
            'globalNegotiateLimiter.check("global");',
        ),
        (
            "typescript/examples/a2a-agent/src/server.ts",
            "negotiateLimiter.check(clientIp);",
            'globalNegotiateLimiter.check("global");',
        ),
    )
    for relative, per_ip, global_limit in checks:
        source = (SDK_ROOT / relative).read_text()
        assert source.index(per_ip) < source.index(global_limit)


def test_typescript_example_verifies_quote_before_funding():
    source = (
        SDK_ROOT / "typescript/examples/a2a-agent/scripts/buyer.ts"
    ).read_text()
    assert source.index("await verifyQuoteSignature({") < source.index(
        "await client.createJob({"
    )
