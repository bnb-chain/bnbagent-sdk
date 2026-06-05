"""Tests for TWAKProvider and its integration as a self-broadcasting executor.

The twak CLI is mocked at the ``subprocess.run`` boundary so the full intent
flow (ContractInterface -> TWAKProvider -> twak CLI -> result mapping) is
exercised without a live binary or on-chain calls.
"""

from __future__ import annotations

import json
import types
from unittest.mock import MagicMock, patch

import pytest

from bnbagent.erc8004.contract import ContractInterface
from bnbagent.wallets import IntentExecutor, TWAKProvider
from bnbagent.wallets.intents import (
    ERC8004_REGISTER,
    ERC8004_SET_AGENT_URI,
    ERC8004_SET_METADATA,
    Intent,
)
from tests.conftest import FAKE_ADDRESS, FAKE_CONTRACT_ADDRESS

# Canonical twak --json outputs (per the ERC-8004/8183 twak spec).
_REGISTER_OUT = {"success": True, "agentId": 42, "txHash": "0xreg", "owner": FAKE_ADDRESS, "chain": "bsc"}
_SETMETA_OUT = {"success": True, "txHash": "0xmeta", "chain": "bsc"}
_SETURI_OUT = {"success": True, "txHash": "0xuri", "chain": "bsc"}
_ADDRESS_OUT = {"success": True, "address": FAKE_ADDRESS, "chain": "bsc"}


def _completed(cmd, stdout, returncode=0, stderr=""):
    return types.SimpleNamespace(
        args=cmd, returncode=returncode, stdout=json.dumps(stdout), stderr=stderr
    )


def _router(fail_keys=None):
    """Return (run_fn, calls) routing twak subcommands to canned JSON.

    fail_keys: set of metadata keys for which set-metadata should exit 1.
    """
    fail_keys = fail_keys or set()
    calls: list[list[str]] = []

    def run(cmd, **kwargs):
        calls.append(cmd)
        if "status" in cmd:  # lazy auto-create probe: model a configured wallet
            return _completed(cmd, {"success": True})
        if "register" in cmd:
            return _completed(cmd, _REGISTER_OUT)
        if "set-metadata" in cmd:
            key = cmd[cmd.index("--key") + 1]
            if key in fail_keys:
                return _completed(cmd, {"success": False, "error": "boom"}, returncode=1)
            return _completed(cmd, _SETMETA_OUT)
        if "set-uri" in cmd:
            return _completed(cmd, _SETURI_OUT)
        if "address" in cmd:
            return _completed(cmd, _ADDRESS_OUT)
        raise AssertionError(f"unexpected twak command: {cmd}")

    return run, calls


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


def test_non_bsc_chain_rejected():
    with pytest.raises(ValueError, match="BNB Smart Chain only"):
        TWAKProvider(chain="ethereum")


def test_bsc_testnet_accepted_and_passed_through():
    twak = TWAKProvider(chain="bsc-testnet")
    assert twak._chain == "bsc-testnet"
    with patch("bnbagent.wallets.twak_provider.subprocess.run") as run:
        run.return_value = _completed(["twak"], _ADDRESS_OUT)
        twak.address
    # the chain flag forwarded to twak is the one we constructed with
    assert "bsc-testnet" in run.call_args[0][0]


def test_contract_selects_twak_as_executor():
    twak = TWAKProvider()
    ci, _ = _make_twak_contract(twak)
    assert ci._executor is twak


# ── register intent with metadata replay ──

def test_register_replays_metadata_and_maps_fields():
    run, calls = _router()
    twak = TWAKProvider()
    with patch("bnbagent.wallets.twak_provider.subprocess.run", side_effect=run):
        result = twak.execute(
            Intent(
                name=ERC8004_REGISTER,
                kwargs={
                    "agent_uri": "https://agent.example/card.json",
                    "metadata": [
                        {"key": "built_with", "value": "https://github.com/bnb-chain/bnbagent-sdk#v1"},
                        {"key": "foo", "value": "bar"},
                    ],
                },
            )
        )

    assert result["success"] is True
    assert result["agentId"] == 42
    assert result["transactionHash"] == "0xreg"  # mapped from twak's txHash
    assert result["receipt"] is None
    assert result["metadataTxs"] == ["0xmeta", "0xmeta"]

    # register, then one set-metadata per entry
    reg = next(c for c in calls if "register" in c)
    assert reg[reg.index("--uri") + 1] == "https://agent.example/card.json"
    assert sum("set-metadata" in c for c in calls) == 2
    assert all(c[-1] == "--json" for c in calls)  # --json always appended


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
    # built_with is auto-injected, so foo + built_with => 2 set-metadata calls
    assert sum("set-metadata" in c for c in calls) == 2
    # The local web3 broadcast path must never be touched.
    web3.eth.send_raw_transaction.assert_not_called()


def test_register_partial_metadata_failure_is_best_effort(caplog):
    run, calls = _router(fail_keys={"foo"})
    twak = TWAKProvider()
    with patch("bnbagent.wallets.twak_provider.subprocess.run", side_effect=run):
        with caplog.at_level("WARNING"):
            result = twak.execute(
                Intent(
                    name=ERC8004_REGISTER,
                    kwargs={
                        "agent_uri": "https://agent.example/card.json",
                        "metadata": [
                            {"key": "built_with", "value": "v1"},
                            {"key": "foo", "value": "bar"},
                        ],
                    },
                )
            )

    # Registration still succeeds; the failed entry is dropped with a warning.
    assert result["agentId"] == 42
    assert result["metadataTxs"] == ["0xmeta"]
    assert "set-metadata for key='foo'" in caplog.text


# ── other intents ──

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


def test_unsupported_intent_raises():
    twak = TWAKProvider()
    with pytest.raises(NotImplementedError, match="erc8183.fund"):
        twak.execute(Intent(name="erc8183.fund", kwargs={}))


# ── error + signing surface ──

def test_nonzero_exit_raises_runtime_error():
    def run(cmd, **kwargs):
        return _completed(cmd, {"success": False, "error": "ExpiryTooShort()"}, returncode=1)

    twak = TWAKProvider()
    with patch("bnbagent.wallets.twak_provider.subprocess.run", side_effect=run):
        with pytest.raises(RuntimeError, match="ExpiryTooShort"):
            twak.execute(Intent(name=ERC8004_SET_METADATA, kwargs={"agent_id": 1, "key": "k", "value": "v"}))


def test_missing_binary_raises_helpful_error():
    twak = TWAKProvider()
    with patch("bnbagent.wallets.twak_provider.subprocess.run", side_effect=FileNotFoundError()):
        with pytest.raises(RuntimeError, match="twak CLI not found"):
            twak.address  # noqa: B018 - property access triggers the lookup


def test_sign_transaction_not_supported():
    twak = TWAKProvider()
    with pytest.raises(NotImplementedError, match="no raw-tx signing primitive"):
        twak.sign_transaction({})


def test_sign_message_parses_signature_components():
    sig = "0x" + "11" * 32 + "22" * 32 + "1b"  # r, s, v=27
    out = {"success": True, "signature": sig, "digest": "0xabc"}

    def run(cmd, **kwargs):
        if "status" in cmd:  # auto-create probe: wallet already configured
            return _completed(cmd, {"success": True})
        assert "sign-message" in cmd
        return _completed(cmd, out)

    twak = TWAKProvider()
    with patch("bnbagent.wallets.twak_provider.subprocess.run", side_effect=run):
        result = twak.sign_message("hello")
    assert result["signature"] == sig
    assert result["v"] == 27
    assert result["r"] == int("11" * 32, 16)
    assert result["messageHash"] == "0xabc"


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
            ok = state["exists"]
            return _completed(cmd, {"success": ok}, returncode=0 if ok else 1)
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
        twak.address
    assert all("create" not in c for c in calls)  # idempotent: no creation


def test_ensure_runs_only_once():
    run, calls, _ = _status_router(wallet_exists=True)
    twak = TWAKProvider()
    with patch("bnbagent.wallets.twak_provider.subprocess.run", side_effect=run):
        twak.address
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
