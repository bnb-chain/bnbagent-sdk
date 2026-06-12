"""Register this A2A agent on the ERC-8004 Identity Registry.

One-time operation. The registered endpoint is the A2A discovery document
(``{base}/.well-known/agent-card.json``) — built with ``AgentEndpoint.a2a()``,
so buyers that discover this agent on-chain can fetch the card directly.

Usage:
    uv run python scripts/register.py

Environment (.env at the example root):
    PRIVATE_KEY / WALLET_PASSWORD — provider wallet (same as the server)
    NETWORK                       — bsc-testnet (default) | bsc-mainnet
    AGENT_NAME / AGENT_DESCRIPTION
    A2A_BASE_URL                  — public base URL of the running server
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / os.path.basename(os.environ.get("ENV_FILE", ".env")))

from bnbagent import AgentEndpoint, ERC8004Agent, EVMWalletProvider


def _make_wallet(network: str):
    """Same WALLET_KIND switch as the server — registration is wallet-polymorphic
    too (ERC-8004 writes route through the wallet's own executor)."""
    if os.getenv("WALLET_KIND", "evm").lower() == "twak":
        from bnbagent.wallets import TWAK_CHAIN_FOR_NETWORK, create_wallet_provider

        kwargs = {"chain": TWAK_CHAIN_FOR_NETWORK[network]}
        if os.getenv("TWAK_BIN"):
            kwargs["twak_bin"] = os.environ["TWAK_BIN"]
        return create_wallet_provider("twak", **kwargs)
    private_key = os.getenv("PRIVATE_KEY")
    if not private_key:
        sys.exit("PRIVATE_KEY is required for WALLET_KIND=evm (see .env.example)")
    return EVMWalletProvider(
        password=os.getenv("WALLET_PASSWORD", "demo-password"),
        private_key=private_key,
    )


def main() -> None:
    base_url = os.getenv("A2A_BASE_URL", "http://localhost:8010")
    name = os.getenv("AGENT_NAME", "a2a-demo-agent")
    description = os.getenv(
        "AGENT_DESCRIPTION",
        "Demo provider that quotes ERC-8183 jobs over the A2A protocol.",
    )

    network = os.getenv("NETWORK", "bsc-testnet")
    sdk = ERC8004Agent(wallet_provider=_make_wallet(network), network=network)

    endpoint = AgentEndpoint.a2a(base_url, version="0.3.0")
    print(f"Registering {name} ({sdk.wallet_address})")
    print(f"  A2A endpoint: {endpoint.endpoint}")

    agent_uri = sdk.generate_agent_uri(name=name, description=description, endpoints=[endpoint])
    result = sdk.register_agent(agent_uri)

    print(f"  tx:       {result.get('transactionHash')}")
    print(f"  agent_id: {result.get('agentId')}")
    print("Save AGENT_ID to .env so buyers can discover this agent on-chain.")


if __name__ == "__main__":
    main()
