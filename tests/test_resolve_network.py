"""Tests for resolve_network RPC override precedence.

Precedence: RPC_URL_<NETWORK> (per-network) > RPC_URL (global) > preset.
A NetworkConfig object passed directly is returned as-is (no env applied).
"""

from __future__ import annotations

import pytest

from bnbagent.config import NETWORKS, NetworkConfig, resolve_network


@pytest.fixture(autouse=True)
def _clean_rpc_env(monkeypatch):
    for key in ("RPC_URL", "RPC_URL_BSC_TESTNET", "RPC_URL_BSC_MAINNET"):
        monkeypatch.delenv(key, raising=False)


class TestResolveNetworkPrecedence:
    def test_preset_default_when_no_env(self):
        nc = resolve_network("bsc-testnet")
        assert nc.rpc_url == NETWORKS["bsc-testnet"].rpc_url

    def test_global_rpc_url_overrides_preset(self, monkeypatch):
        monkeypatch.setenv("RPC_URL", "https://global.example.com")
        nc = resolve_network("bsc-testnet")
        assert nc.rpc_url == "https://global.example.com"

    def test_per_network_overrides_global(self, monkeypatch):
        monkeypatch.setenv("RPC_URL", "https://global.example.com")
        monkeypatch.setenv("RPC_URL_BSC_TESTNET", "https://testnet.example.com")
        nc = resolve_network("bsc-testnet")
        assert nc.rpc_url == "https://testnet.example.com"

    def test_per_network_is_network_scoped(self, monkeypatch):
        """A testnet pin must not leak onto mainnet resolution."""
        monkeypatch.setenv("RPC_URL_BSC_TESTNET", "https://testnet.example.com")
        testnet = resolve_network("bsc-testnet")
        mainnet = resolve_network("bsc-mainnet")
        assert testnet.rpc_url == "https://testnet.example.com"
        assert mainnet.rpc_url == NETWORKS["bsc-mainnet"].rpc_url

    def test_both_networks_pinned_simultaneously(self, monkeypatch):
        """One process can resolve BOTH networks to distinct pinned nodes."""
        monkeypatch.setenv("RPC_URL_BSC_TESTNET", "https://t.example.com")
        monkeypatch.setenv("RPC_URL_BSC_MAINNET", "https://m.example.com")
        assert resolve_network("bsc-testnet").rpc_url == "https://t.example.com"
        assert resolve_network("bsc-mainnet").rpc_url == "https://m.example.com"

    def test_localhost_override_disables_paymaster(self, monkeypatch):
        monkeypatch.setenv("RPC_URL_BSC_TESTNET", "http://localhost:8545")
        nc = resolve_network("bsc-testnet")
        assert nc.use_paymaster is False

    def test_chain_metadata_preserved_under_override(self, monkeypatch):
        monkeypatch.setenv("RPC_URL_BSC_TESTNET", "https://testnet.example.com")
        nc = resolve_network("bsc-testnet")
        preset = NETWORKS["bsc-testnet"]
        assert nc.chain_id == preset.chain_id
        assert nc.commerce_contract == preset.commerce_contract
        assert nc.registry_contract == preset.registry_contract

    def test_network_config_object_ignores_env(self, monkeypatch):
        monkeypatch.setenv("RPC_URL", "https://global.example.com")
        monkeypatch.setenv("RPC_URL_BSC_TESTNET", "https://testnet.example.com")
        explicit = NetworkConfig(name="bsc-testnet", chain_id=97, rpc_url="https://mine.example.com")
        nc = resolve_network(explicit)
        assert nc is explicit
        assert nc.rpc_url == "https://mine.example.com"

    def test_unknown_network_raises(self):
        with pytest.raises(ValueError, match="Unknown network"):
            resolve_network("opbnb")
