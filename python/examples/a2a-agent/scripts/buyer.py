"""Buyer counterpart: discover the agent, fetch its A2A card, get a signed quote.

Stages — each gated by what you configure:

1. **Discover** (optional): when ``AGENT_ID`` is set, resolve the provider's
   A2A endpoint from the ERC-8004 registry (the inverse of register.py).
   Otherwise fall back to ``A2A_BASE_URL`` directly.
2. **Resolve message URL**: GET ``/.well-known/agent-card.json`` and use the
   card's advertised url; if discovery fails (e.g. a POST-only AgentCore invoke
   endpoint serves no GET-able card), POST skills straight to the base.
3. **Quote**: JSON-RPC ``message/send`` with negotiation terms → signed quote.
4. **On-chain** (optional): when ``BUYER_PRIVATE_KEY`` is set, anchor the quoted
   description with ``createJob`` → ``register_job`` → ``set_budget`` → ``fund``,
   then read the job's status once. Without the key, stops after the quote.

Delivery models differ by seller: this example server is negotiate +
status-read only. A studio-style seller instead delivers on a PUSH signal —
after funding, send an A2A ``{"skill":"notify_funded","job_id":<int>}`` message
to trigger delivery, then poll the CHAIN for SUBMITTED. See ``check_status``.

Usage:
    uv run python scripts/buyer.py
"""

from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import httpx
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / os.path.basename(os.environ.get("ENV_FILE", ".env")))

NETWORK = os.getenv("NETWORK", "bsc-testnet")


def discover_base_url() -> str:
    """Seller base URL: ERC-8004 discovery when AGENT_ID is set; A2A_BASE_URL otherwise."""
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
    return os.getenv("A2A_BASE_URL", "http://localhost:8010").rstrip("/")


def agent_card_url(base: str) -> str:
    """Agent-card discovery URL for ``base``, inserting the well-known path BEFORE
    any query string.

    Naive ``f"{base}/.well-known/agent-card.json"`` breaks on an AgentCore invoke
    URL (``…/invocations?qualifier=DEFAULT``): the path would land after the query
    and the URL becomes unreachable (BUG-025). ``urlsplit`` keeps the query where
    it belongs and the append is idempotent when the path is already present.
    """
    parts = urlsplit(base)
    if not parts.scheme or not parts.netloc:
        url = base.rstrip("/")
        return url if url.endswith("/.well-known/agent-card.json") else f"{url}/.well-known/agent-card.json"
    path = parts.path.rstrip("/")
    if not path.endswith("/.well-known/agent-card.json"):
        path += "/.well-known/agent-card.json"
    return urlunsplit((parts.scheme, parts.netloc, path, parts.query, parts.fragment))


def resolve_message_url(base: str) -> str:
    """Resolve the URL to POST JSON-RPC ``message/send`` to.

    Tries A2A discovery first (GET the agent card, use its advertised ``url``).
    AgentCore invoke endpoints are POST-only and serve no GET-able card, so a
    failed discovery is expected there — fall back to POSTing skill payloads
    straight to the base invoke URL instead of hard-failing (BUG-025).
    """
    card_url = agent_card_url(base)
    try:
        resp = httpx.get(card_url, timeout=10)
        if resp.status_code == 200:
            card = resp.json()
            print(f"[card] {card['name']} — skills: {[s['id'] for s in card['skills']]}")
            return card.get("url") or base
        print(f"[discover] agent-card GET → HTTP {resp.status_code}; skipping discovery")
    except httpx.HTTPError as exc:
        print(f"[discover] agent-card GET failed ({exc}); skipping discovery")
    print(f"[discover] POSTing skills directly to {base} (POST-only endpoint, no GET-able card)")
    return base


def send_skill(message_url: str, data: dict) -> dict:
    """POST a single-skill A2A ``message/send`` and return the parsed reply."""
    rpc = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "message/send",
        "params": {
            "message": {
                "kind": "message",
                "role": "user",
                "messageId": str(uuid.uuid4()),
                "parts": [{"kind": "data", "data": data}],
            }
        },
    }
    return httpx.post(message_url, json=rpc, timeout=30).raise_for_status().json()


def negotiate(message_url: str) -> dict:
    reply = send_skill(
        message_url,
        {
            "skill": "negotiate-erc8183-job",
            "task_description": "Summarize the latest BNB Chain ecosystem news",
            "terms": {
                "deliverables": "One markdown summary of the latest BNB Chain news",
                "quality_standards": "At least 5 sourced items, no older than 48h",
            },
        },
    )
    if "error" in reply:
        sys.exit(f"A2A error: {reply['error']}")
    quote = reply["result"]["parts"][0]["data"]
    terms_out = (quote.get("response") or {}).get("terms") or {}
    print(f"[quote] price={terms_out.get('price')} currency={terms_out.get('currency')}")
    print(f"[quote] negotiation_hash={quote.get('negotiation_hash')}")
    print(f"[quote] provider_sig={str(quote.get('provider_sig'))[:42]}…")
    return quote


def fund_job(quote: dict) -> int | None:
    buyer_key = os.getenv("BUYER_PRIVATE_KEY")
    if not buyer_key:
        print("[on-chain] BUYER_PRIVATE_KEY not set — stopping after quote (chain-free run)")
        return None

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
    return job_id


def check_status(message_url: str, job_id: int) -> None:
    """Read the funded job's on-chain status once, via this example server's
    ``erc8183-job-status`` skill.

    Two delivery models — mind the difference:
      * THIS example server negotiates and reads status but performs NO
        delivery, so the job stays FUNDED; a single read shows the lifecycle
        without spinning forever.
      * A studio-style seller delivers on a PUSH signal instead: after funding,
        the buyer sends an A2A ``{"skill":"notify_funded","job_id":<int>}``
        message to trigger delivery, then polls the CHAIN for the job reaching
        SUBMITTED to read the deliverable_url. There is no server-side
        job-query endpoint.
    """
    reply = send_skill(message_url, {"skill": "erc8183-job-status", "job_id": int(job_id)})
    if "error" in reply:
        print(f"[status] lookup failed: {reply['error']}")
        return
    status = reply["result"]["parts"][0]["data"]
    print(f"[status] job {job_id} → {status}")


if __name__ == "__main__":
    _message_url = resolve_message_url(discover_base_url())
    _quote = negotiate(_message_url)
    _job_id = fund_job(_quote)
    if _job_id is not None:
        check_status(_message_url, _job_id)
