"""``ports()``: build every port on the bnbagent SDK."""

from __future__ import annotations

from typing import Any

from bnbagent.config import resolve_network
from bnbagent.erc8004.agent import ERC8004Agent
from bnbagent.erc8004.constants import get_erc8004_config
from bnbagent.erc8183.client import ERC8183Client
from bnbagent.erc8183.job_ops import ERC8183JobOps
from bnbagent.wallets.errors import UnsupportedWalletOperation

from ...ports import ChainPorts
from ._errors import to_chain_error
from .commerce import BnbagentCommerce, BnbagentPolicy
from .compat import SDK_VERSION
from .reader import BnbagentChainReader
from .registry import BnbagentRegistry, ReadOnlyIdentityRegistry
from .signer import SdkAssetCatalog, WalletSigner
from .storage import DEFAULT_IPFS_GATEWAY, BnbagentStorage, UriResolver

__all__ = ["ports"]

_PORTS_KW = {"ipfs_gateway", "payer", "client", "identity", "job_ops", "job_ops_kwargs"}


def ports(
    network: Any = "bsc-testnet",
    wallet: Any | None = None,
    storage: Any | None = None,
    **kw: Any,
) -> ChainPorts:
    """Build every port for ``network`` (preset name or SDK ``NetworkConfig``).

    ``wallet`` (SDK ``WalletProvider``) becomes the ``signer`` and enables writes; ``storage``
    (SDK ``StorageProvider``) backs the ``storage`` port, deliverable uploads and ``ipfs://``
    reads. Keyword options: ``ipfs_gateway``, ``payer`` (an SDK ``X402Payer``; default
    ``wallet.make_x402_payer()`` when the wallet has one), ``job_ops_kwargs`` (forwarded to
    ``ERC8183JobOps``), and prebuilt ``client`` / ``identity`` / ``job_ops``.
    """
    unknown = set(kw) - _PORTS_KW
    if unknown:
        raise TypeError(f"unknown ports() options: {sorted(unknown)}")
    nc = resolve_network(network)
    resolver = UriResolver(storage, ipfs_gateway=kw.get("ipfs_gateway", DEFAULT_IPFS_GATEWAY))

    try:
        client = kw.get("client") or ERC8183Client(wallet, nc)
        registry_address = get_erc8004_config(nc)["registry_contract"]
        identity = kw.get("identity") or (
            ERC8004Agent(wallet, nc)
            if wallet is not None
            else ReadOnlyIdentityRegistry(client.w3, registry_address)
        )
        job_ops = kw.get("job_ops")
        if job_ops is None and wallet is not None:
            job_ops = ERC8183JobOps(
                wallet, nc, storage_provider=storage, **kw.get("job_ops_kwargs", {})
            )
    except Exception as exc:
        raise to_chain_error(exc, op="ports") from exc

    payer = kw.get("payer")
    if payer is None and wallet is not None:
        try:
            payer = wallet.make_x402_payer()
        except (UnsupportedWalletOperation, AttributeError):
            payer = None

    return ChainPorts(
        registry=BnbagentRegistry(
            identity, chain_id=nc.chain_id, registry_address=registry_address, resolver=resolver
        ),
        commerce=BnbagentCommerce(client, job_ops=job_ops, resolver=resolver),
        policy=BnbagentPolicy(client),
        storage=None if storage is None else BnbagentStorage(storage, resolver=resolver),
        signer=None if wallet is None else WalletSigner(wallet),
        reader=BnbagentChainReader(client.w3, chain_id=nc.chain_id),
        assets=SdkAssetCatalog(),
        payer=payer,
        extras={"network": nc.name, "chain_id": nc.chain_id, "sdk_version": SDK_VERSION},
    )
