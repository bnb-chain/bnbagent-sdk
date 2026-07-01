"""Tests for the wallet provider factory and uniform introspection.

Covers ``create_wallet_provider`` selection plus the ``kind`` / ``key_location``
/ ``exists`` / ``describe`` introspection surface shared by every provider.
"""

from __future__ import annotations

import types
from unittest.mock import patch

import pytest

from bnbagent.wallets import (
    SUPPORTED_WALLET_KINDS,
    EVMWalletProvider,
    TWAKProvider,
    WalletProvider,
    create_wallet_provider,
)

PW = "test-secure-password-123"


def _completed(stdout_json, returncode=0):
    import json

    return types.SimpleNamespace(
        args=[], returncode=returncode, stdout=json.dumps(stdout_json), stderr=""
    )


class TestFactory:
    def test_creates_evm(self, tmp_path):
        wallet = create_wallet_provider("evm", password=PW, wallets_dir=tmp_path)
        assert isinstance(wallet, EVMWalletProvider)
        assert wallet.kind == "evm"

    def test_creates_twak(self):
        wallet = create_wallet_provider("twak", chain="bsc")
        assert isinstance(wallet, TWAKProvider)
        assert wallet.kind == "twak"

    def test_kind_is_case_insensitive(self, tmp_path):
        wallet = create_wallet_provider("EVM", password=PW, wallets_dir=tmp_path)
        assert isinstance(wallet, EVMWalletProvider)

    def test_unknown_kind_raises(self):
        with pytest.raises(ValueError, match="Unknown wallet kind"):
            create_wallet_provider("ledger")

    def test_mpc_is_not_implemented(self):
        # mpc is a recognised kind, but the stub provider refuses to construct.
        with pytest.raises(NotImplementedError):
            create_wallet_provider("mpc")

    def test_supported_kinds_match_class_attrs(self):
        assert set(SUPPORTED_WALLET_KINDS) == {"evm", "twak", "mpc"}


class TestIntrospection:
    def test_base_default_kind(self):
        assert WalletProvider.kind == "custom"

    def test_evm_describe_and_key_location(self, tmp_path):
        wallet = create_wallet_provider("evm", password=PW, wallets_dir=tmp_path)
        info = wallet.describe()
        assert info["kind"] == "evm"
        assert info["address"] == wallet.address
        assert info["address"] in info["key_location"]
        assert info["exists"] is True

    def test_evm_in_memory_does_not_exist(self):
        wallet = EVMWalletProvider(password=PW, private_key="0x" + "a" * 64, persist=False)
        assert wallet.exists() is False
        assert "in-memory" in wallet.key_location

    def test_twak_introspection_is_offline_safe(self):
        wallet = create_wallet_provider("twak", chain="bsc")
        # key_location is static (no subprocess needed)
        assert "twak" in wallet.key_location.lower()
        # exists() probes the CLI; a missing binary must yield False, not raise.
        with patch("subprocess.run", side_effect=FileNotFoundError()):
            assert wallet.exists() is False

    def test_twak_exists_true_when_status_ok(self):
        # Field-verified v0.18.0: `wallet status` exits 0 either way — only
        # the agentWallet field signals a configured wallet.
        wallet = create_wallet_provider("twak", chain="bsc")
        with patch("subprocess.run", return_value=_completed({"agentWallet": "configured"})):
            assert wallet.exists() is True
        with patch("subprocess.run", return_value=_completed({"agentWallet": "not configured"})):
            assert wallet.exists() is False
