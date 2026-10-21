"""Tests for TwakX402Payer + make_x402_payer (design §3.2, F-2/F-3).

All quote/request payloads are field-verified captures from twak v0.18.0
(``tests/fixtures/twak_x402/``). The payer is exercised against a duck-typed
stub provider (its contract is just ``x402_quote``/``x402_request``); one
end-to-end test runs through a real ``TWAKProvider`` with the subprocess
boundary mocked, proving the whole delegation chain.
"""

from __future__ import annotations

import json
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from bnbagent.signing import SigningPolicy
from bnbagent.wallets import (
    EVMWalletProvider,
    TwakPaymentNotSentError,
    TwakPaymentStateUnknownError,
    TwakPaymentTerms,
    TWAKProvider,
    UnsupportedWalletOperation,
)
from bnbagent.x402 import (
    SessionBudgetTracker,
    TwakX402Payer,
    X402AmountExceededError,
    X402BudgetExhaustedError,
    X402NoPayableRouteError,
    X402Payer,
    X402PolicyError,
    X402RecipientMismatchError,
)
from bnbagent.x402.twak import DEFAULT_MAX_TIMEOUT_SECONDS

_FIXTURES = Path(__file__).parent / "fixtures" / "twak_x402"


def _fixture(name: str) -> dict:
    return json.loads((_FIXTURES / name).read_text())


# Field-verified addresses from the captures.
USDC_BASE = "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"
U_BSC = "0xce24439f2d9c6a2289f741120fe202248b666666"
PIEVERSE_PAY_TO = "0x4ba07885c7a05734be6ea6f46f749564e1476751"
ONESOURCE_PAY_TO = "0x52E29e0d2Aa49bfBfC548C0A9F2196F4aa51f3ea"
PIEVERSE_TX = "0x09b1af61cdf080e4f3fc7dd999626268ed07ccfdb629b0c0c764b175830c22f8"

URL = "https://pay.pieverse.io/v1/topup/x402"


def _stub_provider(quote="quote_bsc_u.json", request="request_success_pieverse.json"):
    """Duck-typed provider: the payer's contract is just these two methods."""
    provider = types.SimpleNamespace()
    provider.x402_quote = MagicMock(return_value=_fixture(quote))
    provider.x402_request = MagicMock(return_value=_fixture(request))
    return provider


def _payer(provider, **overrides):
    return TwakX402Payer(
        provider,
        expected_pay_to=overrides.pop("expected_pay_to", PIEVERSE_PAY_TO),
        expected_asset=overrides.pop("expected_asset", U_BSC),
        **overrides,
    )


# ── quote() parsing ──


def test_trusted_recipient_and_asset_anchors_are_required_and_validated():
    provider = _stub_provider()
    with pytest.raises(TypeError, match="expected_pay_to"):
        TwakX402Payer(provider)  # type: ignore[call-arg]
    with pytest.raises(ValueError):
        TwakX402Payer(
            provider,
            expected_pay_to="not-an-address",
            expected_asset=U_BSC,
        )


def test_quote_parses_base_usdc_challenge():
    provider = _stub_provider(quote="quote_base_usdc.json")
    payer = _payer(provider)

    quoted = payer.quote("https://skills.onesource.io/api/chain/chain-id")

    provider.x402_quote.assert_called_once_with(
        "https://skills.onesource.io/api/chain/chain-id", method="GET", body=None
    )
    assert quoted.url == "https://skills.onesource.io/api/chain/chain-id"
    assert quoted.mime_type == "application/json"
    assert len(quoted.accepts) == 2
    exact, batch = quoted.accepts
    # amounts are parsed from the CLI's decimal string into ints
    assert exact.amount == 1000 and isinstance(exact.amount, int)
    assert batch.amount == 1000
    assert exact.preferred is True
    assert batch.preferred is False
    assert exact.scheme == "exact" and batch.scheme == "batch-settlement"
    assert exact.asset == USDC_BASE
    assert exact.network == "eip155:8453"
    assert exact.max_timeout_seconds == 3600
    # the raw CLI JSON is preserved for fields not modeled (e.g. nextAction)
    assert quoted.raw == _fixture("quote_base_usdc.json")
    assert quoted.raw["nextAction"] == "Show these payment options to the user."


def test_quote_forwards_method_and_body():
    provider = _stub_provider(quote="quote_bsc_u.json")
    _payer(provider).quote(URL, method="POST", body='{"amountUsd":0.1}')
    provider.x402_quote.assert_called_once_with(URL, method="POST", body='{"amountUsd":0.1}')


# ── request() happy path ──


def test_request_happy_path_pieverse():
    provider = _stub_provider()
    payer = _payer(provider)

    result = payer.request(URL, max_payment=10**18, method="POST", body='{"amountUsd":0.1}')

    assert result.success is True
    # the response is the endpoint body VERBATIM (no payment receipt, gaps S-7)
    assert result.response == _fixture("request_success_pieverse.json")
    # payment metadata comes from the QUOTED option, not from settlement
    assert result.amount == 100000000000000000
    assert result.asset == U_BSC
    assert result.network == "eip155:56"
    assert result.pay_to == PIEVERSE_PAY_TO
    # transaction is best-effort from the endpoint's own body (tx_hash)
    assert result.transaction == PIEVERSE_TX

    # the request is pinned to the prechecked route (TOCTOU narrowing) and
    # twak's cap is the quoted amount, not the caller's larger max_payment
    kwargs = provider.x402_request.call_args.kwargs
    assert kwargs["max_payment"] == 10**17
    assert kwargs["method"] == "POST"
    assert kwargs["body"] == '{"amountUsd":0.1}'
    assert kwargs["prefer_network"] == "eip155:56"
    assert kwargs["prefer_asset"] == U_BSC


@pytest.mark.parametrize(("quoted", "max_payment"), [(1, 3), (0, 100), (5, 5)])
def test_cli_cap_is_the_quoted_amount_not_the_caller_cap(quoted, max_payment):
    # SRC-1333 / SRC-1544: twak re-fetches the challenge and only checks its
    # amount against --max-payment, so the cap must equal what was reserved.
    quote = _fixture("quote_bsc_u.json")
    quote["accepts"][0]["amount"] = str(quoted)
    provider = _stub_provider()
    provider.x402_quote.return_value = quote
    tracker = SessionBudgetTracker(caps={U_BSC: 10**18})
    payer = _payer(provider, session_budget=tracker)

    result = payer.request(URL, max_payment=max_payment)

    assert provider.x402_request.call_args.kwargs["max_payment"] == quoted
    assert result.amount == quoted
    assert tracker.spent(U_BSC) == quoted


ATTACKER = "0x" + "33" * 20


def _two_route_quote(permit2_pay_to=ATTACKER):
    """Static challenge: A = trusted EIP-3009 route, B = Permit2 route."""
    quote = _fixture("quote_bsc_u.json")
    route_a = dict(quote["accepts"][0])
    route_b = dict(route_a, transferMethod="permit2-exact", payTo=permit2_pay_to, preferred=False)
    quote["accepts"] = [route_a, route_b]
    return quote


def test_prefer_method_selects_the_route_before_the_precheck():
    # SRC-1356: twak filters by --prefer-method before choosing the route, so
    # the precheck must validate that same route, not the default one.
    provider = _stub_provider()
    provider.x402_quote.return_value = _two_route_quote()
    payer = _payer(provider)

    with pytest.raises(X402RecipientMismatchError, match=ATTACKER[2:]):
        payer.request(URL, max_payment=10**18, prefer_method="permit2")
    provider.x402_request.assert_not_called()


@pytest.mark.parametrize("prefer", ["permit2", "PERMIT2-EXACT", "permit2-exact"])
def test_prefer_method_passes_a_trusted_route_and_forwards_the_canonical_method(prefer):
    provider = _stub_provider()
    provider.x402_quote.return_value = _two_route_quote(permit2_pay_to=PIEVERSE_PAY_TO)
    payer = _payer(provider)

    result = payer.request(URL, max_payment=10**18, prefer_method=prefer)

    assert result.pay_to == PIEVERSE_PAY_TO
    assert provider.x402_request.call_args.kwargs["prefer_method"] == "permit2-exact"


def test_no_method_preference_pins_the_default_routes_method():
    # Audit M02: without a pin twak may pay a re-fetched route that differs
    # only in its transfer method, which its prompt does not show.
    provider = _stub_provider()
    provider.x402_quote.return_value = _two_route_quote(permit2_pay_to=PIEVERSE_PAY_TO)
    payer = _payer(provider)

    result = payer.request(URL, max_payment=10**18)

    assert result.pay_to.lower() == PIEVERSE_PAY_TO.lower()
    assert provider.x402_request.call_args.kwargs["prefer_method"] == "eip3009"


@pytest.mark.parametrize(
    ("transfer_method", "pinned"),
    [("permit2", "permit2-exact"), ("permit2-exact", "permit2-exact"), (None, "eip3009")],
)
def test_the_prechecked_routes_canonical_method_is_pinned(transfer_method, pinned):
    quote = _fixture("quote_bsc_u.json")
    quote["accepts"][0]["transferMethod"] = transfer_method
    provider = _stub_provider()
    provider.x402_quote.return_value = quote

    _payer(provider).request(URL, max_payment=10**18)

    assert provider.x402_request.call_args.kwargs["prefer_method"] == pinned


def test_prefer_method_without_a_matching_route_is_rejected():
    provider = _stub_provider()  # single eip3009 route
    payer = _payer(provider)
    with pytest.raises(X402NoPayableRouteError, match="permit2-exact"):
        payer.request(URL, max_payment=10**18, prefer_method="permit2")
    provider.x402_request.assert_not_called()


def test_request_passes_matching_expectations():
    # casing differences must not trip the byte-equality checks
    provider = _stub_provider()
    payer = _payer(
        provider,
        expected_pay_to=PIEVERSE_PAY_TO.upper(),
        expected_asset=U_BSC.upper(),
    )
    result = payer.request(URL, max_payment=10**18)
    assert result.success is True
    provider.x402_request.assert_called_once()


# ── precheck rejections: each must reject BEFORE any payment attempt ──


def test_empty_accepts_raises_no_payable_route():
    # live x402.org capture: base-sepolia routes filtered out by the client
    provider = _stub_provider(quote="quote_no_supported_route.json")
    payer = _payer(provider)
    with pytest.raises(X402NoPayableRouteError, match="accepts list is empty"):
        payer.request("https://www.x402.org/protected", max_payment=10**6)
    provider.x402_request.assert_not_called()


def test_pay_to_mismatch_rejected():
    provider = _stub_provider()
    payer = _payer(provider, expected_pay_to="0x" + "aa" * 20)
    with pytest.raises(X402RecipientMismatchError, match=PIEVERSE_PAY_TO):
        payer.request(URL, max_payment=10**18)
    provider.x402_request.assert_not_called()


def test_asset_mismatch_rejected():
    # asset == EIP-712 verifyingContract for eip3009: the SigningPolicy
    # domain-allowlist check relocated to the quote terms
    provider = _stub_provider()
    payer = _payer(provider, expected_asset=USDC_BASE)
    with pytest.raises(X402PolicyError, match="verifyingContract"):
        payer.request(URL, max_payment=10**18)
    provider.x402_request.assert_not_called()


def test_amount_above_max_payment_rejected():
    provider = _stub_provider()  # quoted amount = 10**17
    payer = _payer(provider)
    with pytest.raises(X402AmountExceededError, match="exceeds max_payment"):
        payer.request(URL, max_payment=10**17 - 1)
    provider.x402_request.assert_not_called()


def test_timeout_above_configured_cap_rejected():
    # pieverse claims a 300s window; a 200s cap must refuse it
    provider = _stub_provider()
    payer = _payer(provider, max_timeout_seconds=200)
    with pytest.raises(X402PolicyError, match="maxTimeoutSeconds 300"):
        payer.request(URL, max_payment=10**18)
    provider.x402_request.assert_not_called()


def test_default_timeout_cap_accepts_3600s_endpoint():
    # F-2 regression guard: the default cap is 3600s, NOT SigningPolicy's
    # 600s — the live Bazaar spec endpoint (onesource) claims exactly 3600s
    # and must be payable with a default-constructed payer.
    assert DEFAULT_MAX_TIMEOUT_SECONDS == 3600
    provider = _stub_provider(quote="quote_base_usdc.json")
    provider.x402_request.return_value = {"chainId": "0x2105"}
    payer = _payer(
        provider,
        expected_pay_to=ONESOURCE_PAY_TO,
        expected_asset=USDC_BASE,
    )

    result = payer.request("https://skills.onesource.io/api/chain/chain-id", max_payment=2000)

    assert result.success is True
    assert result.amount == 1000  # the preferred (exact) option was selected
    assert result.transaction is None  # this endpoint body has no tx hash
    provider.x402_request.assert_called_once()


# ── session budget: reserve on the quoted amount, roll back on failure ──


def test_budget_reserved_by_quoted_amount_keyed_by_asset():
    tracker = SessionBudgetTracker(caps={U_BSC: 3 * 10**17})
    provider = _stub_provider()
    payer = _payer(provider, session_budget=tracker)

    payer.request(URL, max_payment=10**18)

    # debited the QUOTED amount (the CLI surfaces no settlement receipt)
    assert tracker.spent(U_BSC) == 10**17


def test_budget_rolled_back_when_nothing_was_sent():
    tracker = SessionBudgetTracker(caps={U_BSC: 10**18})
    tracker.reserve(U_BSC, 5)  # pre-existing spend must survive the rollback
    provider = _stub_provider()
    cause = RuntimeError("twak command failed")
    provider.x402_request.side_effect = TwakPaymentNotSentError(cause)
    payer = _payer(provider, session_budget=tracker)

    with pytest.raises(RuntimeError, match="twak command failed") as raised:
        payer.request(URL, max_payment=10**18)

    # callers see the underlying failure, and the reservation was released
    assert raised.value is cause
    assert tracker.spent(U_BSC) == 5


@pytest.mark.parametrize(
    "failure",
    [
        TwakPaymentStateUnknownError("twak command failed after confirming"),
        RuntimeError("unclassified failure"),
    ],
)
def test_budget_stays_reserved_when_a_payment_may_have_been_sent(failure):
    # SRC-1348: only a failure known to precede the payment releases budget
    tracker = SessionBudgetTracker(caps={U_BSC: 10**18})
    provider = _stub_provider()
    provider.x402_request.side_effect = failure
    payer = _payer(provider, session_budget=tracker)

    with pytest.raises(type(failure)):
        payer.request(URL, max_payment=10**18)

    assert tracker.spent(U_BSC) == 10**17


def test_budget_exhausted_raises_before_payment():
    tracker = SessionBudgetTracker(caps={U_BSC: 10**17 - 1})  # below the quote
    provider = _stub_provider()
    payer = _payer(provider, session_budget=tracker)

    with pytest.raises(X402BudgetExhaustedError):
        payer.request(URL, max_payment=10**18)

    provider.x402_request.assert_not_called()
    assert tracker.spent(U_BSC) == 0  # a rejected payment never consumes budget


# ── make_x402_payer: the selection seam (capability gate + twak override) ──


def test_twak_make_x402_payer_applies_kwargs_without_cli_calls():
    with patch("bnbagent.wallets.twak_provider.subprocess.run") as run:
        twak = TWAKProvider()
        tracker = SessionBudgetTracker()
        payer = twak.make_x402_payer(
            session_budget=tracker,
            expected_pay_to=PIEVERSE_PAY_TO,
            expected_asset=U_BSC,
        )
    assert isinstance(payer, TwakX402Payer)
    assert payer._provider is twak
    assert payer._session_budget is tracker
    assert payer._expected_pay_to.lower() == PIEVERSE_PAY_TO.lower()
    assert payer._max_timeout_seconds == DEFAULT_MAX_TIMEOUT_SECONDS  # F-2
    run.assert_not_called()  # construction-time seam: no probe, no CLI


def test_evm_make_x402_payer_raises_capability_gate():
    evm = EVMWalletProvider(
        password="test-secure-password-123",
        private_key="0x" + "ab" * 32,
        persist=False,
        signing_policy=SigningPolicy.permissive(),
    )
    with pytest.raises(UnsupportedWalletOperation, match="x402\\.pay"):
        evm.make_x402_payer()


def test_twak_payer_satisfies_x402_payer_protocol():
    # X402Payer is a @runtime_checkable Protocol — structural isinstance.
    payer = _payer(_stub_provider())
    assert isinstance(payer, X402Payer)


# ── end-to-end through a real TWAKProvider (subprocess boundary mocked) ──


def _completed(cmd, payload, returncode=0):
    return types.SimpleNamespace(
        args=cmd, returncode=returncode, stdout=json.dumps(payload), stderr=""
    )


def test_end_to_end_request_through_twak_provider():
    calls: list[list[str]] = []

    def run(cmd, **kwargs):
        if cmd[1:] == ["--version"]:
            return types.SimpleNamespace(args=cmd, returncode=0, stdout="0.20.0\n", stderr="")
        calls.append(cmd)
        if cmd[1] == "wallet" and cmd[2] == "status":
            return _completed(cmd, {"agentWallet": "configured"})
        if cmd[1] == "x402" and cmd[2] == "quote":
            return _completed(cmd, _fixture("quote_bsc_u.json"))
        raise AssertionError(f"unexpected twak command: {cmd}")

    twak = TWAKProvider()
    payer = twak.make_x402_payer(
        expected_pay_to=PIEVERSE_PAY_TO,
        expected_asset=U_BSC,
    )
    confirmed = MagicMock(return_value=_fixture("request_success_pieverse.json"))
    with (
        patch("bnbagent.wallets.twak_provider.subprocess.run", side_effect=run),
        patch.object(TWAKProvider, "_run_x402_confirmed", confirmed),
    ):
        result = payer.request(URL, max_payment=10**18)

    assert result.success is True
    assert result.transaction == PIEVERSE_TX
    assert result.asset == U_BSC

    # F-3: the quote ran first and WITHOUT a wallet probe
    assert calls[0][:3] == ["twak", "x402", "quote"]
    # the paid request was preceded by the wallet probe and pinned to the route
    assert calls[-1][1:3] == ["wallet", "status"]
    request_args = confirmed.call_args.args[0]
    assert ["--max-payment", str(10**17)] == request_args[
        request_args.index("--max-payment") : request_args.index("--max-payment") + 2
    ]
    # the payer confirms twak's final terms itself instead of --yes
    assert "--yes" not in request_args
    assert confirmed.call_args.args[1] == TwakPaymentTerms(
        amount=10**17, asset=U_BSC, network="eip155:56", pay_to=PIEVERSE_PAY_TO
    )
    assert ["--prefer-network", "eip155:56"] == request_args[
        request_args.index("--prefer-network") : request_args.index("--prefer-network") + 2
    ]
    assert ["--prefer-asset", U_BSC] == request_args[
        request_args.index("--prefer-asset") : request_args.index("--prefer-asset") + 2
    ]


# ── twak's pre-signing confirmation prompt (SRC-1544 / SRC-1399 / SRC-1356) ──
#
# These run a real subprocess (fixtures/twak_x402/fake_twak.py) that prints
# twak v0.20.0's prompt verbatim and pays only if it reads "y" on stdin.

_HONEST_PROMPT = {
    "amount": "100000000000000000",
    "token": "United Stables",
    "network": "eip155:56",
    "chainKey": "bsc",
    "asset": U_BSC,
    "payTo": PIEVERSE_PAY_TO,
}


def _fake_twak_bin(tmp_path) -> str:
    launcher = tmp_path / "twak"
    launcher.write_text(
        f'#!/bin/sh\nexec "{sys.executable}" "{_FIXTURES / "fake_twak.py"}" "$@"\n'
    )
    launcher.chmod(0o755)
    return str(launcher)


def _fake_twak_payer(
    tmp_path, monkeypatch, *, prompt=None, timeout=20, cap=10**18, allow_unverified=False, **env
):
    log = tmp_path / "twak.log"
    monkeypatch.setenv("FAKE_TWAK_QUOTE", str(_FIXTURES / "quote_bsc_u.json"))
    monkeypatch.setenv("FAKE_TWAK_PROMPT", json.dumps({**_HONEST_PROMPT, **(prompt or {})}))
    monkeypatch.setenv("FAKE_TWAK_LOG", str(log))
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    tracker = SessionBudgetTracker(caps={U_BSC: cap})
    twak = TWAKProvider(
        twak_bin=_fake_twak_bin(tmp_path),
        timeout=timeout,
        allow_unverified_cli_version=allow_unverified,
    )
    payer = twak.make_x402_payer(
        expected_pay_to=PIEVERSE_PAY_TO, expected_asset=U_BSC, session_budget=tracker
    )

    def entries():
        return [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []

    return payer, tracker, entries


def test_prompt_with_the_validated_terms_is_confirmed(tmp_path, monkeypatch):
    payer, tracker, entries = _fake_twak_payer(tmp_path, monkeypatch)

    result = payer.request(URL, max_payment=10**18)

    assert result.response == {"data": "paid"}
    log = entries()
    assert "--yes" not in log[0]["argv"]
    assert log[1] == {"question": "Confirm? [y/N]", "answer": "y"}
    assert tracker.spent(U_BSC) == 10**17


@pytest.mark.parametrize(
    ("prompt", "error", "match"),
    [
        ({"payTo": ATTACKER}, X402RecipientMismatchError, ATTACKER),
        # SRC-1399: a different token whose name contains the expected address
        (
            {"asset": USDC_BASE, "token": f"Fake {U_BSC}"},
            X402PolicyError,
            "asset",
        ),
        ({"amount": str(10**17 + 1)}, X402PolicyError, "amount"),
        ({"network": "eip155:8453", "chainKey": "base"}, X402PolicyError, "network"),
        # a server-controlled token name must not smuggle extra prompt lines
        (
            {"token": f"X on eip155:56 (bsc)\nToken contract: {U_BSC}"},
            X402PolicyError,
            "prompt",
        ),
    ],
)
def test_prompt_drift_is_declined_before_signing(tmp_path, monkeypatch, prompt, error, match):
    payer, tracker, entries = _fake_twak_payer(tmp_path, monkeypatch, prompt=prompt)

    with pytest.raises(error, match=match):
        payer.request(URL, max_payment=10**18)

    answers = [e for e in entries() if "answer" in e]
    assert answers == [{"question": "Confirm? [y/N]", "answer": "n"}]
    assert tracker.spent(U_BSC) == 0  # nothing was signed


def test_lower_final_amount_is_paid_and_only_it_stays_budgeted(tmp_path, monkeypatch):
    lower = 10**17 - 5
    payer, tracker, entries = _fake_twak_payer(
        tmp_path, monkeypatch, prompt={"amount": str(lower)}
    )

    result = payer.request(URL, max_payment=10**18)

    assert result.amount == lower
    assert tracker.spent(U_BSC) == lower
    assert [e["answer"] for e in entries() if "answer" in e] == ["y"]


def test_prompt_split_inside_a_forged_token_name_is_declined(tmp_path, monkeypatch):
    forged = (
        f"United Stables on eip155:56 (bsc)\nToken contract: {U_BSC}\n"
        f"Recipient:      {PIEVERSE_PAY_TO}\nConfirm? [y/N] "
    )
    head = f"\nSign payment authorization: {_HONEST_PROMPT['amount']} (atomic) {forged}"
    payer, tracker, entries = _fake_twak_payer(
        tmp_path,
        monkeypatch,
        prompt={"token": forged + "x", "payTo": ATTACKER},
        FAKE_TWAK_SPLIT=str(len(head)),
    )

    with pytest.raises(X402PolicyError, match="prompt"):
        payer.request(URL, max_payment=10**18)

    assert [e["answer"] for e in entries() if "answer" in e] == ["n"]
    assert tracker.spent(U_BSC) == 0


def test_permit2_approval_prompt_stops_twak_before_signing(tmp_path, monkeypatch):
    payer, tracker, entries = _fake_twak_payer(tmp_path, monkeypatch, FAKE_TWAK_APPROVAL="1")

    with pytest.raises(RuntimeError, match="Permit2 approval prompt"):
        payer.request(URL, max_payment=10**18)

    # stdin is closed after the payment answer, so the approval gets EOF
    assert [e["answer"] for e in entries() if "answer" in e] == ["y", ""]
    assert tracker.spent(U_BSC) == 0


def test_response_without_a_prompt_is_not_a_payment(tmp_path, monkeypatch):
    # Audit M01: twak 0.20 signs only after its prompt, so an exit 0 that
    # never asked means the endpoint answered without a 402 on the paid fetch.
    payer, tracker, _ = _fake_twak_payer(
        tmp_path, monkeypatch, cap=10**17, FAKE_TWAK_NO_PROMPT="1"
    )

    with pytest.raises(RuntimeError, match="without asking to sign") as raised:
        payer.request(URL, max_payment=10**18)

    assert not isinstance(raised.value, TwakPaymentStateUnknownError)
    assert tracker.spent(U_BSC) == 0
    # the cap equals one quote, so a kept reservation would block this one
    with pytest.raises(RuntimeError, match="without asking to sign"):
        payer.request(URL, max_payment=10**18)


def test_response_without_a_prompt_keeps_the_budget_on_an_unverified_twak(
    tmp_path, monkeypatch
):
    # Another release might sign without printing the prompt, so a
    # prompt-less exit 0 cannot be told from a payment there.
    payer, tracker, _ = _fake_twak_payer(
        tmp_path,
        monkeypatch,
        allow_unverified=True,
        FAKE_TWAK_VERSION="0.21.0",
        FAKE_TWAK_NO_PROMPT="1",
    )

    assert payer.request(URL, max_payment=10**18).response == {"data": "paid"}
    assert tracker.spent(U_BSC) == 10**17


# ── the twak release the prompt check was verified on ──


def test_unverified_twak_release_is_refused_before_paying(tmp_path, monkeypatch):
    payer, tracker, entries = _fake_twak_payer(tmp_path, monkeypatch, FAKE_TWAK_VERSION="0.21.0")

    with pytest.raises(RuntimeError, match=r"twak 0\.21\.0 is not verified") as raised:
        payer.request(URL, max_payment=10**18)

    assert not isinstance(raised.value, TwakPaymentNotSentError)
    assert not [e for e in entries() if "argv" in e]  # the paid request never ran
    assert tracker.spent(U_BSC) == 0


def test_unverified_twak_release_can_be_allowed_explicitly(tmp_path, monkeypatch):
    payer, _, _ = _fake_twak_payer(
        tmp_path, monkeypatch, allow_unverified=True, FAKE_TWAK_VERSION="0.21.0"
    )
    assert payer.request(URL, max_payment=10**18).response == {"data": "paid"}


def test_unverified_twak_release_can_be_allowed_by_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("BNBAGENT_TWAK_ALLOW_UNVERIFIED_CLI", "1")
    payer, _, _ = _fake_twak_payer(tmp_path, monkeypatch, FAKE_TWAK_VERSION="0.21.0")
    assert payer.request(URL, max_payment=10**18).response == {"data": "paid"}


def test_twak_version_is_checked_once_per_provider(tmp_path, monkeypatch):
    checks = tmp_path / "versions.log"
    payer, _, _ = _fake_twak_payer(tmp_path, monkeypatch, FAKE_TWAK_VERSION_LOG=str(checks))

    payer.request(URL, max_payment=10**18)
    payer.request(URL, max_payment=10**18)

    assert checks.read_text().splitlines() == ["version"]


# ── session budget follows the payment phase (SRC-1348) ──


@pytest.mark.parametrize(
    "body",
    [{"error": "credit top-up failed"}, {"success": False}, [1, 2], None],
)
def test_paid_error_body_keeps_the_budget_spent(tmp_path, monkeypatch, body):
    # the budget covers exactly one payment of the quoted amount
    payer, tracker, _ = _fake_twak_payer(
        tmp_path, monkeypatch, cap=10**17, FAKE_TWAK_BODY=json.dumps(body)
    )

    result = payer.request(URL, max_payment=10**18)

    assert result.response == body
    assert result.transaction is None
    assert tracker.spent(U_BSC) == 10**17
    with pytest.raises(X402BudgetExhaustedError):
        payer.request(URL, max_payment=10**18)


def test_failure_before_the_confirmation_releases_the_budget(tmp_path, monkeypatch):
    payer, tracker, entries = _fake_twak_payer(tmp_path, monkeypatch, FAKE_TWAK_FAIL="before")

    with pytest.raises(RuntimeError, match="exceeds --max-payment") as raised:
        payer.request(URL, max_payment=10**18)

    assert not isinstance(raised.value, TwakPaymentNotSentError)
    assert not [e for e in entries() if "answer" in e]
    assert tracker.spent(U_BSC) == 0


def test_failure_after_the_confirmation_keeps_the_budget(tmp_path, monkeypatch):
    payer, tracker, _ = _fake_twak_payer(tmp_path, monkeypatch, FAKE_TWAK_FAIL="after")

    with pytest.raises(TwakPaymentStateUnknownError, match="may have been sent"):
        payer.request(URL, max_payment=10**18)

    assert tracker.spent(U_BSC) == 10**17


def test_failure_after_confirming_a_lower_amount_keeps_only_that_amount(tmp_path, monkeypatch):
    lower = 10**17 - 5
    payer, tracker, _ = _fake_twak_payer(
        tmp_path, monkeypatch, prompt={"amount": str(lower)}, FAKE_TWAK_FAIL="after"
    )

    with pytest.raises(TwakPaymentStateUnknownError):
        payer.request(URL, max_payment=10**18)

    assert tracker.spent(U_BSC) == lower


def test_timeout_after_the_confirmation_keeps_the_budget(tmp_path, monkeypatch):
    payer, tracker, _ = _fake_twak_payer(tmp_path, monkeypatch, timeout=2, FAKE_TWAK_HANG="1")

    with pytest.raises(TwakPaymentStateUnknownError, match="timed out"):
        payer.request(URL, max_payment=10**18)

    assert tracker.spent(U_BSC) == 10**17


def test_provider_without_expected_terms_keeps_the_yes_flag(tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_TWAK_LOG", str(tmp_path / "twak.log"))
    twak = TWAKProvider(twak_bin=_fake_twak_bin(tmp_path))
    assert twak.x402_request(URL, max_payment=5) == {"data": "paid"}
    argv = json.loads((tmp_path / "twak.log").read_text().splitlines()[0])["argv"]
    assert "--yes" in argv


@pytest.mark.parametrize(
    "url",
    [
        "https://user:password@example.com/paid",
        "https://api.example/paid?access_token=secret",
    ],
)
def test_sensitive_x402_urls_never_reach_process_argv(url):
    twak = TWAKProvider()
    with patch("bnbagent.wallets.twak_provider.subprocess.run") as run:
        with pytest.raises(ValueError, match="process argv"):
            twak.x402_quote(url)
    run.assert_not_called()
