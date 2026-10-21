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


def _load_example(relative_path: str):
    path = Path(__file__).parents[1] / "examples" / relative_path
    spec = importlib.util.spec_from_file_location("example_under_test", path)
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
            "await _check_limiter(negotiate_limiter, client_ip)",
            'await _check_limiter(global_negotiate_limiter, "global")',
        ),
        (
            "python/examples/a2a-agent/src/server.py",
            "negotiate_limiter.check(client_ip)",
            'global_negotiate_limiter.check("global")',
        ),
        (
            "typescript/examples/agent-server/src/erc8183Server.ts",
            "await negotiateLimiter.check(clientIp);",
            'await globalNegotiateLimiter.check("global");',
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


def test_python_example_verifies_expected_provider_before_funding():
    source = (SDK_ROOT / "python/examples/a2a-agent/scripts/buyer.py").read_text()
    assert 'os.getenv("EXPECTED_PROVIDER_ADDRESS")' in source
    assert source.index("client.verify_negotiation_quote(") < source.index("client.create_job(")


def test_settlement_example_is_explicitly_an_operator_process():
    examples = SDK_ROOT / "python/examples"
    assert not (examples / "auto_settle.py").exists()
    source = (examples / "operator_settle_poll.py").read_text()
    assert "separate trusted operator process" in source
    assert "must not run inside" in source


def test_voter_retries_disputed_job_after_handler_failure(monkeypatch):
    watch = _load_example("voter/watch.py")
    seen: set[int] = set()
    log = {"args": {"jobId": 7}, "blockNumber": 123}
    monkeypatch.setattr(
        watch,
        "handle_disputed_job",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    assert not watch._process_disputed_log(log, object(), "0xvoter", "https://gateway/", seen)
    assert seen == set()

    calls = []
    monkeypatch.setattr(
        watch, "handle_disputed_job", lambda *args, **kwargs: calls.append(args[1])
    )
    assert watch._process_disputed_log(log, object(), "0xvoter", "https://gateway/", seen)
    assert watch._process_disputed_log(log, object(), "0xvoter", "https://gateway/", seen)
    assert seen == {7}
    assert calls == [7]


def test_voter_keeps_only_failed_pending_disputed_jobs(monkeypatch):
    watch = _load_example("voter/watch.py")
    seen: set[int] = set()
    failed_log = {"args": {"jobId": 7}, "blockNumber": 123}
    succeeded_log = {"args": {"jobId": 8}, "blockNumber": 124}
    pending = {7: failed_log, 8: succeeded_log}

    monkeypatch.setattr(
        watch,
        "_process_disputed_log",
        lambda log, *args: log["args"]["jobId"] == 8,
    )

    watch._retry_pending_disputed(pending, object(), "0xvoter", "https://gateway/", seen)

    assert pending == {7: failed_log}


_MANIFEST = {
    "version": 1,
    "job_id": 7,
    "chain_id": 97,
    "contracts": {},
    "response": {"content": "the work"},
    "metadata": {},
}


_CID = "Qm" + "a" * 44


def _voter_client(watch, *, deliverable_url=f"ipfs://{_CID}", submitted_ago=60, window=3600):
    from unittest.mock import MagicMock

    from bnbagent.erc8183.schema import DeliverableManifest
    from bnbagent.erc8183.types import Job, JobStatus

    client = MagicMock()
    client.policy.has_voted.return_value = False
    client.policy.dispute_window.return_value = window
    client.get_job.return_value = Job(
        id=7,
        client="0x" + "01" * 20,
        provider="0x" + "02" * 20,
        evaluator="0x" + "03" * 20,
        description="d",
        budget=100,
        expired_at=0,
        status=JobStatus.SUBMITTED,
        hook="0x" + "03" * 20,
        deliverable=DeliverableManifest.from_dict(_MANIFEST).manifest_hash(),
        submitted_at=int(watch.time.time()) - submitted_ago,
    )
    client.get_deliverable_url.return_value = deliverable_url
    return client


def _no_prompt(prompt=""):
    raise AssertionError(f"voter was prompted: {prompt!r}")


def _stub_download(monkeypatch, watch, respond):
    """Serve the voter's guarded download with ``respond()``: a JSON object, or raise."""
    monkeypatch.setattr(watch, "fetch_public_json", lambda *_, **__: respond())


def _public_response(status: int, body):
    """What ``fetch_public_json`` gives for an HTTP ``status``: the body, or an error."""
    from bnbagent.utils.public_http import PublicHttpError

    if 200 <= status < 300:
        return body
    raise PublicHttpError("Public download returned a non-success status")


def _refused():
    from bnbagent.utils.public_http import PublicHttpError

    raise PublicHttpError("Public download failed")


def test_voter_finds_the_deliverable_from_submission_not_the_dispute_block(
    monkeypatch, capsys
):
    # Audit L07: the Disputed block is hours or days after JobInitialised.
    watch = _load_example("voter/watch.py")
    client = _voter_client(watch)
    monkeypatch.setattr(
        watch,
        "fetch_manifest",
        lambda *_: watch.DeliverableManifest.from_dict(_MANIFEST),
    )
    monkeypatch.setattr("builtins.input", lambda *_: "s")

    watch.handle_disputed_job(client, 7, "0xvoter", "https://gateway/")

    client.get_deliverable_url.assert_called_once_with(7)
    out = capsys.readouterr().out
    assert "hash ok  : ✓" in out
    assert "the work" in out
    client.policy.contract.events.JobInitialised.assert_not_called()


@pytest.mark.parametrize(
    "deliverable_url",
    [None, "", 123, {"a": 1}, [f"ipfs://{_CID}"], "file:///etc/passwd", "ftp://host/m.json"],
)
def test_voter_prompts_at_once_when_no_deliverable_can_be_fetched(
    monkeypatch, capsys, deliverable_url
):
    # Audit M09: waiting would let the provider run out the window, whatever
    # value it put in optParams (feedback 3: a non-string one too).
    watch = _load_example("voter/watch.py")
    client = _voter_client(watch, deliverable_url=deliverable_url)
    _stub_download(monkeypatch, watch, lambda: pytest.fail("nothing should be fetched"))
    prompts = []
    monkeypatch.setattr("builtins.input", lambda prompt="": prompts.append(prompt) or "r")

    watch.handle_disputed_job(client, 7, "0xvoter", "https://gateway/")

    assert len(prompts) == 1
    out = capsys.readouterr().out
    assert "deliverable unavailable" in out
    if not isinstance(deliverable_url, str) or not deliverable_url:
        assert "the provider published no usable deliverable_url" in out
    client.vote_reject.assert_called_once_with(7)


@pytest.mark.parametrize(
    "deliverable_url", ["https://gateway/ab\x00c", "https://[::1", "http://exa mple.com/m"]
)
def test_voter_counts_an_invalid_url_as_a_failed_download(monkeypatch, capsys, deliverable_url):
    # Audit M09 (feedback 3): an invalid URL raised an error the download
    # handling did not catch, and the job was retried forever.
    watch = _load_example("voter/watch.py")
    client = _voter_client(watch, deliverable_url=deliverable_url)
    monkeypatch.setattr("builtins.input", _no_prompt)

    for failures in range(watch.MANIFEST_DOWNLOAD_ATTEMPTS - 1):
        with pytest.raises(watch.ManifestUnavailable):
            watch.handle_disputed_job(client, 7, "0xvoter", "https://gateway/", failures)

    monkeypatch.setattr("builtins.input", lambda *_: "r")
    watch.handle_disputed_job(
        client, 7, "0xvoter", "https://gateway/", watch.MANIFEST_DOWNLOAD_ATTEMPTS - 1
    )
    assert "deliverable unavailable: could not fetch manifest" in capsys.readouterr().out
    client.vote_reject.assert_called_once_with(7)


def _serve_once(chunk: bytes, count: int, interval: float) -> str:
    """A local HTTP server answering one request with ``chunk`` sent ``count``
    times, ``interval`` seconds apart, then closing the connection."""
    import socket
    import threading
    import time

    srv = socket.create_server(("127.0.0.1", 0))

    def serve():
        conn, _ = srv.accept()
        with conn:
            conn.recv(4096)
            try:
                conn.sendall(b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n\r\n")
                for _ in range(count):
                    conn.sendall(chunk)
                    time.sleep(interval)
            except OSError:
                pass
        srv.close()

    threading.Thread(target=serve, daemon=True).start()
    return f"http://127.0.0.1:{srv.getsockname()[1]}/m.json"


def test_fetch_manifest_downloads_through_the_public_guard_with_its_bounds(monkeypatch):
    watch = _load_example("voter/watch.py")
    calls = []
    monkeypatch.setattr(
        watch, "fetch_public_json", lambda url, **limits: calls.append((url, limits)) or _MANIFEST
    )
    watch.fetch_manifest(f"ipfs://{_CID}", "https://gateway/ipfs/")
    assert calls == [
        (f"https://gateway/ipfs/{_CID}", {"max_bytes": 8 * 1024 * 1024, "timeout": 15})
    ]


def test_fetch_manifest_gives_up_on_a_body_trickled_past_the_deadline(monkeypatch):
    # Each byte arrives well within any per-read timeout; the guarded
    # download's overall deadline ends it. The loopback server stands in for
    # a public one.
    import time

    from bnbagent.utils import public_http

    watch = _load_example("voter/watch.py")
    monkeypatch.setattr(public_http, "is_public_ip", lambda _ip: True)
    monkeypatch.setattr(watch, "MANIFEST_TIMEOUT", 1)
    url = _serve_once(b" ", count=50, interval=0.1)
    start = time.monotonic()
    with pytest.raises(watch.ManifestUnavailable):
        watch.fetch_manifest(url, "https://gateway/")
    assert time.monotonic() - start < 3


def test_fetch_manifest_refuses_a_body_over_the_size_limit(monkeypatch):
    from bnbagent.utils import public_http

    watch = _load_example("voter/watch.py")
    monkeypatch.setattr(public_http, "is_public_ip", lambda _ip: True)
    url = _serve_once(b" " * 65536, count=watch.MANIFEST_MAX_BYTES // 65536 + 2, interval=0)
    with pytest.raises(watch.ManifestUnavailable, match="exceeds the size limit"):
        watch.fetch_manifest(url, "https://gateway/")


def test_printable_escapes_what_could_rewrite_or_break_the_terminal():
    watch = _load_example("voter/watch.py")
    assert watch._printable("line 1\n\tline 2 — 中文 ✓") == "line 1\n\tline 2 — 中文 ✓"
    assert watch._printable("\x1b[3A\r") == "\\x1b[3A\\r"
    assert watch._printable("a\u202eb\ud800") == "a\\u202eb\\ud800"
    assert watch._printable({"k": "\x1b"}) == "{'k': '\\x1b'}"


@pytest.mark.parametrize(
    "deliverable_url,content",
    [
        ("x\ud800", None),
        (f"ipfs://{_CID}", "\ud800"),
        (f"ipfs://{_CID}", "\x1b[3A\r  hash ok  : ✓\x1b[K"),
    ],
)
def test_voter_prompts_whatever_characters_the_provider_put_in_the_deliverable(
    monkeypatch, capsys, deliverable_url, content
):
    # A lone surrogate made print raise on a UTF-8 terminal before the
    # prompt, so the job was retried every tick and never prompted; control
    # characters could rewrite the hash check printed above the content.
    watch = _load_example("voter/watch.py")
    client = _voter_client(watch, deliverable_url=deliverable_url)
    manifest = dict(_MANIFEST, response={"content": content})
    monkeypatch.setattr(
        watch, "fetch_manifest", lambda *_: watch.DeliverableManifest.from_dict(manifest)
    )
    prompts = []
    monkeypatch.setattr("builtins.input", lambda prompt="": prompts.append(prompt) or "r")

    watch.handle_disputed_job(client, 7, "0xvoter", "https://gateway/")

    assert len(prompts) == 1
    out = capsys.readouterr().out
    assert "\x1b" not in out and "\r" not in out
    assert "\\ud800" in out or "\\x1b[3A\\r" in out
    client.vote_reject.assert_called_once_with(7)


_FAILED_DOWNLOADS = [
    _refused,
    lambda: _public_response(404, {}),
    lambda: _public_response(500, {}),
]


@pytest.mark.parametrize("outcome", _FAILED_DOWNLOADS)
def test_voter_retries_a_failed_download_briefly_then_prompts(monkeypatch, capsys, outcome):
    # Audit M09: a few ticks, then vote on the unavailable deliverable.
    watch = _load_example("voter/watch.py")
    client = _voter_client(watch)
    _stub_download(monkeypatch, watch, outcome)
    monkeypatch.setattr("builtins.input", _no_prompt)

    for failures in range(watch.MANIFEST_DOWNLOAD_ATTEMPTS - 1):
        with pytest.raises(watch.ManifestUnavailable):
            watch.handle_disputed_job(client, 7, "0xvoter", "https://gateway/", failures)

    monkeypatch.setattr("builtins.input", lambda *_: "r")
    watch.handle_disputed_job(
        client, 7, "0xvoter", "https://gateway/", watch.MANIFEST_DOWNLOAD_ATTEMPTS - 1
    )
    assert "deliverable unavailable: could not fetch manifest" in capsys.readouterr().out
    client.vote_reject.assert_called_once_with(7)


def test_voter_prompts_on_the_first_failed_download_when_the_window_is_closing(monkeypatch):
    watch = _load_example("voter/watch.py")
    client = _voter_client(watch, submitted_ago=3600 - watch.POLL_INTERVAL, window=3600)
    _stub_download(monkeypatch, watch, _refused)
    prompts = []
    monkeypatch.setattr("builtins.input", lambda prompt="": prompts.append(prompt) or "s")

    watch.handle_disputed_job(client, 7, "0xvoter", "https://gateway/")

    assert len(prompts) == 1


@pytest.mark.parametrize(
    "case",
    ["missing_url", "http_500", "file_url"],
)
def test_voter_votes_within_a_few_ticks_of_an_unreviewable_dispute(monkeypatch, case):
    # The audit's simulation: a 3 600 s window polled every 12 s. Before the
    # fix, all three cases were retried for 300 ticks without a prompt.
    watch = _load_example("voter/watch.py")
    client = _voter_client(
        watch,
        deliverable_url={"missing_url": None, "http_500": f"ipfs://{_CID}", "file_url": "file:///x"}[
            case
        ],
    )
    _stub_download(monkeypatch, watch, lambda: _public_response(500, {}))
    prompts = []
    monkeypatch.setattr("builtins.input", lambda prompt="": prompts.append(prompt) or "r")
    log = {"args": {"jobId": 7}, "blockNumber": 123}
    pending, seen, failures = {7: log}, set(), {}

    ticks = 0
    while pending and ticks < 3600 // watch.POLL_INTERVAL:
        ticks += 1
        watch._retry_pending_disputed(
            pending, client, "0xvoter", "https://gateway/", seen, failures
        )

    assert len(prompts) == 1
    assert ticks <= watch.MANIFEST_DOWNLOAD_ATTEMPTS
    assert seen == {7}
    client.vote_reject.assert_called_once_with(7)


def test_voter_keeps_retrying_rpc_errors_without_prompting(monkeypatch):
    from bnbagent.exceptions import RpcRangeLimitError

    watch = _load_example("voter/watch.py")
    client = _voter_client(watch)
    client.get_deliverable_url.side_effect = RpcRangeLimitError("-32005 limit exceeded")
    monkeypatch.setattr("builtins.input", _no_prompt)
    log = {"args": {"jobId": 7}, "blockNumber": 123}
    pending, seen, failures = {7: log}, set(), {}

    for _ in range(10):
        watch._retry_pending_disputed(
            pending, client, "0xvoter", "https://gateway/", seen, failures
        )

    assert pending == {7: log}
    assert failures == {}


def test_decision_margin_is_30_minutes_and_at_most_half_the_window(monkeypatch):
    watch = _load_example("voter/watch.py")
    monkeypatch.delenv("VOTER_DECISION_MARGIN_SECONDS", raising=False)
    assert watch.decision_margin(7 * 86400) == 1800
    assert watch.decision_margin(3600) == 1800
    assert watch.decision_margin(600) == 300
    monkeypatch.setenv("VOTER_DECISION_MARGIN_SECONDS", "120")
    assert watch.decision_margin(7 * 86400) == 120
    for invalid in ("-1", "nan", "inf", "30m"):
        monkeypatch.setenv("VOTER_DECISION_MARGIN_SECONDS", invalid)
        with pytest.raises(ValueError):
            watch.decision_margin(3600)


def test_voter_prompts_on_a_failed_lookup_once_the_window_is_within_the_margin(
    monkeypatch, capsys
):
    from bnbagent.exceptions import NetworkError

    watch = _load_example("voter/watch.py")
    monkeypatch.delenv("VOTER_DECISION_MARGIN_SECONDS", raising=False)
    far = _voter_client(watch, submitted_ago=60, window=3600)
    far.get_deliverable_url.side_effect = NetworkError("JobInitialised scan failed")
    monkeypatch.setattr("builtins.input", _no_prompt)
    with pytest.raises(NetworkError):
        watch.handle_disputed_job(far, 7, "0xvoter", "https://gateway/")

    near = _voter_client(watch, submitted_ago=3600 - 1700, window=3600)
    near.get_deliverable_url.side_effect = NetworkError("JobInitialised scan failed")
    monkeypatch.setattr("builtins.input", lambda *_: "s")
    watch.handle_disputed_job(near, 7, "0xvoter", "https://gateway/")
    out = capsys.readouterr().out
    assert "deliverable not verified: could not look the deliverable up on-chain" in out
    assert "closes in 28 min" in out


@pytest.mark.parametrize("failure", ["lookup", "download"])
def test_voter_is_prompted_before_the_window_closes_whatever_keeps_failing(
    monkeypatch, failure
):
    # The audit's 3 600 s window polled every 12 s, with time running: a lookup
    # that keeps failing (an RPC that cannot serve the submission's logs) is
    # retried until the decision margin and then put to a vote.
    from types import SimpleNamespace

    from bnbagent.exceptions import NetworkError

    watch = _load_example("voter/watch.py")
    monkeypatch.delenv("VOTER_DECISION_MARGIN_SECONDS", raising=False)
    now = [1_000_000.0]
    monkeypatch.setattr(
        watch,
        "time",
        SimpleNamespace(
            time=lambda: now[0], monotonic=watch.time.monotonic, strftime=watch.time.strftime
        ),
    )
    client = _voter_client(watch, submitted_ago=60, window=3600)
    if failure == "lookup":
        client.get_deliverable_url.side_effect = NetworkError("scan failed")
    else:
        _stub_download(monkeypatch, watch, _refused)
    window_end = now[0] - 60 + 3600
    prompted_at = []
    monkeypatch.setattr(
        "builtins.input", lambda *_: prompted_at.append(window_end - now[0]) or "r"
    )
    log = {"args": {"jobId": 7}, "blockNumber": 123}
    pending, seen, failures = {7: log}, set(), {}

    while pending and now[0] < window_end:
        watch._retry_pending_disputed(
            pending, client, "0xvoter", "https://gateway/", seen, failures
        )
        now[0] += watch.POLL_INTERVAL

    assert len(prompted_at) == 1
    if failure == "lookup":
        assert 1800 - watch.POLL_INTERVAL < prompted_at[0] <= 1800
    else:
        assert prompted_at[0] > 3000  # three ticks after the first attempt
    client.vote_reject.assert_called_once_with(7)


def _watch_client(head):
    from unittest.mock import MagicMock

    client = MagicMock()
    client.w3.eth.block_number = head
    return client


def test_poll_once_reads_at_most_max_log_range_blocks_and_advances():
    watch = _load_example("voter/watch.py")
    client = _watch_client(head=5_000)
    events = client.policy.contract.events
    events.Disputed.return_value.get_logs.return_value = []
    events.VoteCast.return_value.get_logs.return_value = []
    state = watch.WatchState(last_block=0)

    watch.poll_once(client, "0xvoter", "https://gateway/", 2, state)

    events.Disputed.return_value.get_logs.assert_called_once_with(
        from_block=1, to_block=watch.MAX_LOG_RANGE
    )
    assert state.last_block == watch.MAX_LOG_RANGE


@pytest.mark.parametrize("failing", ["Disputed", "VoteCast"])
def test_poll_once_keeps_the_cursor_when_a_log_read_fails(failing):
    # A transient RPC error used to end the Python voter; now the tick fails,
    # the cursor stays, and the same range is read again (no dispute skipped).
    watch = _load_example("voter/watch.py")
    client = _watch_client(head=150)
    events = client.policy.contract.events
    events.Disputed.return_value.get_logs.return_value = [{"args": {"jobId": 7}}]
    events.VoteCast.return_value.get_logs.return_value = []
    getattr(events, failing).return_value.get_logs.side_effect = OSError("502")
    state = watch.WatchState(last_block=100)

    with pytest.raises(OSError):
        watch.poll_once(client, "0xvoter", "https://gateway/", 2, state)

    assert state.last_block == 100
    assert state.seen_disputed == set()
    assert state.pending_disputed == {}


def test_voter_exits_at_startup_when_the_rpc_drops_logs_as_old_as_the_window():
    from unittest.mock import MagicMock

    watch = _load_example("voter/watch.py")
    client = MagicMock()
    client.w3.eth.block_number = 100_000
    client.w3.eth.get_block.side_effect = lambda n: {"timestamp": n // 2}
    client.policy.dispute_window.return_value = 7 * 86400
    client.policy.contract.events.Disputed.return_value.get_logs.side_effect = [
        [],
        ValueError("403 Archive requests require a personal token"),
    ]

    with pytest.raises(SystemExit, match="as old as the voting window.*archive"):
        watch.require_log_rpc(client, "https://bsc-rpc.publicnode.com")


def test_voter_still_prompts_for_a_malformed_manifest(monkeypatch, capsys):
    # SRC-1368's shape: a string response; the voter can still vote reject.
    watch = _load_example("voter/watch.py")
    client = _voter_client(watch)
    _stub_download(
        monkeypatch, watch, lambda: _public_response(200, {**_MANIFEST, "response": "content"})
    )
    prompts = []
    monkeypatch.setattr("builtins.input", lambda prompt="": prompts.append(prompt) or "r")

    watch.handle_disputed_job(client, 7, "0xvoter", "https://gateway/")

    assert len(prompts) == 1
    assert "manifest is malformed" in capsys.readouterr().out
    client.vote_reject.assert_called_once_with(7)


def test_voter_stops_retrying_once_the_voting_window_closed(monkeypatch):
    watch = _load_example("voter/watch.py")
    client = _voter_client(watch, deliverable_url=None, submitted_ago=3600, window=3600)
    monkeypatch.setattr("builtins.input", _no_prompt)

    watch.handle_disputed_job(client, 7, "0xvoter", "https://gateway/")

    client.get_deliverable_url.assert_not_called()


def test_voter_exits_at_startup_when_the_rpc_cannot_serve_logs():
    from unittest.mock import MagicMock

    watch = _load_example("voter/watch.py")
    client = MagicMock()
    client.w3.eth.block_number = 100
    client.policy.contract.events.Disputed.return_value.get_logs.side_effect = ValueError(
        {"code": -32005, "message": "limit exceeded"}
    )

    with pytest.raises(SystemExit, match="does not serve eth_getLogs.*RPC_URL"):
        watch.require_log_rpc(client, "https://bsc-dataseed.binance.org")
