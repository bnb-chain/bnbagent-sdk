"""Tests for bnbagent.x402.X402Signer."""

from __future__ import annotations

import time

import pytest

from bnbagent import EVMWalletProvider
from bnbagent.networks import BSC_MAINNET_CHAIN_ID, get_address
from bnbagent.x402 import (
    X402AmountExceededError,
    X402BudgetExhaustedError,
    X402PolicyError,
    X402RecipientMismatchError,
    X402Signer,
)

PW = "test-secure-password-123"
PK = "0x" + "a" * 64

U_MAINNET = get_address(BSC_MAINNET_CHAIN_ID).payment_token

EIP712DOMAIN_FIELDS = [
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


@pytest.fixture
def wallet(tmp_path):
    return EVMWalletProvider(
        password=PW, private_key=PK, wallets_dir=tmp_path / "wallets",
    )


@pytest.fixture
def signer(wallet):
    return X402Signer(
        wallet,
        max_value_per_call={U_MAINNET: 1_000_000},
        session_budget={U_MAINNET: 5_000_000},
    )


def _payload(*, to=None, value=500_000, from_addr=None):
    now = int(time.time())
    return {
        "domain": {
            "name": "United Stables", "version": "1",
            "chainId": BSC_MAINNET_CHAIN_ID, "verifyingContract": U_MAINNET,
        },
        "types": {"EIP712Domain": EIP712DOMAIN_FIELDS, "TransferWithAuthorization": TWA_FIELDS},
        "message": {
            "from": from_addr or ("0x" + "a" * 40),
            "to": to or ("0x" + "b" * 40),
            "value": value,
            "validAfter": now - 60,
            "validBefore": now + 60,
            "nonce": "0x" + "c" * 64,
        },
    }


# ── Happy path ───────────────────────────────────────────────────────────


def test_sign_payment_succeeds_for_u_token_within_budget(signer):
    p = _payload(from_addr=signer.wallet_address)
    signed = signer.sign_payment(**p, expected_to=p["message"]["to"])
    assert "signature" in signed
    assert signer.budget.spent(U_MAINNET) == p["message"]["value"]


# ── Recipient mismatch ───────────────────────────────────────────────────


def test_rejects_when_expected_to_differs(signer):
    p = _payload(to="0x" + "b" * 40)
    with pytest.raises(X402RecipientMismatchError, match="does not match"):
        signer.sign_payment(**p, expected_to="0x" + "9" * 40)
    # Budget untouched
    assert signer.budget.spent(U_MAINNET) == 0


def test_recipient_check_is_case_insensitive(signer):
    """0xAB... and 0xab... must compare equal — checksum casing varies in the wild."""
    p = _payload(
        to="0xaAaAaAaAaAaAaAaAaAaAaAaAaAaAaAaAaAaAaAaA",
        from_addr=signer.wallet_address,
    )
    signed = signer.sign_payment(
        **p, expected_to="0xAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
    )
    assert "signature" in signed


def test_rejects_when_message_to_missing(signer):
    p = _payload()
    del p["message"]["to"]
    with pytest.raises(X402RecipientMismatchError, match="missing or not an address"):
        signer.sign_payment(**p, expected_to="0x" + "b" * 40)


# ── Signer binding (message['from']) ─────────────────────────────────────


def test_rejects_forged_from(signer):
    """message['from'] != wallet address must be refused before budget reserve."""
    p = _payload(from_addr="0x" + "d" * 40)  # not the wallet
    with pytest.raises(X402RecipientMismatchError, match="does not match wallet"):
        signer.sign_payment(**p, expected_to=p["message"]["to"])
    assert signer.budget.spent(U_MAINNET) == 0


def test_rejects_when_message_from_missing(signer):
    p = _payload(from_addr=signer.wallet_address)
    del p["message"]["from"]
    with pytest.raises(X402RecipientMismatchError, match="missing or not an address"):
        signer.sign_payment(**p, expected_to=p["message"]["to"])
    assert signer.budget.spent(U_MAINNET) == 0


# ── Per-call value cap ───────────────────────────────────────────────────


def test_rejects_when_value_exceeds_max_per_call(signer):
    p = _payload(value=2_000_000)  # cap is 1_000_000
    with pytest.raises(X402AmountExceededError, match="exceeds max_value_per_call"):
        signer.sign_payment(**p, expected_to=p["message"]["to"])
    assert signer.budget.spent(U_MAINNET) == 0


# ── Session budget ──────────────────────────────────────────────────────


def test_session_budget_accumulates_across_calls(signer):
    """Five calls of 500k each = 2.5M; budget is 5M, all should succeed."""
    for _ in range(5):
        p = _payload(value=500_000, from_addr=signer.wallet_address)
        signer.sign_payment(**p, expected_to=p["message"]["to"])
    assert signer.budget.spent(U_MAINNET) == 2_500_000


def test_session_budget_blocks_next_call_when_would_exceed(signer):
    """Budget 5M, per-call 1M. Spend 4M then try 1.5M (per-call would also
    fail but budget should trigger first only if budget < per-call)."""
    # First spend 5M cumulative (within both caps)
    for _ in range(5):
        p = _payload(value=1_000_000, from_addr=signer.wallet_address)
        signer.sign_payment(**p, expected_to=p["message"]["to"])
    assert signer.budget.spent(U_MAINNET) == 5_000_000
    # Sixth call: even 1 wei exceeds budget
    p = _payload(value=1, from_addr=signer.wallet_address)
    with pytest.raises(X402BudgetExhaustedError, match="session budget"):
        signer.sign_payment(**p, expected_to=p["message"]["to"])


def test_budget_not_consumed_when_underlying_wallet_rejects(wallet, tmp_path):
    """If wallet's SigningPolicy raises (e.g. unknown verifyingContract),
    budget tracker must remain at zero — failed signs don't deduct."""
    signer = X402Signer(
        wallet,
        max_value_per_call={"0x" + "1" * 40: 1_000_000},
        session_budget={"0x" + "1" * 40: 5_000_000},
    )
    p = _payload(value=500_000, from_addr=signer.wallet_address)
    p["domain"]["verifyingContract"] = "0x" + "1" * 40  # not in wallet allowlist
    with pytest.raises(X402PolicyError):
        signer.sign_payment(**p, expected_to=p["message"]["to"])
    from web3 import Web3
    assert signer.budget.spent(Web3.to_checksum_address("0x" + "1" * 40)) == 0


# ── PolicyViolation propagation ─────────────────────────────────────────


def test_wraps_wallet_policy_violation_as_x402_policy_error(wallet, tmp_path):
    """When the wallet rejects (e.g. Permit primary type), X402Signer
    surfaces X402PolicyError with the underlying PolicyViolation chained."""
    signer = X402Signer(
        wallet,
        max_value_per_call={U_MAINNET: 1_000_000},
    )
    permit_payload = {
        "domain": {
            "name": "United Stables", "version": "1",
            "chainId": BSC_MAINNET_CHAIN_ID, "verifyingContract": U_MAINNET,
        },
        "types": {
            "EIP712Domain": EIP712DOMAIN_FIELDS,
            "Permit": [
                {"name": "owner", "type": "address"},
                {"name": "spender", "type": "address"},
                {"name": "value", "type": "uint256"},
                {"name": "nonce", "type": "uint256"},
                {"name": "deadline", "type": "uint256"},
            ],
        },
        "message": {
            "owner": "0x" + "a" * 40,
            "spender": "0x" + "b" * 40,
            # X402Signer needs message['to'] and ['from'] to pass the recipient
            # and signer-binding checks; for this propagation test we route them
            # even though the Permit struct has neither — caller must supply an
            # expected_to that matches and a from equal to the wallet.
            "to": "0x" + "b" * 40,
            "from": signer.wallet_address,
            "value": 500_000,
            "nonce": 0, "deadline": 2_000_000_000,
        },
    }
    with pytest.raises(X402PolicyError) as exc:
        signer.sign_payment(**permit_payload, expected_to="0x" + "b" * 40)
    # Original PolicyViolation chained
    from bnbagent.signing import PolicyViolation
    assert isinstance(exc.value.__cause__, PolicyViolation)
    assert exc.value.__cause__.primary_type == "Permit"


# ── Concurrency regression (v0.4.1 / PR #34 review) ──────────────────────


def test_budget_atomic_under_concurrent_signs(wallet):
    """Two threads racing sign_payment with value==full-cap must result in
    exactly one signed payment, not two. Without atomic reserve/rollback,
    both threads pass would_exceed (spent=0), both sign, both commit →
    spent=2*cap. This test asserts the v0.4.1 fix.
    """
    import threading
    import time
    from concurrent.futures import ThreadPoolExecutor, as_completed

    from bnbagent.x402 import X402BudgetExhaustedError, X402Signer

    # Slow the wallet sign so any non-atomic check would lose the race —
    # without this the OS may serialise the two threads naturally.
    real_sign = wallet.sign_typed_data
    sign_calls: list[int] = []
    sign_lock = threading.Lock()

    def slow_sign(domain, types, message):
        time.sleep(0.05)  # widen the race window
        result = real_sign(domain, types, message)
        with sign_lock:
            sign_calls.append(1)
        return result

    wallet.sign_typed_data = slow_sign  # type: ignore[method-assign]

    cap = 1_000_000
    signer = X402Signer(
        wallet,
        max_value_per_call={U_MAINNET: cap},
        session_budget={U_MAINNET: cap},  # one call fits exactly
    )

    # Start barrier syncs both threads so they enter sign_payment together
    # (and therefore both contend on reserve()).
    start_barrier = threading.Barrier(2)

    def attempt() -> tuple[str, object]:
        start_barrier.wait()
        p = _payload(value=cap, from_addr=signer.wallet_address)
        try:
            res = signer.sign_payment(**p, expected_to=p["message"]["to"])
            return ("signed", res)
        except X402BudgetExhaustedError as e:
            return ("rejected", e)

    with ThreadPoolExecutor(max_workers=2) as ex:
        futures = [ex.submit(attempt) for _ in range(2)]
        outcomes = [f.result() for f in as_completed(futures)]

    signed = [o for o in outcomes if o[0] == "signed"]
    rejected = [o for o in outcomes if o[0] == "rejected"]
    assert len(signed) == 1, f"exactly one sign expected; got {[o[0] for o in outcomes]}"
    assert len(rejected) == 1
    # Spent counter never exceeds cap
    assert signer.budget.spent(U_MAINNET) == cap
    # The underlying wallet was invoked exactly once — the rejected thread
    # short-circuited at reserve() and never reached sign_typed_data.
    assert len(sign_calls) == 1


def test_budget_rolls_back_when_wallet_raises_nonpolicy_exception(wallet):
    """Failures other than PolicyViolation must also release the reservation
    (caller could see RuntimeError / network error / KeyboardInterrupt)."""
    from bnbagent.x402 import X402Signer

    class Boom(RuntimeError):
        pass

    def explode(domain, types, message):
        raise Boom("transport failed")

    wallet.sign_typed_data = explode  # type: ignore[method-assign]

    signer = X402Signer(
        wallet,
        max_value_per_call={U_MAINNET: 1_000_000},
        session_budget={U_MAINNET: 1_000_000},
    )
    p = _payload(value=500_000, from_addr=signer.wallet_address)
    with pytest.raises(Boom):
        signer.sign_payment(**p, expected_to=p["message"]["to"])
    # Reservation was released
    assert signer.budget.spent(U_MAINNET) == 0
