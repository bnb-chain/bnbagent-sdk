"""Tests that EVMWalletProvider.sign_typed_data enforces SigningPolicy.

Day 10 / Phase 2: complement test_signing_policy.py (which tests the policy
data structure) by verifying the wallet-side wiring — default fail-closed,
permissive opt-out, and the _DANGEROUS_*_no_policy escape hatch.
"""

from __future__ import annotations

import pytest

from bnbagent import EVMWalletProvider
from bnbagent.networks import BSC_MAINNET_CHAIN_ID, get_address
from bnbagent.signing import PolicyViolation, SigningPolicy

PW = "test-secure-password-123"
PK = "0x" + "a" * 64

U_MAINNET = get_address(BSC_MAINNET_CHAIN_ID).payment_token

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


@pytest.fixture
def wdir(tmp_path):
    return tmp_path / "wallets"


def _u_token_twa_payload(wallet, valid_after=0, valid_before=None):
    """Build a U-token TransferWithAuthorization sign request within the
    strict default validity bounds. Uses 'now + 60' as the upper bound by
    default so the future-validity cap (900s) is not the failing factor.
    """
    import time
    if valid_before is None:
        valid_after = int(time.time()) - 60
        valid_before = int(time.time()) + 60
    domain = {
        "name": "United Stables",
        "version": "1",
        "chainId": BSC_MAINNET_CHAIN_ID,
        "verifyingContract": U_MAINNET,
    }
    types = {"EIP712Domain": EIP712DOMAIN_FIELDS, "TransferWithAuthorization": TWA_FIELDS}
    message = {
        "from": wallet.address,
        "to": "0x" + "b" * 40,
        "value": 1_000_000,
        "validAfter": valid_after,
        "validBefore": valid_before,
        "nonce": "0x" + "c" * 64,
    }
    return domain, types, message


# ── Default wallet (strict_default) ───────────────────────────────────────


def test_default_wallet_accepts_u_token_transfer_with_authorization(wdir):
    """The single most important happy-path: default config + U-token TWA works."""
    wallet = EVMWalletProvider(password=PW, private_key=PK, wallets_dir=wdir)
    domain, types, msg = _u_token_twa_payload(wallet)
    signed = wallet.sign_typed_data(domain, types, msg)
    assert "signature" in signed
    assert "messageHash" in signed


def test_default_wallet_rejects_unknown_verifying_contract(wdir):
    """Strict default refuses arbitrary verifyingContract."""
    wallet = EVMWalletProvider(password=PW, private_key=PK, wallets_dir=wdir)
    domain, types, msg = _u_token_twa_payload(wallet)
    domain["verifyingContract"] = "0x" + "1" * 40
    with pytest.raises(PolicyViolation, match="not in allowlist") as exc:
        wallet.sign_typed_data(domain, types, msg)
    assert exc.value.primary_type == "TransferWithAuthorization"
    assert exc.value.chain_id == BSC_MAINNET_CHAIN_ID


def test_default_wallet_rejects_eip2612_permit(wdir):
    """U-token supports EIP-2612 on-chain; denylist must block any signer."""
    wallet = EVMWalletProvider(password=PW, private_key=PK, wallets_dir=wdir)
    domain = {
        "name": "United Stables", "version": "1",
        "chainId": BSC_MAINNET_CHAIN_ID, "verifyingContract": U_MAINNET,
    }
    types = {
        "EIP712Domain": EIP712DOMAIN_FIELDS,
        "Permit": [
            {"name": "owner", "type": "address"},
            {"name": "spender", "type": "address"},
            {"name": "value", "type": "uint256"},
            {"name": "nonce", "type": "uint256"},
            {"name": "deadline", "type": "uint256"},
        ],
    }
    msg = {
        "owner": wallet.address, "spender": "0x" + "b" * 40,
        "value": 2**256 - 1, "nonce": 0, "deadline": 2_000_000_000,
    }
    with pytest.raises(PolicyViolation, match="denylisted"):
        wallet.sign_typed_data(domain, types, msg)


def test_default_wallet_rejects_excessive_validity_window(wdir):
    wallet = EVMWalletProvider(password=PW, private_key=PK, wallets_dir=wdir)
    import time
    now = int(time.time())
    domain, types, msg = _u_token_twa_payload(
        wallet, valid_after=now, valid_before=now + 1200,
    )
    with pytest.raises(PolicyViolation, match="window 1200s exceeds"):
        wallet.sign_typed_data(domain, types, msg)


# ── Permissive opt-out ────────────────────────────────────────────────────


def test_permissive_wallet_accepts_unknown_verifying_contract(wdir):
    """SigningPolicy.permissive() opts out of all gating (tests only)."""
    wallet = EVMWalletProvider(
        password=PW, private_key=PK, wallets_dir=wdir,
        signing_policy=SigningPolicy.permissive(),
    )
    domain = {
        "name": "Whatever", "version": "1",
        "chainId": 999, "verifyingContract": "0x" + "f" * 40,
    }
    types = {
        "EIP712Domain": EIP712DOMAIN_FIELDS,
        "ExoticType": [{"name": "x", "type": "uint256"}],
    }
    signed = wallet.sign_typed_data(domain, types, {"x": 1})
    assert "signature" in signed


# ── _DANGEROUS escape hatch ──────────────────────────────────────────────


def test_dangerous_bypass_signs_anything_with_warn_log(wdir, caplog):
    """Verify the escape hatch (a) signs even un-policied payloads and
    (b) emits a WARN with the caller location, so audit grep can find it."""
    import logging
    wallet = EVMWalletProvider(password=PW, private_key=PK, wallets_dir=wdir)
    domain = {
        "name": "Whatever", "version": "1",
        "chainId": 999, "verifyingContract": "0x" + "f" * 40,
    }
    types = {"EIP712Domain": EIP712DOMAIN_FIELDS, "Anything": [{"name": "x", "type": "uint256"}]}
    with caplog.at_level(logging.WARNING, logger="bnbagent.wallets.evm_wallet_provider"):
        signed = wallet._DANGEROUS_sign_typed_data_no_policy(domain, types, {"x": 1})
    assert "signature" in signed
    # WARN must be emitted with the marker and a caller location
    matches = [r for r in caplog.records if "_DANGEROUS_sign_typed_data_no_policy" in r.message]
    assert matches, "expected WARN log from _DANGEROUS_*_no_policy"
    assert "POLICY BYPASS" in matches[0].message
    # caller annotation should include this test file path
    assert "test_wallet_policy_gating.py" in matches[0].message


# ── signing_policy property + extend integration ────────────────────────


def test_extended_policy_accepts_caller_domain(wdir):
    """Extending the policy with a new (chain_id, contract) pair makes it
    signable. Demonstrates the studio.toml extension path."""
    custom_contract = "0x" + "2" * 40
    from web3 import Web3
    custom_cs = Web3.to_checksum_address(custom_contract)
    extended = SigningPolicy.strict_default().extend(
        domain_allowlist={(BSC_MAINNET_CHAIN_ID, custom_cs)},
    )
    wallet = EVMWalletProvider(
        password=PW, private_key=PK, wallets_dir=wdir,
        signing_policy=extended,
    )
    domain, types, msg = _u_token_twa_payload(wallet)
    domain["verifyingContract"] = custom_cs
    signed = wallet.sign_typed_data(domain, types, msg)
    assert "signature" in signed
    # And the wallet exposes its policy
    assert (BSC_MAINNET_CHAIN_ID, custom_cs) in wallet.signing_policy.domain_allowlist
