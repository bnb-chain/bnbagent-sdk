"""Tests for TwakX402Payer + make_x402_payer (design §3.2, F-2/F-3).

All quote/request payloads are field-verified captures from twak v0.18.0
(``tests/fixtures/twak_x402/``). The payer is exercised against a duck-typed
stub provider (its contract is just ``x402_quote``/``x402_request``); one
end-to-end test runs through a real ``TWAKProvider`` with the subprocess
boundary mocked, proving the whole delegation chain.
"""

from __future__ import annotations

import json
import types
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from bnbagent.signing import SigningPolicy
from bnbagent.wallets import (
    EVMWalletProvider,
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
PIEVERSE_TX = "0x09b1af61cdf080e4f3fc7dd999626268ed07ccfdb629b0c0c764b175830c22f8"

URL = "https://pay.pieverse.io/v1/topup/x402"


def _stub_provider(quote="quote_bsc_u.json", request="request_success_pieverse.json"):
    """Duck-typed provider: the payer's contract is just these two methods."""
    provider = types.SimpleNamespace()
    provider.x402_quote = MagicMock(return_value=_fixture(quote))
    provider.x402_request = MagicMock(return_value=_fixture(request))
    return provider


# ── quote() parsing ──


def test_quote_parses_base_usdc_challenge():
    provider = _stub_provider(quote="quote_base_usdc.json")
    payer = TwakX402Payer(provider)

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
    TwakX402Payer(provider).quote(URL, method="POST", body='{"amountUsd":0.1}')
    provider.x402_quote.assert_called_once_with(URL, method="POST", body='{"amountUsd":0.1}')


# ── request() happy path ──


def test_request_happy_path_pieverse():
    provider = _stub_provider()
    payer = TwakX402Payer(provider)

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

    # the request is pinned to the prechecked route (TOCTOU narrowing)
    kwargs = provider.x402_request.call_args.kwargs
    assert kwargs["max_payment"] == 10**18
    assert kwargs["method"] == "POST"
    assert kwargs["body"] == '{"amountUsd":0.1}'
    assert kwargs["prefer_network"] == "eip155:56"
    assert kwargs["prefer_asset"] == U_BSC


def test_request_passes_matching_expectations():
    # casing differences must not trip the byte-equality checks
    provider = _stub_provider()
    payer = TwakX402Payer(
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
    payer = TwakX402Payer(provider)
    with pytest.raises(X402NoPayableRouteError, match="accepts list is empty"):
        payer.request("https://www.x402.org/protected", max_payment=10**6)
    provider.x402_request.assert_not_called()


def test_pay_to_mismatch_rejected():
    provider = _stub_provider()
    payer = TwakX402Payer(provider, expected_pay_to="0x" + "aa" * 20)
    with pytest.raises(X402RecipientMismatchError, match=PIEVERSE_PAY_TO):
        payer.request(URL, max_payment=10**18)
    provider.x402_request.assert_not_called()


def test_asset_mismatch_rejected():
    # asset == EIP-712 verifyingContract for eip3009: the SigningPolicy
    # domain-allowlist check relocated to the quote terms
    provider = _stub_provider()
    payer = TwakX402Payer(provider, expected_asset=USDC_BASE)
    with pytest.raises(X402PolicyError, match="verifyingContract"):
        payer.request(URL, max_payment=10**18)
    provider.x402_request.assert_not_called()


def test_amount_above_max_payment_rejected():
    provider = _stub_provider()  # quoted amount = 10**17
    payer = TwakX402Payer(provider)
    with pytest.raises(X402AmountExceededError, match="exceeds max_payment"):
        payer.request(URL, max_payment=10**17 - 1)
    provider.x402_request.assert_not_called()


def test_timeout_above_configured_cap_rejected():
    # pieverse claims a 300s window; a 200s cap must refuse it
    provider = _stub_provider()
    payer = TwakX402Payer(provider, max_timeout_seconds=200)
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
    payer = TwakX402Payer(provider)

    result = payer.request("https://skills.onesource.io/api/chain/chain-id", max_payment=2000)

    assert result.success is True
    assert result.amount == 1000  # the preferred (exact) option was selected
    assert result.transaction is None  # this endpoint body has no tx hash
    provider.x402_request.assert_called_once()


# ── session budget: reserve on the quoted amount, roll back on failure ──


def test_budget_reserved_by_quoted_amount_keyed_by_asset():
    tracker = SessionBudgetTracker(caps={U_BSC: 3 * 10**17})
    provider = _stub_provider()
    payer = TwakX402Payer(provider, session_budget=tracker)

    payer.request(URL, max_payment=10**18)

    # debited the QUOTED amount (the CLI surfaces no settlement receipt)
    assert tracker.spent(U_BSC) == 10**17


def test_budget_rolled_back_when_provider_fails():
    tracker = SessionBudgetTracker(caps={U_BSC: 10**18})
    tracker.reserve(U_BSC, 5)  # pre-existing spend must survive the rollback
    provider = _stub_provider()
    provider.x402_request.side_effect = RuntimeError("twak command failed")
    payer = TwakX402Payer(provider, session_budget=tracker)

    with pytest.raises(RuntimeError, match="twak command failed"):
        payer.request(URL, max_payment=10**18)

    # the reservation was released; the tracker is back at its previous level
    assert tracker.spent(U_BSC) == 5


def test_budget_exhausted_raises_before_payment():
    tracker = SessionBudgetTracker(caps={U_BSC: 10**17 - 1})  # below the quote
    provider = _stub_provider()
    payer = TwakX402Payer(provider, session_budget=tracker)

    with pytest.raises(X402BudgetExhaustedError):
        payer.request(URL, max_payment=10**18)

    provider.x402_request.assert_not_called()
    assert tracker.spent(U_BSC) == 0  # a rejected payment never consumes budget


# ── make_x402_payer: the selection seam (capability gate + twak override) ──


def test_twak_make_x402_payer_applies_kwargs_without_cli_calls():
    with patch("bnbagent.wallets.twak_provider.subprocess.run") as run:
        twak = TWAKProvider()
        tracker = SessionBudgetTracker()
        payer = twak.make_x402_payer(session_budget=tracker, expected_pay_to=PIEVERSE_PAY_TO)
    assert isinstance(payer, TwakX402Payer)
    assert payer._provider is twak
    assert payer._session_budget is tracker
    assert payer._expected_pay_to == PIEVERSE_PAY_TO
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
    payer = TwakX402Payer(_stub_provider())
    assert isinstance(payer, X402Payer)


# ── end-to-end through a real TWAKProvider (subprocess boundary mocked) ──


def _completed(cmd, payload, returncode=0):
    return types.SimpleNamespace(
        args=cmd, returncode=returncode, stdout=json.dumps(payload), stderr=""
    )


def test_end_to_end_request_through_twak_provider():
    calls: list[list[str]] = []

    def run(cmd, **kwargs):
        calls.append(cmd)
        if cmd[1] == "wallet" and cmd[2] == "status":
            return _completed(cmd, {"success": True})
        if cmd[1] == "x402" and cmd[2] == "quote":
            return _completed(cmd, _fixture("quote_bsc_u.json"))
        if cmd[1] == "x402" and cmd[2] == "request":
            return _completed(cmd, _fixture("request_success_pieverse.json"))
        raise AssertionError(f"unexpected twak command: {cmd}")

    twak = TWAKProvider()
    payer = twak.make_x402_payer()
    with patch("bnbagent.wallets.twak_provider.subprocess.run", side_effect=run):
        result = payer.request(URL, max_payment=10**18)

    assert result.success is True
    assert result.transaction == PIEVERSE_TX
    assert result.asset == U_BSC

    # F-3: the quote ran first and WITHOUT a wallet probe
    assert calls[0][:3] == ["twak", "x402", "quote"]
    # the paid request was preceded by the wallet probe and pinned to the route
    request_cmd = next(c for c in calls if c[1] == "x402" and c[2] == "request")
    assert calls[calls.index(request_cmd) - 1][1:3] == ["wallet", "status"]
    assert ["--max-payment", str(10**18)] == request_cmd[
        request_cmd.index("--max-payment") : request_cmd.index("--max-payment") + 2
    ]
    assert "--yes" in request_cmd
    assert ["--prefer-network", "eip155:56"] == request_cmd[
        request_cmd.index("--prefer-network") : request_cmd.index("--prefer-network") + 2
    ]
    assert ["--prefer-asset", U_BSC] == request_cmd[
        request_cmd.index("--prefer-asset") : request_cmd.index("--prefer-asset") + 2
    ]
