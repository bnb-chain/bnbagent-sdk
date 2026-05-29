"""End-to-end security validation for SigningPolicy + X402Signer.

Run this script after any change to the signing layer. It does NOT send any
transaction — purely off-chain sign / recover round-trips. The 6 assertions
exercise the canonical defense matrix on BSC testnet's real U-token EIP-712
domain.

Prerequisites:
    pip install -e .  # SDK installed in the current env

Usage:
    python examples/security_e2e.py

Exit code 0 + 6 assertions logged means the policy stack is intact.
"""

from __future__ import annotations

import logging
import os
import sys
import tempfile
import time

from eth_account import Account
from eth_account.messages import encode_typed_data
from web3 import Web3

from bnbagent import (
    EVMWalletProvider,
    PolicyViolation,
    SigningPolicy,
    X402Signer,
)
from bnbagent.networks import (
    BSC_TESTNET_CHAIN_ID,
    PAYMENT_TOKEN_EIP712_NAME,
    PAYMENT_TOKEN_EIP712_VERSION,
    get_address,
)
from bnbagent.x402 import (
    X402AmountExceededError,
    X402PolicyError,
    X402RecipientMismatchError,
)

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s | %(message)s"
)
log = logging.getLogger("security_e2e")

# ── Fixtures ───────────────────────────────────────────────────────────────

PW = "e2e-secure-pw"
# Ephemeral key — this script never broadcasts. Override via E2E_PRIVATE_KEY
# if you need a deterministic key for a specific repro.
PK = os.environ.get("E2E_PRIVATE_KEY") or Account.create().key.hex()
U_TESTNET = get_address(BSC_TESTNET_CHAIN_ID).payment_token
log.info("U testnet address: %s (name=%r version=%r)",
         U_TESTNET, PAYMENT_TOKEN_EIP712_NAME, PAYMENT_TOKEN_EIP712_VERSION)

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
PERMIT_FIELDS = [
    {"name": "owner", "type": "address"},
    {"name": "spender", "type": "address"},
    {"name": "value", "type": "uint256"},
    {"name": "nonce", "type": "uint256"},
    {"name": "deadline", "type": "uint256"},
]


def make_wallet(tmpdir: str, *, signing_policy=None) -> EVMWalletProvider:
    return EVMWalletProvider(
        password=PW,
        private_key=PK,
        wallets_dir=tmpdir,
        signing_policy=signing_policy,
    )


def twa_message(wallet: EVMWalletProvider, *, to: str | None = None, value: int = 100_000):
    now = int(time.time())
    return {
        "from": wallet.address,
        "to": to or ("0x" + "b" * 40),
        "value": value,
        "validAfter": now - 60,
        "validBefore": now + 60,
        "nonce": "0x" + "c" * 64,
    }


def twa_domain():
    return {
        "name": PAYMENT_TOKEN_EIP712_NAME,
        "version": PAYMENT_TOKEN_EIP712_VERSION,
        "chainId": BSC_TESTNET_CHAIN_ID,
        "verifyingContract": U_TESTNET,
    }


# ── Assertions ─────────────────────────────────────────────────────────────


def assert_1_default_signs_u_token_and_round_trips(tmpdir: str) -> None:
    log.info("=" * 60)
    log.info("Assertion 1: default wallet signs U-token TWA + recovers signer")
    wallet = make_wallet(tmpdir)
    domain = twa_domain()
    types = {"EIP712Domain": EIP712_DOMAIN_FIELDS, "TransferWithAuthorization": TWA_FIELDS}
    msg = twa_message(wallet)
    signed = wallet.sign_typed_data(domain, types, msg)
    # Round-trip recover
    message_types = {k: v for k, v in types.items() if k != "EIP712Domain"}
    signable = encode_typed_data(
        domain_data=domain, message_types=message_types, message_data=msg,
    )
    recovered = Account.recover_message(signable, signature=signed["signature"])
    assert recovered == wallet.address, (recovered, wallet.address)
    log.info("  → signed by %s, recovered %s ✓", wallet.address, recovered)


def assert_2_default_rejects_unknown_verifying_contract(tmpdir: str) -> None:
    log.info("=" * 60)
    log.info("Assertion 2: default wallet rejects unknown verifyingContract")
    wallet = make_wallet(tmpdir)
    domain = twa_domain()
    domain["verifyingContract"] = "0x" + "1" * 40
    types = {"EIP712Domain": EIP712_DOMAIN_FIELDS, "TransferWithAuthorization": TWA_FIELDS}
    try:
        wallet.sign_typed_data(domain, types, twa_message(wallet))
    except PolicyViolation as e:
        log.info("  → PolicyViolation as expected: %s", e)
        assert e.primary_type == "TransferWithAuthorization"
        assert e.chain_id == BSC_TESTNET_CHAIN_ID
        return
    raise AssertionError("expected PolicyViolation")


def assert_3_default_rejects_eip2612_permit(tmpdir: str) -> None:
    log.info("=" * 60)
    log.info("Assertion 3: default wallet rejects EIP-2612 Permit (denylist)")
    wallet = make_wallet(tmpdir)
    domain = twa_domain()  # U-token's real domain (Permit is supported on chain)
    types = {"EIP712Domain": EIP712_DOMAIN_FIELDS, "Permit": PERMIT_FIELDS}
    msg = {
        "owner": wallet.address,
        "spender": "0x" + "b" * 40,
        "value": 2**256 - 1,
        "nonce": 0,
        "deadline": 2_000_000_000,
    }
    try:
        wallet.sign_typed_data(domain, types, msg)
    except PolicyViolation as e:
        log.info("  → PolicyViolation as expected (denylist): %s", e)
        assert e.primary_type == "Permit"
        return
    raise AssertionError("expected PolicyViolation for Permit")


def assert_4_extended_policy_accepts_custom_contract(tmpdir: str) -> None:
    log.info("=" * 60)
    log.info("Assertion 4: extended policy accepts a custom verifyingContract")
    custom = Web3.to_checksum_address("0x" + "9" * 40)
    extended = SigningPolicy.strict_default().extend(
        domain_allowlist={(BSC_TESTNET_CHAIN_ID, custom)},
    )
    wallet = make_wallet(tmpdir, signing_policy=extended)
    domain = twa_domain()
    domain["verifyingContract"] = custom
    types = {"EIP712Domain": EIP712_DOMAIN_FIELDS, "TransferWithAuthorization": TWA_FIELDS}
    signed = wallet.sign_typed_data(domain, types, twa_message(wallet))
    log.info("  → signed against custom %s ✓ (sig=%s)", custom, signed["signature"][:18])


def assert_5_x402signer_rejects_overvalue(tmpdir: str) -> None:
    log.info("=" * 60)
    log.info("Assertion 5: X402Signer rejects value over max_value_per_call")
    wallet = make_wallet(tmpdir)
    signer = X402Signer(
        wallet,
        max_value_per_call={U_TESTNET: 1_000_000},
    )
    domain = twa_domain()
    types = {"EIP712Domain": EIP712_DOMAIN_FIELDS, "TransferWithAuthorization": TWA_FIELDS}
    msg = twa_message(wallet, value=2_000_000)
    try:
        signer.sign_payment(
            domain=domain, types=types, message=msg, expected_to=msg["to"],
        )
    except X402AmountExceededError as e:
        log.info("  → X402AmountExceededError as expected: %s", e)
        return
    raise AssertionError("expected X402AmountExceededError")


def assert_6_x402signer_rejects_recipient_mismatch(tmpdir: str) -> None:
    log.info("=" * 60)
    log.info("Assertion 6: X402Signer rejects expected_to mismatch")
    wallet = make_wallet(tmpdir)
    signer = X402Signer(wallet, max_value_per_call={U_TESTNET: 1_000_000})
    domain = twa_domain()
    types = {"EIP712Domain": EIP712_DOMAIN_FIELDS, "TransferWithAuthorization": TWA_FIELDS}
    msg = twa_message(wallet, to="0x" + "b" * 40)
    try:
        signer.sign_payment(
            domain=domain, types=types, message=msg,
            expected_to="0x" + "9" * 40,  # different!
        )
    except X402RecipientMismatchError as e:
        log.info("  → X402RecipientMismatchError as expected: %s", e)
        return
    raise AssertionError("expected X402RecipientMismatchError")


# ── Main ──────────────────────────────────────────────────────────────────


def main() -> int:
    log.info("SDK security_e2e — defense-in-depth signing validation")
    log.info("Wallet PK: %s...%s (in-memory only)", PK[:6], PK[-4:])
    log.info("Network: BSC testnet (chainId=%d)", BSC_TESTNET_CHAIN_ID)

    with tempfile.TemporaryDirectory() as tmp:
        for fn in [
            assert_1_default_signs_u_token_and_round_trips,
            assert_2_default_rejects_unknown_verifying_contract,
            assert_3_default_rejects_eip2612_permit,
            assert_4_extended_policy_accepts_custom_contract,
            assert_5_x402signer_rejects_overvalue,
            assert_6_x402signer_rejects_recipient_mismatch,
        ]:
            fn(tmp)

    log.info("=" * 60)
    log.info("ALL 6 ASSERTIONS PASSED ✓")
    return 0


if __name__ == "__main__":
    sys.exit(main())
