"""Web3 utilities — BSC-aware Web3 factory."""

from __future__ import annotations

from web3 import Web3


def create_web3(rpc_url: str = "") -> Web3:
    """Create a Web3 instance with BSC POA middleware auto-injected.

    Args:
        rpc_url: RPC endpoint URL. If empty, uses BSC Testnet default.

    Returns:
        Web3 instance ready for BSC operations.
    """
    if not rpc_url:
        rpc_url = "https://data-seed-prebsc-2-s2.binance.org:8545"
    w3 = Web3(Web3.HTTPProvider(rpc_url))
    try:
        from web3.middleware import ExtraDataToPOAMiddleware

        w3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)
    except ImportError:
        try:
            from web3.middleware import geth_poa_middleware

            w3.middleware_onion.inject(geth_poa_middleware, layer=0)
        except ImportError:
            pass
    return w3
