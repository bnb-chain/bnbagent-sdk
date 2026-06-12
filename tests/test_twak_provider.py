"""Tests for TWAKProvider and its integration as a self-broadcasting executor.

The twak CLI is mocked at the ``subprocess.run`` boundary so the full intent
flow (ContractInterface -> TWAKProvider -> twak CLI -> result mapping) is
exercised without a live binary or on-chain calls. Canned outputs follow the
field-verified twak v0.18.0 envelopes (``hash`` not ``txHash``, see gaps
REQ-3; the alias chain is covered explicitly in the parse-hardening section).
"""

from __future__ import annotations

import json
import os
import types
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from eth_account import Account
from eth_account.messages import encode_defunct

from bnbagent.erc8004.contract import ContractInterface
from bnbagent.wallets import (
    ExecutionContext,
    IntentExecutor,
    TWAKProvider,
    UnsupportedWalletOperation,
    WalletIdentityMismatch,
)
from bnbagent.wallets.intents import (
    ERC8004_REGISTER,
    ERC8004_SET_AGENT_URI,
    ERC8004_SET_METADATA,
    ERC8183_CLAIM_REFUND,
    ERC8183_COMPLETE,
    ERC8183_CREATE_JOB,
    ERC8183_DISPUTE,
    ERC8183_FUND,
    ERC8183_MARK_EXPIRED,
    ERC8183_REGISTER_JOB,
    ERC8183_REJECT,
    ERC8183_SET_BUDGET,
    ERC8183_SET_PROVIDER,
    ERC8183_SETTLE,
    ERC8183_SUBMIT,
    ERC8183_VOTE_REJECT,
    Intent,
)
from tests.conftest import FAKE_ADDRESS, FAKE_CONTRACT_ADDRESS

# Canonical twak --json outputs (field-verified v0.18.0 shapes).
_REGISTER_OUT = {
    "success": True, "agentId": 42, "hash": "0xreg", "owner": FAKE_ADDRESS, "chain": "bsc",
}
# spec spelling ("txHash"): exercises the hash-alias chain on the happy path
_SETMETA_OUT = {"success": True, "txHash": "0xmeta", "chain": "bsc"}
_SETURI_OUT = {"success": True, "txHash": "0xuri", "chain": "bsc"}
_ADDRESS_OUT = {"success": True, "address": FAKE_ADDRESS, "chain": "bsc"}
_TX_OUT = {"success": True, "hash": "0xfeed", "chain": "bsc"}

_ZERO_ADDRESS = "0x" + "00" * 20
_PROVIDER_ADDR = "0x" + "11" * 20
_EVALUATOR_ADDR = "0x" + "22" * 20
_HOOK_ADDR = "0x" + "33" * 20
_POLICY_ADDR = "0x" + "44" * 20

_WALLET_STATUS_CMD = ["twak", "wallet", "status", "--json"]


def _completed(cmd, stdout, returncode=0, stderr=""):
    return types.SimpleNamespace(
        args=cmd, returncode=returncode, stdout=json.dumps(stdout), stderr=stderr
    )


def _router():
    """Return (run_fn, calls) routing twak subcommands to canned JSON."""
    calls: list[list[str]] = []

    def run(cmd, **kwargs):
        calls.append(cmd)
        if "status" in cmd:  # lazy auto-create probe: model a configured wallet
            return _completed(cmd, {"agentWallet": "configured"})
        if "register" in cmd:
            return _completed(cmd, _REGISTER_OUT)
        if "set-metadata" in cmd:
            return _completed(cmd, _SETMETA_OUT)
        if "set-uri" in cmd:
            return _completed(cmd, _SETURI_OUT)
        if "address" in cmd:
            return _completed(cmd, _ADDRESS_OUT)
        raise AssertionError(f"unexpected twak command: {cmd}")

    return run, calls


def _intent_router(output):
    """Return (run_fn, calls): wallet-status probe succeeds, everything else
    gets ``output``."""
    calls: list[list[str]] = []

    def run(cmd, **kwargs):
        calls.append(cmd)
        if cmd[1] == "wallet" and cmd[2] == "status":
            return _completed(cmd, {"agentWallet": "configured"})
        return _completed(cmd, output)

    return run, calls


def _execute(twak, intent, run):
    with patch("bnbagent.wallets.twak_provider.subprocess.run", side_effect=run):
        return twak.execute(intent)


def _make_twak_contract(twak):
    """Build a ContractInterface whose wallet_provider is a TWAKProvider."""
    web3 = MagicMock()
    web3.provider.endpoint_uri = "https://fake-rpc.example.com"
    with patch.object(ContractInterface, "_get_default_abi", return_value=[]):
        with patch("bnbagent.erc8004.contract.Web3.to_checksum_address", side_effect=lambda x: x):
            ci = ContractInterface(
                web3=web3,
                contract_address=FAKE_CONTRACT_ADDRESS,
                wallet_provider=twak,
            )
    return ci, web3


# ── TWAKProvider is a self-broadcasting executor ──

def test_twak_provider_is_intent_executor():
    assert isinstance(TWAKProvider(), IntentExecutor)


def test_fund_bundles_approval_is_true():
    # The SDK facade keys off this to skip its own allowance top-up: twak's
    # `fund` does approve + deposit itself.
    assert TWAKProvider.fund_bundles_approval is True


def test_non_bsc_chain_rejected():
    with pytest.raises(ValueError, match="BNB Smart Chain only"):
        TWAKProvider(chain="ethereum")


def test_spec_spelling_bsc_testnet_rejected():
    # The spec says "bsc-testnet" but the real CLI rejects it with
    # CHAIN_UNSUPPORTED — the constructor must catch it and name the fix.
    with pytest.raises(ValueError, match="bsctestnet"):
        TWAKProvider(chain="bsc-testnet")


def test_bsctestnet_accepted_and_passed_through():
    twak = TWAKProvider(chain="bsctestnet")
    with patch("bnbagent.wallets.twak_provider.subprocess.run") as run:
        run.return_value = _completed(["twak"], _ADDRESS_OUT)
        twak.address  # noqa: B018 - property access triggers the lookup
    # the chain flag forwarded to twak is the one we constructed with
    assert "bsctestnet" in run.call_args[0][0]


def test_contract_selects_twak_as_executor():
    twak = TWAKProvider()
    ci, _ = _make_twak_contract(twak)
    assert ci._executor is twak


def test_make_executor_returns_self():
    twak = TWAKProvider()
    assert twak.make_executor(ExecutionContext(web3=MagicMock())) is twak


def test_make_executor_warns_on_paymaster(caplog):
    twak = TWAKProvider()
    context = ExecutionContext(web3=MagicMock(), paymaster=MagicMock())
    with caplog.at_level("WARNING", logger="bnbagent.wallets.twak_provider"):
        executor = twak.make_executor(context)
    assert executor is twak  # warned, not rejected: the operation still runs
    assert "paymaster" in caplog.text
    # v0.19.0 wording: mainnet gas is auto-sponsored via MegaFuel; testnet
    # sponsorship is still pending upstream (gaps REQ-2).
    assert "MegaFuel" in caplog.text
    assert "REQ-2" in caplog.text


# ── erc8004.register: atomic --metadata flags (v0.18.0, no replay) ──

def test_register_atomic_metadata_flags_and_field_mapping():
    run, calls = _router()
    twak = TWAKProvider()
    result = _execute(
        twak,
        Intent(
            name=ERC8004_REGISTER,
            kwargs={
                "agent_uri": "https://agent.example/card.json",
                "metadata": [
                    {"key": "built_with", "value": "https://github.com/bnb-chain/bnbagent-sdk#v1"},
                    {"key": "foo", "value": "bar"},
                ],
            },
        ),
        run,
    )

    assert result == {
        "success": True,
        "transactionHash": "0xreg",
        "receipt": None,
        "agentId": 42,
        "owner": FAKE_ADDRESS,
    }

    # one atomic register invocation: repeatable --metadata, no set-metadata replay
    assert calls[0] == _WALLET_STATUS_CMD
    assert calls[1] == [
        "twak", "erc8004", "register",
        "--uri", "https://agent.example/card.json",
        "--metadata", "built_with=https://github.com/bnb-chain/bnbagent-sdk#v1",
        "--metadata", "foo=bar",
        "--chain", "bsc", "--json",
    ]
    assert len(calls) == 2


def test_contract_register_end_to_end_no_web3_send():
    run, calls = _router()
    twak = TWAKProvider()
    ci, web3 = _make_twak_contract(twak)

    with patch("bnbagent.wallets.twak_provider.subprocess.run", side_effect=run):
        result = ci.register_agent(
            agent_uri="https://agent.example/card.json",
            metadata=[{"key": "foo", "value": "bar"}],
        )

    assert result["success"] is True
    assert result["agentId"] == 42
    assert result["transactionHash"] == "0xreg"

    # built_with is auto-injected -> foo + built_with ride the single register tx
    reg = next(c for c in calls if c[1] == "erc8004" and c[2] == "register")
    meta_values = [reg[i + 1] for i, arg in enumerate(reg) if arg == "--metadata"]
    assert meta_values[0] == "foo=bar"
    assert meta_values[1].startswith("built_with=")
    assert len(meta_values) == 2
    assert sum(c[1] == "erc8004" for c in calls) == 1  # atomic: no set-metadata replay
    # The local web3 broadcast path must never be touched.
    web3.eth.send_raw_transaction.assert_not_called()


# ── other erc8004 intents ──

def test_set_metadata_via_contract():
    run, calls = _router()
    twak = TWAKProvider()
    ci, _ = _make_twak_contract(twak)
    with patch("bnbagent.wallets.twak_provider.subprocess.run", side_effect=run):
        result = ci.set_metadata(agent_id=42, key="desc", value="hello")
    assert result["transactionHash"] == "0xmeta"
    meta = next(c for c in calls if "set-metadata" in c)
    assert meta[:3] == ["twak", "erc8004", "set-metadata"]
    assert meta[3] == "42"


def test_set_agent_uri_via_contract_uses_set_uri():
    run, calls = _router()
    twak = TWAKProvider()
    ci, _ = _make_twak_contract(twak)
    with patch("bnbagent.wallets.twak_provider.subprocess.run", side_effect=run):
        result = ci.set_agent_uri(agent_id=42, agent_uri="https://new.example/card.json")
    assert result["transactionHash"] == "0xuri"
    assert any("set-uri" in c for c in calls)


# ── erc8183 dispatch table: exact argv + normalised result ──

@pytest.mark.parametrize(
    ("name", "kwargs", "expected_argv"),
    [
        pytest.param(
            ERC8183_SET_PROVIDER,
            {"job_id": 137, "provider": _PROVIDER_ADDR, "opt_params": b""},
            ["set-provider", "137", "--provider", _PROVIDER_ADDR],
            id="set_provider-empty-opt-params-no-flag",
        ),
        pytest.param(
            ERC8183_SET_PROVIDER,
            {"job_id": 137, "provider": _PROVIDER_ADDR, "opt_params": b"\x01\x02"},
            [
                "set-provider", "137", "--provider", _PROVIDER_ADDR,
                "--opt-params", "0x0102",
            ],
            id="set_provider-opt-params-passthrough-s1",
        ),
        pytest.param(
            ERC8183_SET_BUDGET,
            {"job_id": 137, "amount": 10**18, "opt_params": b""},
            ["set-budget", "137", "--amount", str(10**18)],
            id="set_budget",
        ),
        pytest.param(
            ERC8183_SUBMIT,
            {"job_id": 137, "deliverable": b"\xab" * 32, "opt_params": b""},
            ["submit", "137", "--deliverable", "0x" + "ab" * 32],
            id="submit-32-byte-deliverable-as-0x-hex",
        ),
        pytest.param(
            ERC8183_COMPLETE,
            {"job_id": 137, "reason": b"\x00" * 32, "opt_params": b""},
            ["complete", "137"],
            id="complete-zero-reason-omitted",
        ),
        pytest.param(
            ERC8183_COMPLETE,
            {"job_id": 137, "reason": b"\x12" * 32, "opt_params": b""},
            ["complete", "137", "--reason", "0x" + "12" * 32],
            id="complete-nonzero-reason-as-0x-hex",
        ),
        pytest.param(
            ERC8183_COMPLETE,
            {"job_id": 137, "reason": b"\x12" * 32, "opt_params": b"\x01"},
            [
                "complete", "137", "--reason", "0x" + "12" * 32,
                "--opt-params", "0x01",
            ],
            id="complete-reason-then-opt-params",
        ),
        pytest.param(
            ERC8183_REJECT,
            {"job_id": 137, "reason": b"\x00" * 32, "opt_params": b""},
            ["reject", "137"],
            id="reject-zero-reason-omitted",
        ),
        pytest.param(
            ERC8183_REJECT,
            {"job_id": 137, "reason": b"\x12" * 32, "opt_params": b""},
            ["reject", "137", "--reason", "0x" + "12" * 32],
            id="reject-nonzero-reason-as-0x-hex",
        ),
        pytest.param(
            ERC8183_CLAIM_REFUND,
            {"job_id": 137},
            ["claim-refund", "137"],
            id="claim_refund",
        ),
        pytest.param(
            ERC8183_REGISTER_JOB,
            {"job_id": 137, "policy": _POLICY_ADDR},
            ["register-job", "137", "--policy", _POLICY_ADDR],
            id="register_job",
        ),
        pytest.param(
            ERC8183_SETTLE,
            {"job_id": 137, "evidence": b""},
            ["settle", "137"],
            id="settle-empty-evidence-omitted",
        ),
        pytest.param(
            ERC8183_SETTLE,
            {"job_id": 137, "evidence": b"\xca\xfe"},
            ["settle", "137", "--evidence", "0xcafe"],
            id="settle-with-evidence",
        ),
        pytest.param(
            ERC8183_MARK_EXPIRED,
            {"job_id": 137},
            ["mark-expired", "137"],
            id="mark_expired",
        ),
        pytest.param(
            ERC8183_DISPUTE,
            {"job_id": 137},
            ["dispute", "137"],
            id="dispute",
        ),
        pytest.param(
            ERC8183_VOTE_REJECT,
            {"job_id": 137},
            ["vote-reject", "137"],
            id="vote_reject",
        ),
    ],
)
def test_erc8183_intent_argv_and_result(name, kwargs, expected_argv):
    run, calls = _intent_router(_TX_OUT)
    twak = TWAKProvider()
    result = _execute(twak, Intent(name=name, kwargs=kwargs), run)
    assert result == {"success": True, "transactionHash": "0xfeed", "receipt": None}
    assert calls[0] == _WALLET_STATUS_CMD
    assert calls[1] == ["twak", "erc8183", *expected_argv, "--chain", "bsc", "--json"]
    assert len(calls) == 2


def test_create_job_omits_hook_for_zero_address():
    run, calls = _intent_router({"success": True, "hash": "0xjob", "jobId": 138})
    twak = TWAKProvider()
    result = _execute(
        twak,
        Intent(
            name=ERC8183_CREATE_JOB,
            kwargs={
                "provider": _PROVIDER_ADDR,
                "evaluator": _EVALUATOR_ADDR,
                "expired_at": 1750000000,
                "description": "index the docs",
                "hook": _ZERO_ADDRESS,
            },
        ),
        run,
    )
    assert result == {
        "success": True,
        "transactionHash": "0xjob",
        "receipt": None,
        "jobId": 138,
    }
    assert calls[1] == [
        "twak", "erc8183", "create-job",
        "--provider", _PROVIDER_ADDR,
        "--evaluator", _EVALUATOR_ADDR,
        "--expires-at", "1750000000",  # twak's flag name; carries expired_at
        "--description", "index the docs",
        "--chain", "bsc", "--json",
    ]


def test_create_job_includes_nonzero_hook():
    run, calls = _intent_router({"success": True, "hash": "0xjob", "jobId": 139})
    twak = TWAKProvider()
    result = _execute(
        twak,
        Intent(
            name=ERC8183_CREATE_JOB,
            kwargs={
                "provider": _PROVIDER_ADDR,
                "evaluator": _EVALUATOR_ADDR,
                "expired_at": 1750000000,
                "description": "index the docs",
                "hook": _HOOK_ADDR,
            },
        ),
        run,
    )
    assert result["jobId"] == 139
    assert calls[1] == [
        "twak", "erc8183", "create-job",
        "--provider", _PROVIDER_ADDR,
        "--evaluator", _EVALUATOR_ADDR,
        "--expires-at", "1750000000",
        "--description", "index the docs",
        "--hook", _HOOK_ADDR,
        "--chain", "bsc", "--json",
    ]


# ── erc8183.fund: --expected-budget atomic pin (gaps S-2, v0.19.0) ──

def _fund_intent(expected_budget, opt_params=b""):
    return Intent(
        name=ERC8183_FUND,
        kwargs={
            "job_id": 137,
            "expected_budget": expected_budget,
            "opt_params": opt_params,
        },
    )


def test_fund_pins_expected_budget_no_status_precheck_and_surfaces_approve_hash():
    run, calls = _intent_router(
        {"success": True, "hash": "0xfund", "approveHash": "0xappr"}
    )
    twak = TWAKProvider()
    result = _execute(twak, _fund_intent(1000), run)
    assert result == {
        "success": True,
        "transactionHash": "0xfund",
        "receipt": None,
        "approveHash": "0xappr",  # twak's fund is approve + deposit, two txs
    }
    # v0.19.0 (S-2): the contract reverts atomically on budget drift — the
    # old client-side `erc8183 status` pre-check is gone entirely.
    assert calls[0] == _WALLET_STATUS_CMD
    assert calls[1] == [
        "twak", "erc8183", "fund", "137",
        "--expected-budget", "1000", "--chain", "bsc", "--json",
    ]
    assert len(calls) == 2
    assert all(not (c[1] == "erc8183" and c[2] == "status") for c in calls)


def test_fund_opt_params_passthrough():
    run, calls = _intent_router({"success": True, "hash": "0xfund"})
    twak = TWAKProvider()
    _execute(twak, _fund_intent(1000, opt_params=b"\xca\xfe"), run)
    assert calls[1] == [
        "twak", "erc8183", "fund", "137",
        "--expected-budget", "1000", "--opt-params", "0xcafe",
        "--chain", "bsc", "--json",
    ]


def test_fund_without_approve_hash_omits_key():
    run, _ = _intent_router({"success": True, "hash": "0xfund"})
    twak = TWAKProvider()
    result = _execute(twak, _fund_intent(1000), run)
    assert "approveHash" not in result


def test_fund_budget_mismatch_revert_surfaces_selector():
    # v0.19.0: budget drift reverts on-chain with BudgetMismatch() and the
    # CLI passes the raw revert through as an error envelope (field-verified
    # error-passthrough shape per the gaps doc). The selector must reach the
    # caller so the mismatch is diagnosable.
    def run(cmd, **kwargs):
        if cmd[1] == "wallet" and cmd[2] == "status":
            return _completed(cmd, {"agentWallet": "configured"})
        return _completed(
            cmd,
            {"error": "execution reverted: 0x99b0fc87", "errorCode": "TX_FAILED"},
            returncode=1,
        )

    twak = TWAKProvider()
    with pytest.raises(RuntimeError, match="0x99b0fc87"):
        _execute(twak, _fund_intent(999), run)


# ── opt-params passthrough (REQ-1 / S-1, v0.19.0) ──

def test_submit_opt_params_passthrough_req1():
    # v0.19.0 (REQ-1): the deliverable_url JSON the SDK facade encodes into
    # optParams rides the submit tx verbatim — seller role works end-to-end.
    opt = b'{"deliverable_url":"ipfs://x"}'
    run, calls = _intent_router(_TX_OUT)
    twak = TWAKProvider()
    result = _execute(
        twak,
        Intent(
            name=ERC8183_SUBMIT,
            kwargs={"job_id": 137, "deliverable": b"\xab" * 32, "opt_params": opt},
        ),
        run,
    )
    assert result == {"success": True, "transactionHash": "0xfeed", "receipt": None}
    assert calls[1] == [
        "twak", "erc8183", "submit", "137",
        "--deliverable", "0x" + "ab" * 32,
        "--opt-params", "0x" + opt.hex(),
        "--chain", "bsc", "--json",
    ]


def test_set_budget_opt_params_passthrough_s1():
    run, calls = _intent_router(_TX_OUT)
    twak = TWAKProvider()
    _execute(
        twak,
        Intent(
            name=ERC8183_SET_BUDGET,
            kwargs={"job_id": 137, "amount": 1, "opt_params": b"x"},
        ),
        run,
    )
    assert calls[1] == [
        "twak", "erc8183", "set-budget", "137", "--amount", "1",
        "--opt-params", "0x78", "--chain", "bsc", "--json",
    ]


def test_empty_opt_params_emits_no_flag():
    run, calls = _intent_router(_TX_OUT)
    twak = TWAKProvider()
    _execute(
        twak,
        Intent(
            name=ERC8183_SET_BUDGET,
            kwargs={"job_id": 137, "amount": 1, "opt_params": b""},
        ),
        run,
    )
    assert "--opt-params" not in calls[1]


# ── guards: fail fast, no wallet probe, no CLI call ──


def test_unknown_intent_rejected_listing_supported():
    twak = TWAKProvider()
    with patch("bnbagent.wallets.twak_provider.subprocess.run") as run:
        with pytest.raises(UnsupportedWalletOperation) as exc:
            twak.execute(Intent(name="erc20.transfer", kwargs={"to": "0x1", "amount": 1}))
    message = str(exc.value)
    assert "arbitrary contract calls" in message
    assert "erc8004.register" in message  # supported intents are listed
    assert "erc8183.fund" in message
    run.assert_not_called()  # no wallet probe, no CLI call


def test_unnamed_intent_rejected():
    # A purely mechanical Intent (call only, name="") cannot be replayed by a
    # fixed-command-menu wallet.
    twak = TWAKProvider()
    with patch("bnbagent.wallets.twak_provider.subprocess.run") as run:
        with pytest.raises(UnsupportedWalletOperation, match="arbitrary contract calls"):
            twak.execute(Intent(call=MagicMock()))
    run.assert_not_called()


# ── output parse hardening (gaps REQ-3 + error-envelope variants) ──

@pytest.mark.parametrize("field", ["hash", "txHash", "transactionHash"])
def test_tx_hash_field_variants_extracted(field):
    run, _ = _intent_router({"success": True, field: "0xabc"})
    twak = TWAKProvider()
    result = _execute(twak, Intent(name=ERC8183_DISPUTE, kwargs={"job_id": 1}), run)
    assert result["transactionHash"] == "0xabc"


def test_error_field_with_zero_exit_is_failure():
    # erc8183 unknown-command-style envelopes carry `error` without `success`
    # and may still exit 0 — the error field alone must fail the call.
    run, _ = _intent_router({"error": "INSUFFICIENT_FUNDS"})
    twak = TWAKProvider()
    with pytest.raises(RuntimeError, match="INSUFFICIENT_FUNDS"):
        _execute(twak, Intent(name=ERC8183_DISPUTE, kwargs={"job_id": 1}), run)


def test_success_false_with_zero_exit_is_failure():
    run, _ = _intent_router({"success": False})
    twak = TWAKProvider()
    with pytest.raises(RuntimeError, match="twak command failed"):
        _execute(twak, Intent(name=ERC8183_DISPUTE, kwargs={"job_id": 1}), run)


def test_unknown_command_in_stderr_hints_upgrade():
    def run(cmd, **kwargs):
        if cmd[1] == "wallet" and cmd[2] == "status":
            return _completed(cmd, {"agentWallet": "configured"})
        return types.SimpleNamespace(
            args=cmd,
            returncode=1,
            stdout="",
            stderr="error: unknown command 'set-provider'",
        )

    twak = TWAKProvider()
    with pytest.raises(RuntimeError, match=r"upgrade twak to >= v0\.19\.0"):
        _execute(
            twak,
            Intent(
                name=ERC8183_SET_PROVIDER,
                kwargs={"job_id": 1, "provider": _PROVIDER_ADDR, "opt_params": b""},
            ),
            run,
        )


def test_nonzero_exit_raises_runtime_error():
    def run(cmd, **kwargs):
        return _completed(cmd, {"success": False, "error": "ExpiryTooShort()"}, returncode=1)

    twak = TWAKProvider()
    with patch("bnbagent.wallets.twak_provider.subprocess.run", side_effect=run):
        with pytest.raises(RuntimeError, match="ExpiryTooShort"):
            twak.execute(
                Intent(name=ERC8004_SET_METADATA, kwargs={"agent_id": 1, "key": "k", "value": "v"})
            )


def test_missing_binary_raises_helpful_error():
    twak = TWAKProvider()
    with patch("bnbagent.wallets.twak_provider.subprocess.run", side_effect=FileNotFoundError()):
        with pytest.raises(RuntimeError, match="twak CLI not found"):
            twak.address  # noqa: B018 - property access triggers the lookup


# ── signing surface ──

# Deterministic test key: the mocked twak output must be a *real* signature so
# the provider's ecrecover self-check (it computes the EIP-191 digest locally,
# twak signs out of process) can pass.
_TEST_KEY = "0x" + "ab" * 32


def _primed_twak(address):
    """A provider with the wallet probe and address lookup pre-satisfied, so
    sign_message is the only CLI call left."""
    twak = TWAKProvider()
    twak._address = address
    twak._ensured = True
    return twak


def test_sign_transaction_not_supported():
    # Phase 1b: twak no longer overrides sign_transaction (override-is-capability
    # discipline) — the base default raises, naming the missing capability.
    twak = TWAKProvider()
    with patch("bnbagent.wallets.twak_provider.subprocess.run") as run:
        with pytest.raises(UnsupportedWalletOperation, match="sign.transaction"):
            twak.sign_transaction({})
    run.assert_not_called()  # P0: no CLI call is ever attempted


def test_sign_message_normalises_and_self_checks():
    message = "hello twak"
    acct = Account.from_key(_TEST_KEY)
    signed = Account.sign_message(encode_defunct(text=message), private_key=_TEST_KEY)
    raw_sig = bytes(signed.signature).hex()  # no 0x prefix: exercises S-4 normalisation

    twak = _primed_twak(acct.address)

    def run(cmd, **kwargs):
        assert cmd == [
            "twak", "wallet", "sign-message",
            "--chain", "bsc", "--message", message, "--json",
        ]
        return _completed(cmd, {"success": True, "signature": raw_sig})

    with patch("bnbagent.wallets.twak_provider.subprocess.run", side_effect=run):
        result = twak.sign_message(message)

    assert result["signature"] == "0x" + raw_sig  # 0x prefix added
    # messageHash = locally computed EIP-191 digest
    assert result["messageHash"] == "0x" + bytes(signed.message_hash).hex()
    assert result["r"] == signed.r
    assert result["s"] == signed.s
    assert result["v"] == signed.v


def test_sign_message_recovery_mismatch_raises():
    # twak hands back a valid signature from the *wrong* key: the recovered
    # address differs from the wallet address, so the provider must refuse.
    message = "hello twak"
    acct = Account.from_key(_TEST_KEY)
    tampered = bytes(
        Account.sign_message(
            encode_defunct(text=message), private_key="0x" + "cd" * 32
        ).signature
    ).hex()

    twak = _primed_twak(acct.address)

    def run(cmd, **kwargs):
        return _completed(cmd, {"success": True, "signature": tampered})

    with patch("bnbagent.wallets.twak_provider.subprocess.run", side_effect=run):
        with pytest.raises(RuntimeError, match="self-check failed"):
            twak.sign_message(message)


def test_sign_message_malformed_signature_raises_descriptive():
    # twak hands back garbage bytes (not just a wrong-key signature): the
    # recovery error is wrapped in the provider's descriptive RuntimeError
    # instead of leaking a raw eth_keys exception.
    acct = Account.from_key(_TEST_KEY)
    twak = _primed_twak(acct.address)

    def run(cmd, **kwargs):
        return _completed(cmd, {"success": True, "signature": "0x" + "00" * 65})

    with patch("bnbagent.wallets.twak_provider.subprocess.run", side_effect=run):
        with pytest.raises(RuntimeError, match="malformed signature"):
            twak.sign_message("hello twak")


def test_sign_typed_data_unsupported_no_cli_call():
    # P0: no spec-forward CLI path — the (base-default) method raises without
    # shelling out, pointing x402 consumers at the delegated payer path.
    twak = TWAKProvider()
    with patch("bnbagent.wallets.twak_provider.subprocess.run") as run:
        with pytest.raises(UnsupportedWalletOperation, match="sign.typed_data") as exc:
            twak.sign_typed_data({}, {}, {})
    run.assert_not_called()
    assert "x402 payer path" in str(exc.value)


def test_address_cached_after_first_lookup():
    calls = []

    def run(cmd, **kwargs):
        calls.append(cmd)
        return _completed(cmd, _ADDRESS_OUT)

    twak = TWAKProvider()
    with patch("bnbagent.wallets.twak_provider.subprocess.run", side_effect=run):
        a1 = twak.address
        a2 = twak.address
    assert a1 == a2 == FAKE_ADDRESS
    # the address lookup itself is cached — only one `wallet address` call
    # (a single leading `wallet status` probe from auto-create is allowed)
    assert sum("address" in c for c in calls) == 1


# ── auto-create / create_wallet (EVM-parity) ──

def _status_router(wallet_exists: bool):
    """Route wallet status / create / address with a toggleable existence."""
    state = {"exists": wallet_exists}
    calls: list[list[str]] = []

    def run(cmd, **kwargs):
        calls.append(cmd)
        if "status" in cmd:
            # Field-verified v0.18.0: `wallet status` exits 0 either way; the
            # agentWallet field is the real signal (the exit code is a false
            # positive for a missing wallet).
            agent = "configured" if state["exists"] else "not configured"
            return _completed(cmd, {"agentWallet": agent})
        if "create" in cmd:
            state["exists"] = True  # creation flips existence
            return _completed(cmd, {"success": True})
        if "address" in cmd:
            return _completed(cmd, _ADDRESS_OUT)
        if "set-uri" in cmd:
            return _completed(cmd, _SETURI_OUT)
        raise AssertionError(f"unexpected twak command: {cmd}")

    return run, calls, state


def test_auto_create_default_creates_when_missing():
    run, calls, _ = _status_router(wallet_exists=False)
    twak = TWAKProvider()  # auto-create is the default (EVM-parity)
    with patch("bnbagent.wallets.twak_provider.subprocess.run", side_effect=run):
        addr = twak.address
    assert addr == FAKE_ADDRESS
    assert any("create" in c for c in calls)  # wallet was created
    # password is never placed on the command line
    create_cmd = next(c for c in calls if "create" in c)
    assert "--password" not in create_cmd


def test_auto_create_skips_when_wallet_present():
    run, calls, _ = _status_router(wallet_exists=True)
    twak = TWAKProvider()
    with patch("bnbagent.wallets.twak_provider.subprocess.run", side_effect=run):
        twak.address  # noqa: B018 - property access triggers the lookup
    assert all("create" not in c for c in calls)  # idempotent: no creation


def test_ensure_runs_only_once():
    run, calls, _ = _status_router(wallet_exists=True)
    twak = TWAKProvider()
    with patch("bnbagent.wallets.twak_provider.subprocess.run", side_effect=run):
        twak.address  # noqa: B018 - property access triggers the lookup
        twak.execute(Intent(name=ERC8004_SET_AGENT_URI, kwargs={"agent_id": 1, "agent_uri": "u"}))
    # status probed at most once despite two operations
    assert sum("status" in c for c in calls) == 1


def test_create_wallet_explicit_idempotent():
    run, calls, _ = _status_router(wallet_exists=True)
    twak = TWAKProvider()
    with patch("bnbagent.wallets.twak_provider.subprocess.run", side_effect=run):
        addr = twak.create_wallet()
    assert addr == FAKE_ADDRESS
    assert all("create" not in c for c in calls)  # already exists -> no create


# ── custody: home relocation (design §4, gaps S-5) ──


def test_home_overrides_subprocess_home_inheriting_environ():
    twak = TWAKProvider(home="/srv/agent-7")
    with patch("bnbagent.wallets.twak_provider.subprocess.run") as run:
        run.return_value = _completed([], {"success": True, "accepts": []})
        # x402_quote is a single CLI call with no wallet probe (F-3) — the
        # cleanest window onto the env every invocation gets.
        twak.x402_quote("https://api.example/paid")
    env = run.call_args.kwargs["env"]
    assert env["HOME"] == "/srv/agent-7"  # twak resolves /srv/agent-7/.twak
    # the rest of the environment is inherited (password/credential env vars
    # must keep flowing through to the twak subprocess)
    assert env == {**os.environ, "HOME": "/srv/agent-7"}


def test_home_accepts_path_object():
    twak = TWAKProvider(home=Path("/srv/agent-8"))
    with patch("bnbagent.wallets.twak_provider.subprocess.run") as run:
        run.return_value = _completed([], {"success": True, "accepts": []})
        twak.x402_quote("https://api.example/paid")
    assert run.call_args.kwargs["env"]["HOME"] == "/srv/agent-8"


def test_home_none_inherits_environment_untouched():
    twak = TWAKProvider()  # home=None
    with patch("bnbagent.wallets.twak_provider.subprocess.run") as run:
        run.return_value = _completed([], {"success": True, "accepts": []})
        twak.x402_quote("https://api.example/paid")
    # env=None == "inherit the parent environment" for subprocess.run
    assert run.call_args.kwargs["env"] is None


# ── custody: expected_address identity pin (INV-4) ──


def test_expected_address_match_case_insensitive_and_cached():
    calls: list[list[str]] = []

    def run(cmd, **kwargs):
        calls.append(cmd)
        if "status" in cmd:
            return _completed(cmd, {"agentWallet": "configured"})
        if "address" in cmd:
            return _completed(cmd, _ADDRESS_OUT)
        raise AssertionError(f"unexpected twak command: {cmd}")

    # pin with different casing: the comparison is case-insensitive
    twak = TWAKProvider(expected_address=FAKE_ADDRESS.lower())
    with patch("bnbagent.wallets.twak_provider.subprocess.run", side_effect=run):
        a1 = twak.address
        a2 = twak.address
    assert a1 == a2 == FAKE_ADDRESS
    # the verified address is cached: one CLI lookup across two reads
    assert sum("address" in c for c in calls) == 1


def test_expected_address_mismatch_blocks_operation_and_is_not_cached():
    calls: list[list[str]] = []

    def run(cmd, **kwargs):
        calls.append(cmd)
        if "status" in cmd:
            return _completed(cmd, {"agentWallet": "configured"})
        if "address" in cmd:
            return _completed(cmd, _ADDRESS_OUT)
        raise AssertionError(f"unexpected twak command: {cmd}")

    pinned = "0x" + "99" * 20
    twak = TWAKProvider(expected_address=pinned)
    with patch("bnbagent.wallets.twak_provider.subprocess.run", side_effect=run):
        # the identity check fires from a state-changing entry point BEFORE
        # the operation's own CLI command can run
        with pytest.raises(WalletIdentityMismatch) as exc:
            twak.sign_message("hello")
        assert exc.value.expected == pinned
        assert exc.value.actual == FAKE_ADDRESS
        # a retry re-checks and re-raises: the bad state was never cached
        with pytest.raises(WalletIdentityMismatch):
            twak.sign_message("hello")
    assert all("sign-message" not in c for c in calls)  # never reached the CLI
    assert twak._address is None  # nothing cached under a drifted identity


# ── custody: auto_create=False (deployment mode, INV-4) ──


def test_auto_create_false_missing_wallet_raises_and_never_creates():
    run, calls, _ = _status_router(wallet_exists=False)
    twak = TWAKProvider(auto_create=False)
    with patch("bnbagent.wallets.twak_provider.subprocess.run", side_effect=run):
        with pytest.raises(RuntimeError) as exc:
            twak.address  # noqa: B018 - property access triggers the probe
    message = str(exc.value)
    # the error names the deployment fix: materialize from the secret bundle
    assert "materialize_twak_home" in message
    assert "TWAK_WALLET_JSON" in message
    # `wallet create` was never invoked — only the status probe ran
    assert all("create" not in c for c in calls)


def test_auto_create_false_with_existing_wallet_operates_normally():
    run, calls, _ = _status_router(wallet_exists=True)
    twak = TWAKProvider(auto_create=False)
    with patch("bnbagent.wallets.twak_provider.subprocess.run", side_effect=run):
        assert twak.address == FAKE_ADDRESS
    assert all("create" not in c for c in calls)


# ── x402 raw transport: exact argv + ensure-wallet discipline ──

_X402_FIXTURES = Path(__file__).parent / "fixtures" / "twak_x402"


def _x402_fixture(name):
    return json.loads((_X402_FIXTURES / name).read_text())


def test_x402_quote_argv_minimal_and_no_wallet_probe():
    calls: list[list[str]] = []

    def run(cmd, **kwargs):
        calls.append(cmd)
        return _completed(cmd, _x402_fixture("quote_base_usdc.json"))

    twak = TWAKProvider()
    url = "https://skills.onesource.io/api/chain/chain-id"
    with patch("bnbagent.wallets.twak_provider.subprocess.run", side_effect=run):
        data = twak.x402_quote(url)

    # F-3: quote is read-only — exactly ONE call, no wallet status/create ever
    assert calls == [["twak", "x402", "quote", url, "--json"]]
    assert data["accepts"][0]["maxTimeoutSeconds"] == 3600  # passthrough verbatim


def test_x402_quote_argv_includes_method_and_body_only_when_given():
    calls: list[list[str]] = []

    def run(cmd, **kwargs):
        calls.append(cmd)
        return _completed(cmd, _x402_fixture("quote_bsc_u.json"))

    twak = TWAKProvider()
    with patch("bnbagent.wallets.twak_provider.subprocess.run", side_effect=run):
        twak.x402_quote("https://pay.example/x402", method="POST", body='{"amountUsd":0.1}')

    assert calls == [[
        "twak", "x402", "quote", "https://pay.example/x402",
        "--method", "POST", "--body", '{"amountUsd":0.1}', "--json",
    ]]


def test_x402_quote_https_only_error_surfaces():
    # field-verified VALIDATION_ERROR envelope: success=false on exit 0
    def run(cmd, **kwargs):
        return _completed(cmd, _x402_fixture("error_https_only.json"))

    twak = TWAKProvider()
    with patch("bnbagent.wallets.twak_provider.subprocess.run", side_effect=run):
        with pytest.raises(RuntimeError, match="only https:// URLs are allowed"):
            twak.x402_quote("http://insecure.example/paid")


def _x402_request_router(output):
    """Wallet probe succeeds; the x402 request gets ``output``."""
    calls: list[list[str]] = []

    def run(cmd, **kwargs):
        calls.append(cmd)
        if cmd[1] == "wallet" and cmd[2] == "status":
            return _completed(cmd, {"agentWallet": "configured"})
        return _completed(cmd, output)

    return run, calls


def test_x402_request_argv_minimal_with_wallet_probe_first():
    run, calls = _x402_request_router(_x402_fixture("request_success_pieverse.json"))
    twak = TWAKProvider()
    with patch("bnbagent.wallets.twak_provider.subprocess.run", side_effect=run):
        data = twak.x402_request("https://pay.example/x402", max_payment=1000)

    # a paid request IS state-changing: the wallet probe runs first
    assert calls[0] == _WALLET_STATUS_CMD
    # minimal argv: --max-payment + --yes, and no --prefer-*/--method/--body
    assert calls[1] == [
        "twak", "x402", "request", "https://pay.example/x402",
        "--max-payment", "1000", "--yes", "--json",
    ]
    assert len(calls) == 2
    # success output = the endpoint body verbatim (no receipt, gaps S-7)
    assert data["tx_hash"].startswith("0x09b1af61")


def test_x402_request_argv_includes_prefer_flags_only_when_set():
    run, calls = _x402_request_router(_x402_fixture("request_success_pieverse.json"))
    twak = TWAKProvider()
    u_asset = "0xce24439f2d9c6a2289f741120fe202248b666666"
    with patch("bnbagent.wallets.twak_provider.subprocess.run", side_effect=run):
        twak.x402_request(
            "https://pay.example/x402",
            max_payment=10**17,
            method="POST",
            body='{"amountUsd":0.1}',
            prefer_network="eip155:56",
            prefer_method="eip3009",
            prefer_asset=u_asset,
        )

    assert calls[1] == [
        "twak", "x402", "request", "https://pay.example/x402",
        "--max-payment", str(10**17), "--yes",
        "--method", "POST", "--body", '{"amountUsd":0.1}',
        "--prefer-network", "eip155:56",
        "--prefer-method", "eip3009",
        "--prefer-asset", u_asset,
        "--json",
    ]


def test_x402_request_settlement_rejected_error_surfaces():
    # field-verified NETWORK_ERROR envelope: `error` set, no `success` field
    run, _ = _x402_request_router(_x402_fixture("error_settlement_rejected.json"))
    twak = TWAKProvider()
    with patch("bnbagent.wallets.twak_provider.subprocess.run", side_effect=run):
        with pytest.raises(RuntimeError, match="invalid_payload"):
            twak.x402_request("https://pay.example/x402", max_payment=1000)


def test_x402_request_min_amount_error_surfaces():
    run, _ = _x402_request_router(_x402_fixture("error_min_amount.json"))
    twak = TWAKProvider()
    with patch("bnbagent.wallets.twak_provider.subprocess.run", side_effect=run):
        with pytest.raises(RuntimeError, match="amountUsd must be at least 0.1"):
            twak.x402_request("https://pay.example/x402", max_payment=1000)


# ── live-CLI quirks surfaced by examples/twak (field-verified v0.18.0) ──

def test_exists_false_when_status_reports_not_configured():
    # `wallet status` exits 0 even with no wallet; only the agentWallet field
    # tells the truth. exists() must not be fooled by the zero exit code.
    twak = TWAKProvider()
    with patch("bnbagent.wallets.twak_provider.subprocess.run") as run:
        run.return_value = _completed(
            ["twak"], {"agentWallet": "not configured", "keychainPassword": "not stored"}
        )
        assert twak.exists() is False
        run.return_value = _completed(["twak"], {"agentWallet": "configured"})
        assert twak.exists() is True


def test_auto_create_false_reachable_on_zero_exit_unconfigured_status():
    # Regression: before the exists() fix, a zero-exit "not configured" status
    # made the wallet look present, so the INV-4 deployment-mode error was
    # unreachable and callers got a raw CLI failure later instead.
    twak = TWAKProvider(auto_create=False)
    with patch("bnbagent.wallets.twak_provider.subprocess.run") as run:
        run.return_value = _completed(["twak"], {"agentWallet": "not configured"})
        with pytest.raises(RuntimeError, match="materialize_twak_home"):
            twak.sign_message("hello")


def test_run_trusts_success_envelope_over_nonzero_exit():
    # `x402 quote` exits non-zero on empty accepts while emitting an explicit
    # success envelope (gaps S-9). The envelope wins in the success direction.
    twak = TWAKProvider()
    no_route = _x402_fixture("quote_no_supported_route.json")
    with patch("bnbagent.wallets.twak_provider.subprocess.run") as run:
        run.return_value = _completed(["twak"], no_route, returncode=1)
        data = twak.x402_quote("https://www.x402.org/protected")
    assert data["accepts"] == []
    # an error envelope with a non-zero exit still raises
    with patch("bnbagent.wallets.twak_provider.subprocess.run") as run:
        run.return_value = _completed(
            ["twak"], {"error": "boom", "errorCode": "X"}, returncode=1
        )
        with pytest.raises(RuntimeError, match="boom"):
            twak.x402_quote("https://www.x402.org/protected")


def test_create_wallet_password_requirement_maps_to_descriptive_error():
    # v0.18.0 hard-requires --password on argv for creation; the SDK refuses
    # to pass secrets there (INV-1) and must explain instead (gaps S-8).
    twak = TWAKProvider()

    def run(cmd, **kwargs):
        if "status" in cmd:
            return _completed(cmd, {"agentWallet": "not configured"})
        if "create" in cmd:
            return _completed(
                cmd, {}, returncode=1,
                stderr="error: required option '--password <password>' not specified",
            )
        raise AssertionError(f"unexpected twak command: {cmd}")

    with patch("bnbagent.wallets.twak_provider.subprocess.run", side_effect=run):
        with pytest.raises(RuntimeError, match="S-8"):
            twak.create_wallet()


def test_create_job_string_job_id_normalised_to_int():
    # Field-verified v0.19.0: the CLI emits numeric ids as JSON strings
    # ("150"); the local-executor path yields ints from event logs. The
    # envelope must agree or downstream uint256 web3 calls break (live-found).
    run, _ = _intent_router({"success": True, "hash": "0xfeed", "jobId": "150"})
    twak = TWAKProvider()
    result = _execute(
        twak,
        Intent(
            name=ERC8183_CREATE_JOB,
            kwargs={
                "provider": _PROVIDER_ADDR,
                "evaluator": _EVALUATOR_ADDR,
                "expired_at": 1750000000,
                "description": "live-found normalisation",
                "hook": _ZERO_ADDRESS,
            },
        ),
        run,
    )
    assert result["jobId"] == 150 and isinstance(result["jobId"], int)


def test_register_string_agent_id_normalised_to_int():
    run, _ = _intent_router({"success": True, "hash": "0xreg", "agentId": "1362"})
    twak = TWAKProvider()
    result = _execute(
        twak, Intent(name=ERC8004_REGISTER, kwargs={"agent_uri": "https://a/c.json"}), run
    )
    assert result["agentId"] == 1362 and isinstance(result["agentId"], int)
