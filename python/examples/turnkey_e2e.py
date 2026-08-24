"""Live BSC-testnet E2E for the Python Turnkey wallet provider — 4 gated steps.

⚠️ SPENDS REAL MONEY-SHAPED RESOURCES. Every successful Turnkey signature is
BILLED against the org's quota (free tier: 25 signatures/month at 1
request/second; pay-as-you-go $0.10/signature). A full run consumes exactly
4 billed signatures plus a few 10⁻⁵ tBNB of gas for two self-transfers.
Calls are strictly serial with a ≥1.1 s gap; the chain-id assertion runs
BEFORE anything billable.

⚠️ Production posture: run with a NON-ROOT API user restricted by an
explicit ALLOW policy — root API keys bypass ALL Turnkey server-side
policies. The SDK-side SigningPolicy still applies either way.

Steps (each proves a self-built wire-format piece against the real enclave):
  1. EIP-191 blind digest signing recovers to the Turnkey address.
  2. EIP-712 signing: the self-built full-document payload (EIP712Domain
     included) parses in the enclave and binds the REAL domain.
  3. A legacy (gasPrice) self-transfer: the self-built unsigned RLP is
     accepted, signs, broadcasts over our own RPC and lands on-chain.
  4. An EIP-1559 self-transfer does the same for the typed encoding.

Usage (env in ``python/.env`` or the shell, never committed)::

    TURNKEY_E2E=1
    TURNKEY_API_PUBLIC_KEY=...   TURNKEY_API_PRIVATE_KEY=...
    TURNKEY_ORG_ID=...           TURNKEY_SIGN_WITH=0x...
    # optional: TURNKEY_API_BASE_URL, RPC_URL
    uv run python examples/turnkey_e2e.py

Requires the turnkey extra (``pip install 'bnbagent[turnkey]'``) and a
little tBNB on the TURNKEY_SIGN_WITH address (~0.001). Exits 0 only when
every step passes. Deliberately NOT part of CI.
"""

from __future__ import annotations

import os
import secrets
import sys
import time

from dotenv import load_dotenv
from eth_account import Account
from eth_account.messages import encode_defunct, encode_typed_data
from web3 import Web3

from bnbagent.networks.addresses import BNB_CHAIN_ADDRESSES
from bnbagent.wallets import TurnkeyWalletProvider

CHAIN_ID = 97
DEFAULT_RPC = "https://data-seed-prebsc-1-s1.binance.org:8545"
GAP_S = 1.1  # free tier: 1 request/second — stay under it
SIGNATURE_BUDGET = 4

_billed = 0
_last_vendor_call = 0.0


def vendor(label: str, fn):
    """Serialize vendor calls (≥1.1 s apart) and count the budget."""
    global _billed, _last_vendor_call
    if _billed >= SIGNATURE_BUDGET:
        raise RuntimeError(f"signature budget {SIGNATURE_BUDGET} exhausted before {label!r}")
    wait = _last_vendor_call + GAP_S - time.monotonic()
    if wait > 0:
        time.sleep(wait)
    _last_vendor_call = time.monotonic()
    result = fn()
    _billed += 1
    print(f"   [budget] {_billed}/{SIGNATURE_BUDGET} billed signatures")
    return result


def main() -> int:
    load_dotenv()
    if os.environ.get("TURNKEY_E2E") != "1":
        print(
            "TURNKEY_E2E != 1 — refusing to run (this script consumes billed "
            "Turnkey signatures and testnet gas). Set TURNKEY_E2E=1 plus the "
            "TURNKEY_* env vars to opt in."
        )
        return 0

    rpc_url = os.environ.get("RPC_URL") or DEFAULT_RPC
    w3 = Web3(Web3.HTTPProvider(rpc_url))

    # ── Gate 0: chain identity, BEFORE anything billable ────────────────
    chain_id = w3.eth.chain_id
    if chain_id != CHAIN_ID:
        raise RuntimeError(f"RPC {rpc_url} reports chainId={chain_id}, need {CHAIN_ID}")

    wallet = TurnkeyWalletProvider.from_env(expected_chain_id=CHAIN_ID)
    address = wallet.address
    balance = w3.eth.get_balance(address)
    print(
        f"turnkey e2e: signer={address} balance={w3.from_wei(balance, 'ether')} tBNB rpc={rpc_url}"
    )
    if balance < 3 * 10**14:
        raise RuntimeError(
            "signer balance is below the ~0.0003 tBNB needed for two "
            f"self-transfers — fund {address} first"
        )

    # ── 1. EIP-191 ───────────────────────────────────────────────────────
    message = f"turnkey-py-e2e {int(time.time())}"
    signed = vendor("eip191", lambda: wallet.sign_message(message))
    recovered = Account.recover_message(
        encode_defunct(text=message), signature=signed["signature"]
    )
    assert recovered == address, f"191 recovered {recovered}, want {address}"
    print(f"✅ 1/4 EIP-191 — recovered {recovered}")

    # ── 2. EIP-712 with real-domain binding ─────────────────────────────
    payment_token = BNB_CHAIN_ADDRESSES[CHAIN_ID].payment_token
    now = int(time.time())
    domain = {
        "name": "United Stables",
        "version": "1",
        "chainId": CHAIN_ID,
        "verifyingContract": payment_token,
    }
    types = {
        "TransferWithAuthorization": [
            {"name": "from", "type": "address"},
            {"name": "to", "type": "address"},
            {"name": "value", "type": "uint256"},
            {"name": "validAfter", "type": "uint256"},
            {"name": "validBefore", "type": "uint256"},
            {"name": "nonce", "type": "bytes32"},
        ],
    }
    message_712 = {
        "from": address,
        "to": address,
        "value": 1,
        "validAfter": now - 10,
        "validBefore": now + 580,
        "nonce": "0x" + secrets.token_hex(32),
    }
    signed_712 = vendor("eip712", lambda: wallet.sign_typed_data(domain, types, message_712))
    signable = encode_typed_data(domain_data=domain, message_types=types, message_data=message_712)
    recovered_712 = Account.recover_message(signable, signature=signed_712["signature"])
    assert recovered_712 == address, (
        f"712 recovered {recovered_712} against the REAL domain, want {address}"
    )
    print(f"✅ 2/4 EIP-712 — real-domain recovery {recovered_712}")

    # ── 3+4. legacy and 1559 self-transfers over our own RPC ────────────
    gas_price = w3.eth.gas_price
    nonce = w3.eth.get_transaction_count(address, "pending")
    transactions = [
        (
            "3/4 legacy tx",
            {
                "chainId": CHAIN_ID,
                "to": address,
                "value": 1,
                "gas": 21_000,
                "nonce": nonce,
                "gasPrice": gas_price,
            },
        ),
        (
            "4/4 eip-1559 tx",
            {
                "chainId": CHAIN_ID,
                "to": address,
                "value": 1,
                "gas": 21_000,
                "nonce": nonce + 1,
                "maxFeePerGas": gas_price * 2,
                "maxPriorityFeePerGas": gas_price,
            },
        ),
    ]
    for step, tx in transactions:
        signed_tx = vendor(step, lambda tx=tx: wallet.sign_transaction(tx))
        tx_hash = w3.eth.send_raw_transaction(signed_tx["rawTransaction"])
        receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=120)
        assert receipt["status"] == 1, f"{step}: reverted {tx_hash.hex()}"
        print(f"✅ {step} — landed 0x{tx_hash.hex().removeprefix('0x')}")

    print(f"\nall 4 steps PASS — {_billed} billed signatures used")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as error:  # noqa: BLE001 - top-level reporter
        print(f"E2E FAILED: {error}", file=sys.stderr)
        sys.exit(1)
