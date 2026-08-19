"""Tests for bnbagent.networks address registry."""

from __future__ import annotations

import pytest
from web3 import Web3

from bnbagent.config import NETWORKS
from bnbagent.networks import (
    BNB_CHAIN_ADDRESSES,
    BSC_MAINNET_CHAIN_ID,
    BSC_TESTNET_CHAIN_ID,
    PAYMENT_TOKEN_EIP712_NAME,
    PAYMENT_TOKEN_EIP712_VERSION,
    get_address,
    known_payment_tokens,
)


def test_get_address_returns_mainnet_payment_token():
    d = get_address(BSC_MAINNET_CHAIN_ID)
    assert d.payment_token == "0xcE24439F2D9C6a2289F741120FE202248B666666"


def test_get_address_returns_testnet_payment_token():
    d = get_address(BSC_TESTNET_CHAIN_ID)
    assert d.payment_token == "0xc70B8741B8B07A6d61E54fd4B20f22Fa648E5565"


def test_address_registry_matches_current_apex_deployments():
    testnet = get_address(BSC_TESTNET_CHAIN_ID)
    assert testnet.commerce_impl.lower() == "0x153783ddbdf5233c591965f04644b1df2d1a7815"
    assert testnet.router_impl.lower() == "0x40c0254610d92f1eb9c2d7d5d2114bc4c99d935e"
    assert testnet.policy.lower() == "0xd6a4217588f6b1f5657a92a3e94e6422ad771cea"
    assert NETWORKS["bsc-testnet"].policy_contract.lower() == testnet.policy.lower()

    mainnet = get_address(BSC_MAINNET_CHAIN_ID)
    assert mainnet.commerce_impl.lower() == "0xd5f9b570c96b5d67702d508c0bfb8b3b09209787"


def test_get_address_unknown_chain_raises_keyerror():
    with pytest.raises(KeyError, match="chain_id=1"):
        get_address(1)


def test_all_addresses_are_checksummed():
    """Every address on every chain must be EIP-55 checksum-encoded."""
    for chain_id, deploy in BNB_CHAIN_ADDRESSES.items():
        for field_name in (
            "payment_token",
            "treasury",
            "commerce_proxy",
            "commerce_impl",
            "router_proxy",
            "router_impl",
            "policy",
        ):
            addr = getattr(deploy, field_name)
            assert Web3.is_checksum_address(addr), (
                f"chain {chain_id} field {field_name}={addr} is not checksummed"
            )


def test_known_payment_tokens_contains_both_networks():
    pairs = known_payment_tokens()
    assert (BSC_MAINNET_CHAIN_ID, "0xcE24439F2D9C6a2289F741120FE202248B666666") in pairs
    assert (BSC_TESTNET_CHAIN_ID, "0xc70B8741B8B07A6d61E54fd4B20f22Fa648E5565") in pairs
    assert len(pairs) == 2  # update when adding more networks


def test_known_payment_tokens_is_frozenset_for_policy_use():
    """Must be hashable + immutable so SigningPolicy can use it directly."""
    pairs = known_payment_tokens()
    assert isinstance(pairs, frozenset)
    # frozensets are hashable
    hash(pairs)


def test_deployed_addresses_is_frozen():
    d = get_address(BSC_MAINNET_CHAIN_ID)
    with pytest.raises((AttributeError, Exception)):
        d.payment_token = "0xdeadbeef"  # type: ignore[misc]


def test_eip712_domain_constants_match_phase0_verification():
    """name+version constants must match what's encoded in U token's
    DOMAIN_SEPARATOR on-chain. Phase 0 verification recovered these by
    brute-forcing keccak(name)|keccak(version)|chainId|verifyingContract
    against the live DOMAIN_SEPARATOR() return value."""
    assert PAYMENT_TOKEN_EIP712_NAME == "United Stables"
    assert PAYMENT_TOKEN_EIP712_VERSION == "1"
