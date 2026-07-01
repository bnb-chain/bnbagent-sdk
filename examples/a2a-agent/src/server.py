"""A2A-fronted ERC-8183 provider agent.

Demonstrates the SDK's recommended serving direction: the agent's outward
surface is **A2A** (agent card + JSON-RPC ``message/send``), while everything
under it is plain SDK protocol capability (NegotiationHandler quote signing,
ERC8183Client job reads). The SDK ships no serving runtime — this file IS the
serving layer, and it is yours to own.

Wire format follows the A2A spec (card at ``/.well-known/agent-card.json``,
JSON-RPC 2.0 ``message/send`` with data parts) but is hand-rolled on FastAPI
to stay minimal and dependency-light. For a production agent, the official
``a2a-sdk`` package implements the same contract with full task/streaming
support — this example keeps the wire shape compatible with its clients.

Skills exposed:
    negotiate-erc8183-job  — returns a wallet-signed price quote (the same
                             NegotiationHandler quote the HTTP example signs)
    erc8183-job-status     — read-only on-chain job lookup

Run:
    uv run uvicorn server:app --app-dir src --port 8010

Environment (.env at the example root):
    PRIVATE_KEY / WALLET_PASSWORD   — provider wallet (quote signing)
    NETWORK                         — bsc-testnet (default) | bsc-mainnet
    AGENT_NAME / AGENT_DESCRIPTION  — card identity
    A2A_BASE_URL                    — public base URL (default http://localhost:8010)
    ERC8183_SERVICE_PRICE           — minimum budget, raw token units
"""

from __future__ import annotations

import logging
import os
import uuid
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

load_dotenv(Path(__file__).resolve().parent.parent / os.path.basename(os.environ.get("ENV_FILE", ".env")))

from bnbagent import EVMWalletProvider
from bnbagent.erc8183 import ERC8183Client, NegotiationHandler
from bnbagent.utils import RateLimitExceeded, SlidingWindowLimiter

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")
logger = logging.getLogger("a2a-agent")

# ── Provider identity + protocol stack (plain SDK capability, no serving) ──

NETWORK = os.getenv("NETWORK", "bsc-testnet")
AGENT_NAME = os.getenv("AGENT_NAME", "a2a-demo-agent")
AGENT_DESCRIPTION = os.getenv(
    "AGENT_DESCRIPTION",
    "Demo provider that quotes ERC-8183 jobs over the A2A protocol.",
)
BASE_URL = os.getenv("A2A_BASE_URL", "http://localhost:8010").rstrip("/")
SERVICE_PRICE = os.getenv("ERC8183_SERVICE_PRICE", "1000000000000000000")  # 1 token

# WALLET_KIND switches the provider wallet (evm | twak). Everything below —
# quote signing, job reads — is wallet-polymorphic, so this is the only line
# that changes per kind.
WALLET_KIND = os.getenv("WALLET_KIND", "evm").lower()
if WALLET_KIND == "twak":
    from bnbagent.wallets import TWAK_CHAIN_FOR_NETWORK, create_wallet_provider

    _twak_kwargs = {"chain": TWAK_CHAIN_FOR_NETWORK[NETWORK]}
    if os.getenv("TWAK_BIN"):
        _twak_kwargs["twak_bin"] = os.environ["TWAK_BIN"]
    wallet = create_wallet_provider("twak", **_twak_kwargs)
else:
    _private_key = os.getenv("PRIVATE_KEY")
    if not _private_key:
        raise SystemExit("PRIVATE_KEY is required for WALLET_KIND=evm (see .env.example)")
    wallet = EVMWalletProvider(
        password=os.getenv("WALLET_PASSWORD", "demo-password"),
        private_key=_private_key,
    )

client = ERC8183Client(wallet_provider=wallet, network=NETWORK)

# Bind the quote signature to this chain + commerce contract (anti-replay),
# exactly like the HTTP example's create_erc8183_state does.
negotiation_handler = NegotiationHandler(
    service_price=SERVICE_PRICE,
    currency=client.payment_token,
    wallet_provider=wallet,
    chain_id=client.network.chain_id,
    verifying_contract=client.commerce.address,
)

# Every accepted negotiate burns a wallet signature — throttle it.
negotiate_limiter = SlidingWindowLimiter(max_requests=30, window_seconds=60.0)

# ── A2A surface ──

AGENT_CARD: dict[str, Any] = {
    "protocolVersion": "0.3.0",
    "name": AGENT_NAME,
    "description": AGENT_DESCRIPTION,
    "url": f"{BASE_URL}/a2a",
    "preferredTransport": "JSONRPC",
    "version": "1.0.0",
    "capabilities": {"streaming": False, "pushNotifications": False},
    "defaultInputModes": ["application/json"],
    "defaultOutputModes": ["application/json"],
    "skills": [
        {
            "id": "negotiate-erc8183-job",
            "name": "Negotiate an ERC-8183 job",
            "description": (
                "Send a data part {\"skill\": \"negotiate-erc8183-job\", "
                "\"task_description\": \"...\", \"terms\": {...}} and receive a "
                "wallet-signed quote (price, currency, negotiation_hash, provider_sig). "
                "Anchor the returned envelope on-chain via createJob."
            ),
            "tags": ["erc8183", "negotiation", "bnb-chain"],
            "inputModes": ["application/json"],
            "outputModes": ["application/json"],
        },
        {
            "id": "erc8183-job-status",
            "name": "ERC-8183 job status",
            "description": (
                "Send {\"skill\": \"erc8183-job-status\", \"job_id\": <int>} for a "
                "read-only on-chain job lookup."
            ),
            "tags": ["erc8183", "status"],
            "inputModes": ["application/json"],
            "outputModes": ["application/json"],
        },
    ],
}

app = FastAPI(title=AGENT_NAME)


@app.get("/.well-known/agent-card.json")
async def agent_card():
    return AGENT_CARD


def _rpc_error(req_id: Any, code: int, message: str, status: int = 200) -> JSONResponse:
    return JSONResponse(
        {"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}},
        status_code=status,
    )


def _agent_message(data: dict[str, Any]) -> dict[str, Any]:
    """A2A Message envelope with a single data part."""
    return {
        "kind": "message",
        "role": "agent",
        "messageId": str(uuid.uuid4()),
        "parts": [{"kind": "data", "data": data}],
    }


def _extract_data_part(message: dict[str, Any]) -> dict[str, Any] | None:
    for part in message.get("parts", []):
        if isinstance(part, dict) and part.get("kind") == "data" and isinstance(part.get("data"), dict):
            return part["data"]
    return None


@app.post("/a2a")
async def a2a_endpoint(request: Request):
    try:
        body = await request.json()
    except Exception:
        return _rpc_error(None, -32700, "Parse error", status=400)

    req_id = body.get("id")
    if body.get("jsonrpc") != "2.0" or "method" not in body:
        return _rpc_error(req_id, -32600, "Invalid Request", status=400)
    if body["method"] != "message/send":
        return _rpc_error(req_id, -32601, f"Method not found: {body['method']}")

    message = (body.get("params") or {}).get("message") or {}
    data = _extract_data_part(message)
    if data is None:
        return _rpc_error(req_id, -32602, "message must carry a data part with a 'skill' field")

    skill = data.get("skill")

    if skill == "negotiate-erc8183-job":
        client_ip = request.client.host if request.client else "unknown"
        try:
            negotiate_limiter.check(client_ip)
        except RateLimitExceeded:
            return _rpc_error(req_id, -32000, "Rate limited, retry later")
        terms = data.get("terms")
        task_description = data.get("task_description")
        if not isinstance(terms, dict) or not isinstance(task_description, str):
            return _rpc_error(
                req_id, -32602,
                "negotiate-erc8183-job requires 'task_description' (string) and 'terms' (object)",
            )
        try:
            result = negotiation_handler.negotiate(
                {"task_description": task_description, "terms": terms}
            )
        except Exception as exc:
            logger.error("negotiation failed: %s", exc)
            return _rpc_error(req_id, -32603, "Negotiation failed")
        envelope = result.to_dict()
        # The buyer needs the provider address for createJob (and to verify
        # ecrecover(negotiation_hash, provider_sig) == provider).
        envelope["provider_address"] = wallet.address
        return {"jsonrpc": "2.0", "id": req_id, "result": _agent_message(envelope)}

    if skill == "erc8183-job-status":
        job_id = data.get("job_id")
        if not isinstance(job_id, int):
            return _rpc_error(req_id, -32602, "erc8183-job-status requires an integer 'job_id'")
        try:
            job = client.get_job(job_id)
        except Exception as exc:
            logger.error("job lookup failed: %s", exc)
            return _rpc_error(req_id, -32603, f"Job {job_id} lookup failed")
        payload = {
            "job_id": job.id,
            "client": job.client,
            "provider": job.provider,
            "status": job.status.name,
            "budget": str(job.budget),
            "expired_at": job.expired_at,
            "submitted_at": job.submitted_at,
            "deliverable": "0x" + job.deliverable.hex(),
        }
        return {"jsonrpc": "2.0", "id": req_id, "result": _agent_message(payload)}

    return _rpc_error(req_id, -32602, f"Unknown skill: {skill!r}")


logger.info("A2A agent %s — card at %s/.well-known/agent-card.json", wallet.address, BASE_URL)
