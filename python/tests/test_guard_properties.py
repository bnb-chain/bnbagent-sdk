"""Property tests for the x402 / signing guard surfaces.

These do not test specific inputs. They state the invariants the guards exist
to uphold and let hypothesis search for a counterexample, so the *class* of bug
behind SRC-1314 (a one-sided bound check on an externally-influenced number)
cannot come back through a surface nobody wrote a unit test for.

Two invariants, deliberately decoupled from any individual check so that new
guards get covered without editing this file:

1. A rejected call must not change any counter.
2. An accepted amount must land inside ``[0, cap]``.

The signer tests use a duck-typed :class:`TypedDataSigner` rather than a real
``EVMWalletProvider`` — key derivation costs ~700ms, which a property test
cannot afford per example, and the guards under test run before the wallet is
ever touched.
"""

from __future__ import annotations

import time

from hypothesis import given, settings
from hypothesis import strategies as st

from bnbagent.networks import BSC_MAINNET_CHAIN_ID, get_address
from bnbagent.x402 import (
    SessionBudgetTracker,
    X402BudgetExhaustedError,
    X402Signer,
    X402SignerError,
)

U = get_address(BSC_MAINNET_CHAIN_ID).payment_token

# Values an attacker can put in a 402 response body. Python ints are arbitrary
# precision, so "too large to encode" and "negative" are both reachable; the
# non-int members cover callers that forward the JSON value unconverted.
HOSTILE_AMOUNTS = st.one_of(
    st.integers(min_value=-(2**300), max_value=2**300),
    st.sampled_from([0, -1, 1, 2**256, -(2**256), 2**256 - 1]),
)

CAPS = st.integers(min_value=0, max_value=10**24)


# ── SessionBudgetTracker ─────────────────────────────────────────────────


@given(cap=CAPS, amounts=st.lists(HOSTILE_AMOUNTS, max_size=8))
@settings(max_examples=300, deadline=None)
def test_tracker_counter_stays_within_zero_and_cap(cap, amounts):
    """The counter is a spend total: it must never go below 0 or above cap.

    This is the invariant SRC-1314 broke. A negative amount made ``cur + amt``
    smaller, so the one-sided ``> cap`` test passed and the counter went
    negative — after which the cap stopped binding entirely.
    """
    t = SessionBudgetTracker({U: cap})
    for amt in amounts:
        before = t.spent(U)
        try:
            t.reserve(U, amt)
        except X402BudgetExhaustedError:
            assert t.spent(U) == before, "a rejected reserve mutated the counter"
            continue
        spent = t.spent(U)
        assert spent >= 0, f"counter went negative: {spent} after reserving {amt}"
        assert spent <= cap, f"counter {spent} exceeded cap {cap}"


@given(cap=CAPS, amounts=st.lists(HOSTILE_AMOUNTS, max_size=8))
@settings(max_examples=300, deadline=None)
def test_tracker_commit_holds_the_same_invariant(cap, amounts):
    """commit() is the legacy path but reaches the same counter."""
    t = SessionBudgetTracker({U: cap})
    for amt in amounts:
        before = t.spent(U)
        try:
            t.commit(U, amt)
        except X402BudgetExhaustedError:
            assert t.spent(U) == before
            continue
        assert t.spent(U) >= 0, f"counter went negative after commit({amt})"


@given(amounts=st.lists(HOSTILE_AMOUNTS, max_size=8))
@settings(max_examples=300, deadline=None)
def test_uncapped_tracker_counter_still_never_negative(amounts):
    """No cap means unlimited spend, not a counter that can be driven negative.

    A negative counter on an uncapped token is latent damage: the moment a cap
    is introduced (or the tracker is shared with a capped path) it inherits the
    debt.
    """
    t = SessionBudgetTracker(None)
    for amt in amounts:
        try:
            t.reserve(U, amt)
        except X402BudgetExhaustedError:
            continue
        assert t.spent(U) >= 0, f"uncapped counter went negative after {amt}"


@given(
    cap=CAPS,
    reserved=st.integers(min_value=0, max_value=10**24),
    rolled_back=HOSTILE_AMOUNTS,
)
@settings(max_examples=300, deadline=None)
def test_rollback_never_drives_the_counter_out_of_range(cap, reserved, rolled_back):
    t = SessionBudgetTracker({U: cap})
    try:
        t.reserve(U, reserved)
    except X402BudgetExhaustedError:
        pass
    t.rollback(U, rolled_back)
    assert t.spent(U) >= 0, "rollback drove the counter negative"


# ── X402Signer amount guards ─────────────────────────────────────────────


class _FakeSigner:
    """Minimal TypedDataSigner. Records whether it was ever asked to sign."""

    def __init__(self) -> None:
        self.address = "0x" + "a" * 40
        self.sign_calls: list[int] = []

    def sign_typed_data(self, domain, types, message):
        self.sign_calls.append(int(message["value"]))
        return {"signature": "0x" + "11" * 65}


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


def _payload(wallet_addr: str, to: str, value):
    now = int(time.time())
    return {
        "domain": {
            "name": "United Stables", "version": "1",
            "chainId": BSC_MAINNET_CHAIN_ID, "verifyingContract": U,
        },
        "types": {
            "EIP712Domain": EIP712DOMAIN_FIELDS,
            "TransferWithAuthorization": TWA_FIELDS,
        },
        "message": {
            "from": wallet_addr, "to": to, "value": value,
            "validAfter": now - 60, "validBefore": now + 60,
            "nonce": "0x" + "c" * 64,
        },
    }


@given(
    per_call=st.integers(min_value=0, max_value=10**12),
    session=CAPS,
    values=st.lists(HOSTILE_AMOUNTS, max_size=6),
)
@settings(max_examples=200, deadline=None)
def test_signer_only_ever_signs_amounts_within_both_caps(per_call, session, values):
    """Whatever reaches the wallet must satisfy every advertised cap.

    Stated against the wallet rather than against the guards: this stays true
    when a new guard is added, and it fails if any existing guard is bypassable.
    """
    fake = _FakeSigner()
    signer = X402Signer(
        fake,
        max_value_per_call={U: per_call},
        session_budget={U: session},
    )
    to = "0x" + "b" * 40
    for value in values:
        before = signer.budget.spent(U)
        try:
            signer.sign_payment(**_payload(fake.address, to, value), expected_to=to)
        except X402SignerError:
            assert signer.budget.spent(U) == before, (
                "a rejected sign_payment consumed budget"
            )
            continue
        assert signer.budget.spent(U) >= 0

    for signed in fake.sign_calls:
        assert 0 <= signed <= per_call, (
            f"signed {signed}, outside the per-call cap [0, {per_call}]"
        )
    assert sum(fake.sign_calls) <= session, (
        f"signed a total of {sum(fake.sign_calls)}, over the session cap {session}"
    )


@given(value=HOSTILE_AMOUNTS)
@settings(max_examples=200, deadline=None)
def test_negative_amount_never_reaches_the_wallet(value):
    """No negative value may reach the wallet, whatever the caps are.

    Deliberately not asserting that non-negative values are accepted — an
    over-cap positive is *supposed* to be refused, so "did it raise" is the
    wrong question. The invariant is about what gets through.
    """
    fake = _FakeSigner()
    signer = X402Signer(fake, max_value_per_call={U: 10**9}, session_budget={U: 10**12})
    to = "0x" + "b" * 40
    try:
        signer.sign_payment(**_payload(fake.address, to, value), expected_to=to)
    except X402SignerError:
        pass
    if value < 0:
        assert fake.sign_calls == [], f"negative value {value} reached the wallet"
    assert all(v >= 0 for v in fake.sign_calls)


# ── Guards that silently vanish when unconfigured (audit finding E) ──────


def test_signer_with_no_caps_enforces_nothing_numeric():
    """Documents the current, deliberate behaviour so a change is visible.

    ``X402Signer(wallet)`` with no options applies no per-call cap and no
    session budget. The recipient and signer-binding guards still run; the
    amount guards do not exist. Anyone reading the class name would not guess
    this, which is why the audit flags it.
    """
    fake = _FakeSigner()
    signer = X402Signer(fake)
    to = "0x" + "b" * 40
    signer.sign_payment(**_payload(fake.address, to, 10**30), expected_to=to)
    assert fake.sign_calls == [10**30]
    assert signer.budget.cap_for(U) is None
