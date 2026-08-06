"""Top-level SDK configuration.

Exports:

- :class:`NetworkConfig` — per-network defaults (RPC, paymaster, contract
  addresses for every module that uses on-chain state).
- :func:`resolve_network` — looks up a preset by name, with an optional
  ``RPC_URL`` env override. **Module-specific contract overrides live in
  their own module configs** (e.g. ``ERC8183Config``, ``get_erc8004_config``),
  not here.

Env var surface
---------------
``resolve_network`` is intentionally narrow: it only reads ``RPC_URL``.
Module-scoped env vars (``ERC8183_COMMERCE_ADDRESS``, ``ERC8004_REGISTRY_ADDRESS``,
``STORAGE_*``, ...) are owned by the corresponding module config. The
project-root ``.env.example`` is the authoritative reference.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from .core.config import get_env

logger = logging.getLogger(__name__)


@dataclass
class NetworkConfig:
    """Per-network configuration with ALL protocol addresses.

    ERC-8183 is a three-contract stack: AgenticCommerce kernel (escrow),
    EvaluatorRouter (routing + hook), and OptimisticPolicy (silence-approves,
    vote-rejects). Payment token is NOT configured here — it is immutable
    on the Commerce kernel and read at runtime via ``ERC8183Client.payment_token``.
    """

    name: str
    chain_id: int
    rpc_url: str
    paymaster_url: str | None = None
    use_paymaster: bool = False
    # ERC-8004 Identity Registry
    registry_contract: str = ""
    # ERC-8183 stack
    commerce_contract: str = ""
    router_contract: str = ""
    policy_contract: str = ""


NETWORKS: dict[str, NetworkConfig] = {
    "bsc-testnet": NetworkConfig(
        name="bsc-testnet",
        chain_id=97,
        rpc_url="https://data-seed-prebsc-2-s2.binance.org:8545",
        paymaster_url="https://bsc-megafuel-testnet.nodereal.io",
        use_paymaster=True,
        registry_contract="0x8004A818BFB912233c491871b3d84c89A494BD9e",
        commerce_contract="0xa206c0517b6371c6638cd9e4a42cc9f02a33b0de",
        router_contract="0xd7d36d66d2f1b608a0f943f722d27e3744f66f25",
        policy_contract="0x4f4678d4439fec812ac7674bb3efb4c8f5fb78a6",
    ),
    "bsc-mainnet": NetworkConfig(
        name="bsc-mainnet",
        chain_id=56,
        rpc_url="https://bsc-dataseed.binance.org",
        paymaster_url="https://bsc-megafuel.nodereal.io/",
        use_paymaster=True,
        registry_contract="0x8004A169FB4a3325136EB29fA0ceB6D2e539a432",
        commerce_contract="0xea4daa3100a767e86fded867729ae7446476eba6",
        router_contract="0x51895229e12f9876011789b04f8698af06ccd6da",
        policy_contract="0x9c01845705b3078aa2e8cff7520a6376fd766de5",
    ),
}


def resolve_network(network: str | NetworkConfig = "bsc-testnet") -> NetworkConfig:
    """Resolve a network preset to a concrete ``NetworkConfig``.

    Accepts either a preset name (``"bsc-testnet"`` / ``"bsc-mainnet"``) or a
    concrete ``NetworkConfig`` instance:

    - **String** → look up the preset; apply the RPC env override if set.
      Module-scoped contract-address envs (``ERC8183_*``, ``ERC8004_*``) are
      NOT read here — they belong to each module's own config loader.
    - **NetworkConfig** → returned as-is; env vars are never applied (fully
      explicit control is the point of passing an object).

    RPC override precedence (a process that touches several networks needs
    per-network pins — a single shared URL would silently apply to the wrong
    chain):

    1. ``RPC_URL_<NETWORK>`` — per-network, e.g. ``RPC_URL_BSC_TESTNET`` /
       ``RPC_URL_BSC_MAINNET`` (preset name uppercased, ``-`` → ``_``).
    2. ``RPC_URL`` — global, network-agnostic.
    3. The preset default.
    """
    if isinstance(network, NetworkConfig):
        return network

    nc = NETWORKS.get(network)
    if nc is None:
        raise ValueError(f"Unknown network: {network}")

    per_network_key = f"RPC_URL_{nc.name.upper().replace('-', '_')}"
    rpc_override = get_env(per_network_key) or get_env("RPC_URL")
    if rpc_override:
        use_paymaster = not rpc_override.startswith("http://localhost")
        return NetworkConfig(
            name=nc.name,
            chain_id=nc.chain_id,
            rpc_url=rpc_override,
            paymaster_url=nc.paymaster_url,
            use_paymaster=use_paymaster,
            registry_contract=nc.registry_contract,
            commerce_contract=nc.commerce_contract,
            router_contract=nc.router_contract,
            policy_contract=nc.policy_contract,
        )
    return nc
