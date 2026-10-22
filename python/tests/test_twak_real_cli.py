"""TwakX402Payer against the real twak CLI, fully offline.

Runs only when TWAK_CLI_BIN points at an installed ``twak``:
fixtures/twak_x402/intercept_fetch.cjs is preloaded into the CLI, serves the
x402 endpoint in-process and blocks every other host, and the "server"
changes its terms between the SDK's quote and the CLI's paid request. CI
runs it against the verified release (python-ci.yml) and weekly against
@latest (twak-cli-latest.yml, with TWAK_CLI_ALLOW_UNVERIFIED=1) to catch a
changed pre-signing prompt.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from bnbagent.wallets import TwakPaymentStateUnknownError, TWAKProvider
from bnbagent.x402 import (
    SessionBudgetTracker,
    X402PolicyError,
    X402RecipientMismatchError,
)

TWAK = os.environ.get("TWAK_CLI_BIN", "")
INTERCEPT = Path(__file__).parent / "fixtures" / "twak_x402" / "intercept_fetch.cjs"
U = "0xce24439f2d9c6a2289f741120fe202248b666666"
PAY_TO = "0x4ba07885c7a05734be6ea6f46f749564e1476751"
PASSWORD = "Offline-Harness-Passw0rd!"
CLI_TIMEOUT = 60

pytestmark = pytest.mark.skipif(not TWAK, reason="set TWAK_CLI_BIN to run against a real twak")


@pytest.fixture(scope="module")
def cli_home(tmp_path_factory):
    home = tmp_path_factory.mktemp("twak-real-cli")
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("NODE_OPTIONS", f"--require {INTERCEPT}")
        mp.setenv("TWAK_ACCESS_ID", "offline")
        mp.setenv("TWAK_HMAC_SECRET", "offline")
        mp.setenv("TWAK_WALLET_PASSWORD", PASSWORD)
        mp.setenv("X402_MOCK_LOG", str(home / "x402.log"))
        subprocess.run(
            [TWAK, "wallet", "create", "--password", PASSWORD, "--no-keychain", "--json"],
            env={**os.environ, "HOME": str(home)},
            capture_output=True,
            check=True,
            timeout=CLI_TIMEOUT,
        )
        yield home


def _pay(home: Path, mode: str, monkeypatch) -> dict:
    monkeypatch.setenv("X402_MOCK_MODE", mode)
    log = home / "x402.log"
    log.write_text("")
    budget = SessionBudgetTracker(caps={U: 10**6})
    payer = TWAKProvider(
        twak_bin=TWAK,
        home=home,
        timeout=CLI_TIMEOUT,
        allow_unverified_cli_version=os.environ.get("TWAK_CLI_ALLOW_UNVERIFIED") == "1",
    ).make_x402_payer(expected_pay_to=PAY_TO, expected_asset=U, session_budget=budget)
    outcome: dict = {}
    try:
        outcome["result"] = payer.request(f"https://x402.test/{mode}", max_payment=10**6)
    except Exception as e:  # noqa: BLE001 - the tests assert on the type
        outcome["error"] = e
    entries = [json.loads(line) for line in log.read_text().splitlines()]
    outcome["paid"] = [e["paid"] for e in entries if "paid" in e]
    outcome["spent"] = budget.spent(U)
    return outcome


def test_pays_exactly_the_validated_terms(cli_home, monkeypatch):
    out = _pay(cli_home, "honest", monkeypatch)
    assert "error" not in out, out.get("error")
    assert out["result"].response == {"data": "paid"}
    [paid] = out["paid"]
    assert paid["payload"]["authorization"]["to"].lower() == PAY_TO
    assert paid["payload"]["authorization"]["value"] == "1000"
    assert paid["accepted"]["asset"].lower() == U
    assert out["spent"] == 1000


@pytest.mark.parametrize(
    ("mode", "error", "match"),
    [
        ("payto", X402RecipientMismatchError, "payTo"),
        # a different token whose name contains the expected address
        ("asset_fuzzy", X402PolicyError, "asset"),
        # forged prompt lines in the token name
        ("inject", X402PolicyError, "not recognised"),
        # refused by twak's own --max-payment (the quoted amount)
        ("amount_higher", RuntimeError, "exceeds --max-payment"),
        # only the transfer method changes: refused by the --prefer-method pin
        ("method_switch", RuntimeError, "preferMethod=eip3009"),
    ],
)
def test_declines_drifted_terms_before_signing(cli_home, monkeypatch, mode, error, match):
    out = _pay(cli_home, mode, monkeypatch)
    assert isinstance(out.get("error"), error), out
    assert match in str(out["error"])
    assert out["paid"] == []
    assert out["spent"] == 0


def test_lower_final_amount_is_paid_and_budgeted_as_signed(cli_home, monkeypatch):
    out = _pay(cli_home, "amount_lower", monkeypatch)
    assert "error" not in out, out.get("error")
    assert out["result"].amount == 999
    [paid] = out["paid"]
    assert paid["payload"]["authorization"]["value"] == "999"
    assert out["spent"] == 999


@pytest.mark.skipif(
    os.environ.get("TWAK_CLI_ALLOW_UNVERIFIED") == "1",
    reason="an unverified twak keeps the budget for a response without a prompt",
)
def test_response_without_a_challenge_is_not_a_payment(cli_home, monkeypatch):
    out = _pay(cli_home, "unpaid", monkeypatch)
    assert isinstance(out.get("error"), RuntimeError), out
    assert "without asking to sign" in str(out["error"])
    assert out["paid"] == []
    assert out["spent"] == 0


def test_paid_error_body_is_returned_and_keeps_the_budget(cli_home, monkeypatch):
    out = _pay(cli_home, "paid_error_body", monkeypatch)
    assert out["result"].response == {"error": "credit top-up failed", "success": False}
    assert len(out["paid"]) == 1
    assert out["spent"] == 1000


def test_failure_after_the_payment_keeps_the_budget(cli_home, monkeypatch):
    out = _pay(cli_home, "paid_502", monkeypatch)
    assert isinstance(out.get("error"), TwakPaymentStateUnknownError), out
    assert len(out["paid"]) == 1
    assert out["spent"] == 1000
