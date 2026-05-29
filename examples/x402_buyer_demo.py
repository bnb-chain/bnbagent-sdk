"""End-to-end x402 buyer demo: GET → 402 → EIP-3009 sign → retry with X-PAYMENT.

This script is a developer-facing walkthrough of the x402 client-side loop:

    1. Client GETs a paywalled resource.
    2. Server returns HTTP 402 Payment Required, advertising what it will
       accept in the JSON ``accepts[]`` list (scheme=exact, EIP-3009 on a
       specific token + chain).
    3. Client constructs an EIP-712 ``TransferWithAuthorization`` message
       from those terms and signs it through ``X402Signer`` — the SDK's
       constrained, policy-gated signer that enforces recipient + amount
       guards on top of the wallet's own ``SigningPolicy``.
    4. Client base64-encodes the x402 payload envelope, retries the GET
       with ``X-PAYMENT: <envelope>``, and receives the protected resource.

Everything happens against an in-process ``ThreadingHTTPServer`` on
127.0.0.1 — no real network, no testnet U burned. The server does NOT
verify the signature (that's the facilitator's job in production); it
just demonstrates the request/response envelope round-trip so SDK users
can see exactly which bytes go where.

Usage:
    python examples/x402_buyer_demo.py

Exit code 0 + "ALL STEPS OK" line means the buyer-side envelope is wired
end-to-end against the SDK's signing surface.
"""

from __future__ import annotations

import base64
import json
import logging
import secrets
import sys
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from typing import Any

from eth_account import Account

from bnbagent import EVMWalletProvider, X402Signer
from bnbagent.networks import (
    BSC_TESTNET_CHAIN_ID,
    PAYMENT_TOKEN_EIP712_NAME,
    PAYMENT_TOKEN_EIP712_VERSION,
    get_address,
)

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s | %(message)s"
)
log = logging.getLogger("x402_buyer_demo")

# ── Fixtures ──────────────────────────────────────────────────────────────
# Ephemeral, in-memory wallet (persist=False) — fresh key per run, never
# written to disk and never broadcast.
DEMO_PK = Account.create().key.hex()
DEMO_PW = "x402-buyer-demo-pw"

U_TESTNET = get_address(BSC_TESTNET_CHAIN_ID).payment_token
NETWORK_ID = f"eip155:{BSC_TESTNET_CHAIN_ID}"
PAY_TO = "0x" + "be" * 20            # Mock beneficiary (server-controlled in real life)
PRICE_BASE_UNITS = 100_000           # 0.1 U at 6 decimals — same shape as a real x402 listing
SECRET_PAYLOAD = "secret payload"

# EIP-712 schema fields — identical to the production U-token EIP-3009 domain.
EIP712_DOMAIN_FIELDS = [
    {"name": "name", "type": "string"},
    {"name": "version", "type": "string"},
    {"name": "chainId", "type": "uint256"},
    {"name": "verifyingContract", "type": "address"},
]
TWA_FIELDS = [
    {"name": "from", "type": "address"},
    {"name": "to", "type": "address"},
    {"name": "value", "type": "uint256"},
    {"name": "validAfter", "type": "uint256"},
    {"name": "validBefore", "type": "uint256"},
    {"name": "nonce", "type": "bytes32"},
]


# ── Mock 402 server ───────────────────────────────────────────────────────


def _make_402_body() -> dict[str, Any]:
    """Build a realistic x402 v2 challenge body.

    The buyer parses ``accepts[0]`` and reconstructs the EIP-712 domain
    from ``asset`` (the token contract) + ``extra.name`` / ``extra.version``.
    """
    return {
        "x402Version": 2,
        "accepts": [
            {
                "scheme": "exact",
                "network": NETWORK_ID,
                "asset": U_TESTNET,
                "payTo": PAY_TO,
                "amount": str(PRICE_BASE_UNITS),
                "maxTimeoutSeconds": 300,
                "extra": {
                    "name": PAYMENT_TOKEN_EIP712_NAME,
                    "version": PAYMENT_TOKEN_EIP712_VERSION,
                },
            }
        ],
        "resource": "/resource",
    }


class _Mock402Handler(BaseHTTPRequestHandler):
    """Tiny mock seller: 402 without X-PAYMENT, 200 with it.

    Crucially, this handler does NOT validate the signature. A real
    seller would forward the envelope to an x402 facilitator that
    recovers the EIP-712 signer and submits the authorization on chain.
    The demo's job is only to prove the SDK produces an envelope of the
    right shape and that the retry handshake works.
    """

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        # Silence the default stderr access log; we have our own logger.
        log.debug("server: " + format, *args)

    def do_GET(self) -> None:  # noqa: N802
        if self.path != "/resource":
            self.send_error(404)
            return

        payment_header = self.headers.get("X-PAYMENT")
        if not payment_header:
            body = json.dumps(_make_402_body()).encode()
            self.send_response(402)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        # In production the facilitator would verify + settle here. For
        # the demo we just confirm the header round-tripped and serve
        # the protected payload.
        body = json.dumps({"data": SECRET_PAYLOAD}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def _start_server() -> tuple[ThreadingHTTPServer, str]:
    """Bind to an OS-assigned port on loopback and run in a background thread."""
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Mock402Handler)
    host, port = server.server_address
    thread = Thread(target=server.serve_forever, name="mock-402", daemon=True)
    thread.start()
    base = f"http://{host}:{port}"
    log.info("mock 402 server bound on %s", base)
    return server, base


# ── Buyer helpers ─────────────────────────────────────────────────────────


def _request_resource(url: str, *, payment: str | None = None) -> tuple[int, dict[str, Any]]:
    """GET ``url`` with optional X-PAYMENT header; returns (status, json_body)."""
    req = urllib.request.Request(url, method="GET")
    if payment is not None:
        req.add_header("X-PAYMENT", payment)
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        # 402 lands here — we still want the body to parse the accepts[] list.
        return e.code, json.loads(e.read().decode())


def _build_twa_message(from_addr: str, accept: dict[str, Any]) -> dict[str, Any]:
    """Materialize a TransferWithAuthorization message from a 402 accept entry.

    ``validAfter`` / ``validBefore`` form the authorization window the
    facilitator will check on chain. ``nonce`` is a random bytes32 so
    the same authorization can't be replayed.
    """
    now = int(time.time())
    return {
        "from": from_addr,
        "to": accept["payTo"],
        "value": int(accept["amount"]),
        "validAfter": now - 60,
        "validBefore": now + int(accept["maxTimeoutSeconds"]),
        "nonce": "0x" + secrets.token_hex(32),
    }


def _build_payment_envelope(
    accept: dict[str, Any], msg: dict[str, Any], signature: str
) -> str:
    """Encode the X-PAYMENT envelope per x402 v2.

    The envelope is base64(json) so it travels safely in an HTTP header.
    Numeric fields are stringified (JS bigint safety) and bytes32 nonce
    is hex-encoded — both standard x402 conventions.
    """
    envelope = {
        "x402Version": 2,
        "scheme": accept["scheme"],
        "network": accept["network"],
        "payload": {
            "authorization": {
                "from": msg["from"],
                "to": msg["to"],
                "value": str(msg["value"]),
                "validAfter": str(msg["validAfter"]),
                "validBefore": str(msg["validBefore"]),
                "nonce": msg["nonce"],
            },
            "signature": signature,
        },
    }
    return base64.b64encode(json.dumps(envelope).encode()).decode()


# ── Main flow ─────────────────────────────────────────────────────────────


def main() -> int:
    log.info("x402 buyer demo — off-chain signing round-trip")
    log.info("Network: BSC testnet (chainId=%d), U token=%s", BSC_TESTNET_CHAIN_ID, U_TESTNET)

    # 1. Spin up the mock seller on loopback.
    server, base_url = _start_server()
    try:
        # 2. Build an in-memory wallet and the constrained X402Signer.
        #    The per-call cap is deliberately set just above the listing
        #    price — a real agent would size this to its risk budget.
        wallet = EVMWalletProvider(password=DEMO_PW, private_key=DEMO_PK, persist=False)
        signer = X402Signer(wallet, max_value_per_call={U_TESTNET: PRICE_BASE_UNITS})
        log.info("buyer wallet: %s (in-memory)", wallet.address)

        # 3. First GET — expect 402 with an accepts[] challenge.
        url = f"{base_url}/resource"
        status, body = _request_resource(url)
        if status != 402:
            log.error("expected 402, got %s body=%r", status, body)
            return 1
        log.info("step 1: GET %s → 402 (challenge received)", url)
        accept = body["accepts"][0]
        log.info("  challenge: pay %s base units of %s to %s on %s",
                 accept["amount"], accept["asset"], accept["payTo"], accept["network"])

        # 4. Construct the EIP-712 payload that satisfies the challenge.
        domain = {
            "name": accept["extra"]["name"],
            "version": accept["extra"]["version"],
            "chainId": BSC_TESTNET_CHAIN_ID,
            "verifyingContract": accept["asset"],
        }
        types = {
            "EIP712Domain": EIP712_DOMAIN_FIELDS,
            "TransferWithAuthorization": TWA_FIELDS,
        }
        message = _build_twa_message(wallet.address, accept)

        # 5. Sign through X402Signer. ``expected_to`` is PAY_TO — the buyer's
        #    OWN record of the payee, fixed before the 402 round-trip and
        #    independent of the server response. If a tampered / MITM 402 body
        #    altered ``message['to']`` (built from accept["payTo"]), this guard
        #    raises instead of silently signing to the attacker. Sourcing
        #    ``expected_to`` from accept["payTo"] would compare the response
        #    against itself and provide no protection.
        signed = signer.sign_payment(
            domain=domain,
            types=types,
            message=message,
            expected_to=PAY_TO,
        )
        # ``signature`` may come back as HexBytes; normalize to a 0x-hex
        # string for the JSON envelope.
        raw_sig = signed["signature"]
        sig = raw_sig.hex() if hasattr(raw_sig, "hex") and not isinstance(raw_sig, str) else raw_sig
        if not sig.startswith("0x"):
            sig = "0x" + sig
        log.info("step 2: signed TransferWithAuthorization (sig=%s…%s)", sig[:10], sig[-6:])

        # 6. Encode the X-PAYMENT envelope and retry.
        envelope = _build_payment_envelope(accept, message, sig)
        log.info("step 3: built X-PAYMENT envelope (%d bytes base64)", len(envelope))

        status, body = _request_resource(url, payment=envelope)
        if status != 200:
            log.error("expected 200 on retry, got %s body=%r", status, body)
            return 1
        if body.get("data") != SECRET_PAYLOAD:
            log.error("payload mismatch: got %r", body)
            return 1
        log.info("step 4: GET %s with X-PAYMENT → 200 data=%r", url, body["data"])

        log.info("=" * 60)
        log.info("x402 buyer demo: ALL STEPS OK ✓")
        return 0
    finally:
        server.shutdown()
        server.server_close()


if __name__ == "__main__":
    sys.exit(main())
