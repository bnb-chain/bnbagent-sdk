"""Per-provider capability conformance tests (design doc §3.3 / §3.4).

The capability model's anti-drift rules, asserted for every constructible
provider:

- the declared capability set matches the design table EXACTLY;
- every declared ``sign.*`` capability is actually usable (the call succeeds);
- every undeclared ``sign.*`` capability raises ``UnsupportedWalletOperation``
  (a ``NotImplementedError`` subclass, for back-compat) — and, for twak,
  without ever shelling out (P0);
- ``supports()`` is a pure membership test over ``capabilities()``; unknown
  values are False (absence = unsupported, the EIP-5792 omission rule);
- ``describe()["capabilities"]`` is the sorted capability list;
- ``sign.*`` auto-derivation from method overrides, including the
  override-is-capability counterexample;
- ``make_executor()`` gates on ``sign.transaction`` at construction time.

TWAK is constructed offline: ``subprocess.run`` is patched module-wide so a
test that accidentally reaches for the real CLI fails loudly instead.
"""

from __future__ import annotations

import json
import types
from typing import Any
from unittest.mock import MagicMock, Mock

import pytest
from eth_account import Account
from eth_account.messages import encode_defunct

from bnbagent.signing import SigningPolicy
from bnbagent.wallets import (
    EVMWalletProvider,
    ExecutionContext,
    TWAKProvider,
    UnsupportedWalletOperation,
    WalletProvider,
)
from bnbagent.wallets.capabilities import (
    BROADCAST_SELF,
    CALLS_ARBITRARY,
    INTENTS_ERC8004,
    INTENTS_ERC8183,
    PAYMASTER_SPONSOR,
    SIGN_MESSAGE,
    SIGN_TRANSACTION,
    SIGN_TYPED_DATA,
    X402_PAY,
)
from bnbagent.wallets.local_executor import LocalExecutor

PW = "test-secure-password-123"
_TEST_KEY = "0x" + "ab" * 32

SIGN_CAPABILITIES = frozenset({SIGN_MESSAGE, SIGN_TRANSACTION, SIGN_TYPED_DATA})

# The design-table capability sets (§3.4) — asserted EXACTLY, so an
# accidentally gained or lost capability fails this suite.
EXPECTED_CAPABILITIES: dict[str, frozenset[str]] = {
    "evm": frozenset(
        {SIGN_MESSAGE, SIGN_TRANSACTION, SIGN_TYPED_DATA, CALLS_ARBITRARY, PAYMASTER_SPONSOR}
    ),
    "twak": frozenset(
        {SIGN_MESSAGE, BROADCAST_SELF, INTENTS_ERC8004, INTENTS_ERC8183, X402_PAY}
    ),
}


@pytest.fixture(autouse=True)
def no_real_cli(monkeypatch):
    """Fail loudly if anything in this module reaches the real twak CLI.

    Calls are intercepted by a MagicMock whose side effect raises; tests that
    must prove "no CLI call at all" additionally assert ``call_count == 0``.
    Tests that need a scripted CLI response override ``side_effect``.
    """
    runner = MagicMock(
        side_effect=AssertionError("conformance test attempted a real twak CLI call")
    )
    monkeypatch.setattr("bnbagent.wallets.twak_provider.subprocess.run", runner)
    return runner


def _make_provider(kind: str) -> WalletProvider:
    if kind == "evm":
        # In-memory mode: private_key + persist=False, nothing touches disk.
        # Permissive policy so the declared-usable sign_typed_data probe is
        # not entangled with SigningPolicy (covered in test_signing_policy.py).
        return EVMWalletProvider(
            password=PW,
            private_key=_TEST_KEY,
            persist=False,
            signing_policy=SigningPolicy.permissive(),
        )
    return TWAKProvider(chain="bsc")


@pytest.fixture(params=sorted(EXPECTED_CAPABILITIES))
def kind(request) -> str:
    return request.param


@pytest.fixture
def provider(kind) -> WalletProvider:
    return _make_provider(kind)


# ── canonical invocations, one per sign.* capability ──

_TX = {
    "to": "0x" + "22" * 20,
    "value": 0,
    "gas": 21000,
    "gasPrice": 10**9,
    "nonce": 0,
    "chainId": 56,
}
_DOMAIN = {"name": "Conformance", "version": "1", "chainId": 56,
           "verifyingContract": "0x" + "33" * 20}
_TYPES = {
    "EIP712Domain": [
        {"name": "name", "type": "string"},
        {"name": "version", "type": "string"},
        {"name": "chainId", "type": "uint256"},
        {"name": "verifyingContract", "type": "address"},
    ],
    "Probe": [{"name": "value", "type": "uint256"}],
}
_MESSAGE = {"value": 1}

_SIGN_INVOCATIONS = {
    SIGN_MESSAGE: lambda p: p.sign_message("conformance probe"),
    SIGN_TRANSACTION: lambda p: p.sign_transaction(dict(_TX)),
    SIGN_TYPED_DATA: lambda p: p.sign_typed_data(_DOMAIN, _TYPES, _MESSAGE),
}


def _completed(payload: dict[str, Any]) -> types.SimpleNamespace:
    return types.SimpleNamespace(args=[], returncode=0, stdout=json.dumps(payload), stderr="")


def _prime_twak_for_sign_message(twak: TWAKProvider, runner: MagicMock) -> None:
    """Script the CLI so twak's sign.message is exercisable offline.

    The mocked twak output must be a *real* signature over the probe message
    (the provider ecrecovers it against the wallet address), so we sign with
    the test key and report that key's address as the wallet.
    """
    acct = Account.from_key(_TEST_KEY)
    twak._address = acct.address
    twak._ensured = True
    signed = Account.sign_message(
        encode_defunct(text="conformance probe"), private_key=_TEST_KEY
    )
    runner.side_effect = lambda cmd, **kwargs: _completed(
        {"success": True, "signature": bytes(signed.signature).hex()}
    )


# ── conformance: declared set, declared-usable, undeclared-raises ──


def test_capability_set_matches_design_table_exactly(kind, provider):
    assert provider.capabilities() == EXPECTED_CAPABILITIES[kind]


def test_declared_sign_capabilities_are_usable(kind, provider, no_real_cli):
    # Rule: a declared capability must be usable — calling the matching
    # method produces a signature dict, never UnsupportedWalletOperation.
    if kind == "twak":
        _prime_twak_for_sign_message(provider, no_real_cli)
    for capability in sorted(provider.capabilities() & SIGN_CAPABILITIES):
        result = _SIGN_INVOCATIONS[capability](provider)
        assert isinstance(result, dict)
        assert result.get("signature") is not None or result.get("rawTransaction") is not None


def test_undeclared_sign_capabilities_raise(kind, provider, no_real_cli):
    # Rule: an undeclared capability must raise UnsupportedWalletOperation
    # naming the capability — and (P0) without any CLI/network attempt.
    for capability in sorted(SIGN_CAPABILITIES - provider.capabilities()):
        with pytest.raises(UnsupportedWalletOperation, match=capability) as exc:
            _SIGN_INVOCATIONS[capability](provider)
        # Back-compat: existing `except NotImplementedError` callers keep working.
        assert isinstance(exc.value, NotImplementedError)
    assert no_real_cli.call_count == 0


def test_supports_agrees_with_capabilities(provider):
    declared = provider.capabilities()
    for capability in sorted(EXPECTED_CAPABILITIES["evm"] | EXPECTED_CAPABILITIES["twak"]):
        assert provider.supports(capability) == (capability in declared)
    # Unknown value: consumers ignore what they don't recognise, and absence
    # means unsupported — supports() must return False, never raise.
    assert provider.supports("vendor.unknown_capability") is False


def test_describe_capabilities_is_sorted_list(provider):
    assert provider.describe()["capabilities"] == sorted(provider.capabilities())


# ── sign.* auto-derivation (throwaway subclasses) ──


class _AddressOnly(WalletProvider):
    """Minimal constructible provider: only the abstract `address`."""

    @property
    def address(self) -> str:
        return "0x" + "11" * 20


def test_address_only_subclass_has_no_capabilities():
    assert _AddressOnly().capabilities() == frozenset()


def test_sign_message_override_derives_exactly_that_capability():
    class MessageOnly(_AddressOnly):
        def sign_message(self, message):
            return {"signature": "0x" + "00" * 65}

    assert MessageOnly().capabilities() == frozenset({SIGN_MESSAGE})


def test_override_to_raise_still_claims_the_capability():
    # The override-is-capability counterexample: derivation only sees that the
    # method was overridden, not what it does — so overriding sign_typed_data
    # just to raise FALSELY claims sign.typed_data. This is exactly WHY the
    # discipline says "don't override-to-raise": the base default already
    # raises a descriptive UnsupportedWalletOperation, and not overriding is
    # the only way to keep the capability out of capabilities().
    class OverridesToRaise(_AddressOnly):
        def sign_typed_data(self, domain, types, message):
            raise UnsupportedWalletOperation("sign.typed_data")

    assert SIGN_TYPED_DATA in OverridesToRaise().capabilities()


def test_extra_capabilities_union_with_derived():
    class Extra(_AddressOnly):
        _extra_capabilities = frozenset({BROADCAST_SELF, "acme.batch_sign"})

        def sign_message(self, message):
            return {"signature": "0x" + "00" * 65}

    wallet = Extra()
    assert wallet.capabilities() == frozenset(
        {SIGN_MESSAGE, BROADCAST_SELF, "acme.batch_sign"}
    )
    # Open set: the vendor-namespaced value is first-class in supports()...
    assert wallet.supports("acme.batch_sign") is True
    # ...and any string not declared is simply unsupported.
    assert wallet.supports("acme.other") is False


# ── make_executor construction gate ──


def test_signless_subclass_make_executor_raises_at_construction():
    # The default executor needs sign.transaction; the error must surface at
    # make_executor() — before any intent runs — not on first execution.
    with pytest.raises(UnsupportedWalletOperation, match=SIGN_TRANSACTION):
        _AddressOnly().make_executor(ExecutionContext(web3=Mock()))


def test_evm_make_executor_returns_local_executor(no_real_cli):
    executor = _make_provider("evm").make_executor(ExecutionContext(web3=Mock()))
    assert isinstance(executor, LocalExecutor)


def test_twak_make_executor_returns_itself(no_real_cli):
    twak = _make_provider("twak")
    assert twak.make_executor(ExecutionContext(web3=Mock())) is twak
    assert no_real_cli.call_count == 0  # construction-time seam, no CLI probe
