"""Secondary confirmation that a relay-returned tx hash is really invisible.

Port of ``typescript/src/core/relayVerifier.ts``. A relay (sponsored) write
can return a hash the chain never saw. Before a caller reacts to that, it is
worth a few seconds to ask *other* RPC endpoints whether the transaction is
truly unseen: a flaky primary RPC must not trigger a same-nonce race against
a transaction that actually reached the mempool.

A decisive result requires two distinct RPC origins. Anything weaker is
``"inconclusive"``, and callers fail closed instead of acting on one
configurable source's claim.
"""

from __future__ import annotations

import json
import urllib.request
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from typing import Literal
from urllib.parse import urlsplit

from ..networks import BSC_MAINNET_CHAIN_ID, BSC_TESTNET_CHAIN_ID
from .config import get_env

TxProbeResult = Literal["seen", "unseen", "error"]
#: ``"seen"``: two independent origins see the transaction.
#: ``"confirmed-unseen"``: two independent origins report it absent.
#: ``"inconclusive"``: fewer than two agree, or seen/unseen answers conflict.
SecondaryConfirmation = Literal["confirmed-unseen", "seen", "inconclusive"]
TxPresenceProbe = Callable[[str, str, float], TxProbeResult]

#: Default per-endpoint probe timeout (seconds).
RELAY_VERIFIER_ENDPOINT_TIMEOUT = 3.0
_QUORUM = 2

# Last-resort public endpoints per chain; overridable through
# BNBAGENT_FALLBACK_RPC_URLS (comma-separated) or ``fallback_rpc_urls``.
_FALLBACK_RPC_URLS: dict[int, tuple[str, ...]] = {
    BSC_TESTNET_CHAIN_ID: (
        "https://bsc-testnet-rpc.publicnode.com",
        "https://data-seed-prebsc-1-s1.bnbchain.org:8545",
        "https://data-seed-prebsc-2-s1.bnbchain.org:8545",
    ),
    BSC_MAINNET_CHAIN_ID: (
        "https://bsc-rpc.publicnode.com",
        "https://bsc-dataseed1.bnbchain.org",
        "https://bsc-dataseed2.bnbchain.org",
    ),
}


def _rpc_origin(url: str | None) -> str | None:
    """Independent-source key: scheme + host + port, never path or query."""
    if not url:
        return None
    try:
        parts = urlsplit(url.strip())
        port = parts.port
    except ValueError:
        return None
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return None
    default_port = 443 if parts.scheme == "https" else 80
    host = parts.hostname.lower()
    return f"{parts.scheme}://{host}" + (f":{port}" if port and port != default_port else "")


def resolve_fallback_rpc_urls(
    chain_id: int,
    *,
    primary_rpc_url: str | None = None,
    fallback_rpc_urls: Sequence[str] | None = None,
) -> list[str]:
    """Endpoints to probe: explicit list, then BNBAGENT_FALLBACK_RPC_URLS, then
    the built-in per-chain table, minus the primary RPC and repeated origins."""
    if fallback_rpc_urls:
        urls = list(fallback_rpc_urls)
    else:
        raw = get_env("BNBAGENT_FALLBACK_RPC_URLS")
        if raw and raw.strip():
            urls = raw.split(",")
        else:
            urls = list(_FALLBACK_RPC_URLS.get(chain_id, ()))
    primary = _rpc_origin(primary_rpc_url)
    out: list[str] = []
    seen: set[str] = set()
    for entry in urls:
        key = _rpc_origin(entry)
        if key is None or key == primary or key in seen:
            continue
        seen.add(key)
        out.append(entry.strip())
    return out


def fetch_tx_presence(rpc_url: str, tx_hash: str, timeout: float) -> TxProbeResult:
    """One raw ``eth_getTransactionByHash`` call. ``null`` is an authoritative
    "unseen"; transport failures, non-2xx responses and RPC errors are
    ``"error"`` (that endpoint contributes no signal)."""
    body = json.dumps(
        {"jsonrpc": "2.0", "id": 1, "method": "eth_getTransactionByHash", "params": [tx_hash]}
    ).encode()
    request = urllib.request.Request(
        rpc_url, data=body, headers={"content-type": "application/json"}, method="POST"
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
            payload = json.loads(response.read())
    except Exception:  # noqa: BLE001 - any failure means "no signal"
        return "error"
    if not isinstance(payload, dict) or payload.get("error") is not None:
        return "error"
    result = payload.get("result")
    if "result" in payload and result is None:
        return "unseen"
    if not isinstance(result, dict):
        return "error"
    returned = result.get("hash")
    return "seen" if isinstance(returned, str) and returned.lower() == tx_hash.lower() else "error"


def confirm_tx_unseen(
    tx_hash: str,
    *,
    chain_id: int,
    primary_rpc_url: str | None = None,
    fallback_rpc_urls: Sequence[str] | None = None,
    probe: TxPresenceProbe | None = None,
    per_endpoint_timeout: float = RELAY_VERIFIER_ENDPOINT_TIMEOUT,
) -> SecondaryConfirmation:
    """Ask the fallback endpoints (in parallel) whether ``tx_hash`` is visible.

    Never raises. See :data:`SecondaryConfirmation` for the quorum rules.
    """
    urls = resolve_fallback_rpc_urls(
        chain_id, primary_rpc_url=primary_rpc_url, fallback_rpc_urls=fallback_rpc_urls
    )
    if not urls:
        return "inconclusive"
    run = probe or fetch_tx_presence

    def safe_probe(url: str) -> TxProbeResult:
        try:
            return run(url, tx_hash, per_endpoint_timeout)
        except Exception:  # noqa: BLE001 - a throwing probe counts as "error"
            return "error"

    with ThreadPoolExecutor(max_workers=len(urls)) as pool:
        results = list(pool.map(safe_probe, urls))
    seen = results.count("seen")
    unseen = results.count("unseen")
    if seen and unseen:
        return "inconclusive"
    if seen >= _QUORUM:
        return "seen"
    if unseen >= _QUORUM:
        return "confirmed-unseen"
    return "inconclusive"
