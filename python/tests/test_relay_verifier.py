"""Multi-RPC secondary confirmation (port of typescript/tests/relayVerifier.test.ts)."""

from __future__ import annotations

import pytest

from bnbagent.core.relay_verifier import (
    confirm_tx_unseen,
    fetch_tx_presence,
    resolve_fallback_rpc_urls,
)
from bnbagent.networks import BSC_MAINNET_CHAIN_ID, BSC_TESTNET_CHAIN_ID

HASH = "0x" + "ab" * 32


@pytest.fixture(autouse=True)
def _no_env_override(monkeypatch):
    monkeypatch.delenv("BNBAGENT_FALLBACK_RPC_URLS", raising=False)


def _probe(answers):
    calls = []

    def probe(url, tx_hash, timeout):
        calls.append((url, tx_hash, timeout))
        answer = answers[url]
        if isinstance(answer, Exception):
            raise answer
        return answer

    return probe, calls


# ── resolve_fallback_rpc_urls ──


def test_explicit_urls_beat_env_and_the_built_in_table(monkeypatch):
    monkeypatch.setenv("BNBAGENT_FALLBACK_RPC_URLS", "https://env.example")
    assert resolve_fallback_rpc_urls(
        BSC_TESTNET_CHAIN_ID, fallback_rpc_urls=["https://a.example", "https://b.example"]
    ) == ["https://a.example", "https://b.example"]


def test_env_beats_the_built_in_table(monkeypatch):
    monkeypatch.setenv("BNBAGENT_FALLBACK_RPC_URLS", "https://x.example, https://y.example")
    assert resolve_fallback_rpc_urls(BSC_TESTNET_CHAIN_ID) == [
        "https://x.example",
        "https://y.example",
    ]


def test_built_in_table_and_unknown_chain():
    for chain_id in (BSC_TESTNET_CHAIN_ID, BSC_MAINNET_CHAIN_ID):
        urls = resolve_fallback_rpc_urls(chain_id)
        assert len(urls) >= 2
        assert all(u.startswith("https://") for u in urls)
    assert resolve_fallback_rpc_urls(1337) == []


def test_primary_and_repeated_origins_are_dropped():
    urls = resolve_fallback_rpc_urls(
        BSC_TESTNET_CHAIN_ID,
        primary_rpc_url="https://A.example/",
        fallback_rpc_urls=[
            "https://a.example",
            "https://b.example/path?key=1",
            "https://B.example:443/other",
            "ftp://c.example",
            "https://c.example",
        ],
    )
    assert urls == ["https://b.example/path?key=1", "https://c.example"]


# ── confirm_tx_unseen ──

TWO = ["https://one.example", "https://two.example"]


@pytest.mark.parametrize(
    ("answers", "verdict"),
    [
        ({"https://one.example": "unseen", "https://two.example": "unseen"}, "confirmed-unseen"),
        ({"https://one.example": "seen", "https://two.example": "seen"}, "seen"),
        # one seen answer alone is not authoritative
        ({"https://one.example": "seen", "https://two.example": "error"}, "inconclusive"),
        # a single conflicting seen answer blocks an unseen quorum
        (
            {"https://one.example": "unseen", "https://two.example": "seen"},
            "inconclusive",
        ),
        ({"https://one.example": "unseen", "https://two.example": "error"}, "inconclusive"),
        ({"https://one.example": "error", "https://two.example": "error"}, "inconclusive"),
        # a throwing probe counts as error and never escapes
        (
            {"https://one.example": RuntimeError("boom"), "https://two.example": "unseen"},
            "inconclusive",
        ),
    ],
)
def test_quorum(answers, verdict):
    probe, _ = _probe(answers)
    assert confirm_tx_unseen(HASH, chain_id=97, fallback_rpc_urls=TWO, probe=probe) == verdict


def test_no_usable_endpoints_is_inconclusive_without_probing():
    probe, calls = _probe({})
    assert confirm_tx_unseen(HASH, chain_id=1337, probe=probe) == "inconclusive"
    assert calls == []


def test_per_endpoint_timeout_is_passed_to_the_probe():
    probe, calls = _probe(dict.fromkeys(TWO, "unseen"))
    confirm_tx_unseen(
        HASH, chain_id=97, fallback_rpc_urls=TWO, probe=probe, per_endpoint_timeout=1.5
    )
    assert sorted(calls) == [(url, HASH, 1.5) for url in TWO]


def test_default_probe_reads_eth_get_transaction_by_hash(monkeypatch):
    replies = []

    class Response:
        def __init__(self, body):
            self._body = body

        def read(self):
            return self._body

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def urlopen(request, timeout):
        replies.append((request.full_url, request.data, timeout))
        return Response(next(bodies))

    bodies = iter(
        [
            b'{"jsonrpc":"2.0","id":1,"result":null}',
            f'{{"jsonrpc":"2.0","id":1,"result":{{"hash":"{HASH.upper()}"}}}}'.encode(),
            b'{"jsonrpc":"2.0","id":1,"result":{"hash":"0xdifferent"}}',
            b'{"jsonrpc":"2.0","id":1,"error":{"code":-32000}}',
        ]
    )
    monkeypatch.setattr("bnbagent.core.relay_verifier.urllib.request.urlopen", urlopen)

    assert fetch_tx_presence("https://rpc.example", HASH, 2.0) == "unseen"
    assert fetch_tx_presence("https://rpc.example", HASH, 2.0) == "seen"
    # a transaction object for another hash is not a "seen"
    assert fetch_tx_presence("https://rpc.example", HASH, 2.0) == "error"
    assert fetch_tx_presence("https://rpc.example", HASH, 2.0) == "error"
    assert b"eth_getTransactionByHash" in replies[0][1]
    assert replies[0][2] == 2.0
