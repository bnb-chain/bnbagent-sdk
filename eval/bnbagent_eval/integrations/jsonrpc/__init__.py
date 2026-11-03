"""Read-only ports over plain Ethereum JSON-RPC (BSC) — no web3, no bnbagent SDK.

Transport is ``httpx`` (core). ``eth_call`` encoding and log decoding use ``eth-abi``
(the ``[evm]`` extra), imported lazily: receipts, transactions, native balances and raw logs
work without it.

>>> ports = jsonrpc.ports(  # doctest: +SKIP
...     "https://bsc-testnet.example/rpc",
...     chain_id=97,
...     addresses={"identity_registry": "0x...", "commerce": "0x...", "policy": "0x..."},
... )
"""

from __future__ import annotations

from collections.abc import Mapping

import httpx

from ...ports import ChainPorts, StoragePort
from .client import DEFAULT_MAX_LOG_RANGE, JsonRpcClient
from .contracts import JsonRpcCommerce, JsonRpcPolicy, JsonRpcRegistry
from .reader import JsonRpcReader
from .storage import DEFAULT_MAX_BYTES, ReadOnlyStorage, Resolver, system_resolver

REQUIRED_ADDRESSES = ("identity_registry", "commerce", "policy")


def ports(
    rpc_url: str,
    *,
    chain_id: int,
    addresses: Mapping[str, str],
    storage: StoragePort | None = None,
    ipfs_gateway: str = "https://ipfs.io/ipfs/",
    client: httpx.AsyncClient | None = None,
    block: int | str = "latest",
    max_log_range: int | None = DEFAULT_MAX_LOG_RANGE,
    max_document_bytes: int = DEFAULT_MAX_BYTES,
    resolver: Resolver = system_resolver,
    timeout: float = 15.0,
) -> ChainPorts:
    """Build read-only ``ChainPorts`` (registry, commerce, policy, storage, reader).

    ``addresses`` holds ``identity_registry``, ``commerce``, ``policy`` and optionally
    ``router`` (then each job's policy comes from ``router.jobPolicy``). ``storage`` replaces
    the built-in HTTP(S) / IPFS-gateway downloader. ``block`` pins every read to a block number
    (default ``"latest"``). An injected ``client`` is shared by the RPC client and the downloader.
    """
    missing = [k for k in REQUIRED_ADDRESSES if not addresses.get(k)]
    if missing:
        raise ValueError(f"addresses is missing {missing}")
    http = client or httpx.AsyncClient(timeout=timeout)
    rpc = JsonRpcClient(rpc_url, client=http, block=block, max_log_range=max_log_range)
    # Without an injected client the downloader has its own, so it can pin connections to
    # checked public addresses while the RPC client may still reach a local node.
    store = storage or ReadOnlyStorage(
        client=client,
        ipfs_gateway=ipfs_gateway,
        max_bytes=max_document_bytes,
        resolver=resolver,
        timeout=timeout,
    )
    return ChainPorts(
        registry=JsonRpcRegistry(
            rpc,
            chain_id=chain_id,
            registry_address=addresses["identity_registry"],
            storage=store,
        ),
        commerce=JsonRpcCommerce(
            rpc,
            chain_id=chain_id,
            commerce_address=addresses["commerce"],
            policy_address=addresses["policy"],
            router_address=addresses.get("router") or None,
            storage=store,
        ),
        policy=JsonRpcPolicy(rpc, policy_address=addresses["policy"]),
        storage=store,
        reader=JsonRpcReader(rpc, chain_id=chain_id),
        extras={"jsonrpc": rpc, "http_client": http},
    )


__all__ = [
    "JsonRpcClient",
    "JsonRpcCommerce",
    "JsonRpcPolicy",
    "JsonRpcReader",
    "JsonRpcRegistry",
    "ReadOnlyStorage",
    "ports",
]
