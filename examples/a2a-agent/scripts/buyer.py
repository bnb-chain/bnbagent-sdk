"""Buyer counterpart: discover the agent, fetch its A2A card, get a signed quote.

Three stages — each gated by what you configure:

1. **Discover** (optional): when ``AGENT_ID`` is set, resolve the provider's
   A2A endpoint from the ERC-8004 registry (the inverse of register.py).
   Otherwise fall back to ``A2A_BASE_URL`` directly.
2. **Quote**: fetch ``/.well-known/agent-card.json``, then JSON-RPC
   ``message/send`` with negotiation terms → wallet-signed quote.
3. **On-chain** (optional): when ``BUYER_PRIVATE_KEY`` is set, anchor the
   quoted description with ``createJob`` → ``register_job`` → ``set_budget``
   → ``fund`` via ``ERC8183Client``. Without it, the script stops after
   printing the quote — useful for a chain-free first run.

Usage:
    uv run python scripts/buyer.py
"""

from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path

import httpx
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / os.path.basename(os.environ.get("ENV_FILE", ".env")))

NETWORK = os.getenv("NETWORK", "bsc-testnet")


def discover_card_url() -> str:
    """ERC-8004 discovery when AGENT_ID is set; A2A_BASE_URL fallback otherwise."""
    agent_id = os.getenv("AGENT_ID")
    if agent_id:
        from bnbagent import ERC8004Agent, EVMWalletProvider
        from bnbagent.erc8004.agent_uri import AgentURIGenerator

        # Read-only lookup still needs a wallet for client construction; any key works.
        wallet = EVMWalletProvider(
            password="lookup-only",
            private_key=os.getenv("BUYER_PRIVATE_KEY") or os.getenv("PRIVATE_KEY"),
        )
        sdk = ERC8004Agent(wallet_provider=wallet, network=NETWORK)
        info = sdk.get_agent_info(int(agent_id))
        registration = AgentURIGenerator.decode_registration_file_from_base64(info["agentURI"])
        # The EIP-8004 registration-v1 file lists endpoints under "services".
        for ep in registration.get("services", []):
            if ep.get("name") == "A2A":
                print(f"[discover] agent {agent_id} → {ep['endpoint']}")
                return ep["endpoint"]
        sys.exit(f"agent {agent_id} has no A2A endpoint registered")
    base = os.getenv("A2A_BASE_URL", "http://localhost:8010").rstrip("/")
    return f"{base}/.well-known/agent-card.json"


def get_quote(card_url: str) -> dict:
    card = httpx.get(card_url, timeout=10).raise_for_status().json()
    print(f"[card] {card['name']} — skills: {[s['id'] for s in card['skills']]}")

    inquiry = {
        "skill": "negotiate-erc8183-job",
        "task_description": "Summarize the latest BNB Chain ecosystem news",
        "terms": {
            "deliverables": "One markdown summary of the latest BNB Chain news",
            "quality_standards": "At least 5 sourced items, no older than 48h",
        },
    }
    rpc = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "message/send",
        "params": {
            "message": {
                "kind": "message",
                "role": "user",
                "messageId": str(uuid.uuid4()),
                "parts": [{"kind": "data", "data": inquiry}],
            }
        },
    }
    reply = httpx.post(card["url"], json=rpc, timeout=30).raise_for_status().json()
    if "error" in reply:
        sys.exit(f"A2A error: {reply['error']}")
    quote = reply["result"]["parts"][0]["data"]
    terms_out = (quote.get("response") or {}).get("terms") or {}
    print(f"[quote] price={terms_out.get('price')} currency={terms_out.get('currency')}")
    print(f"[quote] negotiation_hash={quote.get('negotiation_hash')}")
    print(f"[quote] provider_sig={str(quote.get('provider_sig'))[:42]}…")
    return quote


def fund_job(quote: dict) -> None:
    buyer_key = os.getenv("BUYER_PRIVATE_KEY")
    if not buyer_key:
        print("[on-chain] BUYER_PRIVATE_KEY not set — stopping after quote (chain-free run)")
        return

    import time

    from bnbagent import ERC8183Client, EVMWalletProvider
    from bnbagent.erc8183.negotiation import build_job_description

    wallet = EVMWalletProvider(password=os.getenv("BUYER_WALLET_PASSWORD", "demo-password"), private_key=buyer_key)
    client = ERC8183Client(wallet_provider=wallet, network=NETWORK)

    provider = quote["provider_address"]
    price = int(quote["response"]["terms"]["price"])
    # Anchor the SAME signed terms on-chain so provider_sig stays verifiable:
    # ecrecover(negotiation_hash, provider_sig) == job.provider.
    description = build_job_description(quote)

    created = client.create_job(
        provider=provider,
        expired_at=int(time.time()) + 26 * 3600,  # > 24h testnet dispute window
        description=description,
    )
    job_id = created["jobId"]
    print(f"[on-chain] createJob → job {job_id} ({created['transactionHash']})")
    client.register_job(job_id)
    client.set_budget(job_id, price)
    client.fund(job_id, price)
    print(f"[on-chain] job {job_id} FUNDED with {price} raw units")


if __name__ == "__main__":
    quote = get_quote(discover_card_url())
    fund_job(quote)
