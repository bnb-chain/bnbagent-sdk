"""Tests for bnbagent.signing — SigningPolicy + check()."""

from __future__ import annotations

import pytest

from bnbagent.networks import (
    BSC_MAINNET_CHAIN_ID,
    BSC_TESTNET_CHAIN_ID,
    get_address,
)
from bnbagent.signing import (
    EIP3009_TYPES,
    PERMIT_UNBOUNDED_TYPES,
    PolicyViolation,
    SigningPolicy,
    check,
    infer_primary_type,
)

# ── Fixtures ───────────────────────────────────────────────────────────────

U_MAINNET = get_address(BSC_MAINNET_CHAIN_ID).payment_token
U_TESTNET = get_address(BSC_TESTNET_CHAIN_ID).payment_token

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
PERMIT_FIELDS = [
    {"name": "owner", "type": "address"},
    {"name": "spender", "type": "address"},
    {"name": "value", "type": "uint256"},
    {"name": "nonce", "type": "uint256"},
    {"name": "deadline", "type": "uint256"},
]

NOW = 1_700_000_000  # frozen time for deterministic validity checks


def _twa_msg(*, valid_after=None, valid_before=None):
    return {
        "from": "0x" + "a" * 40,
        "to": "0x" + "b" * 40,
        "value": 1_000_000,
        "validAfter": NOW - 60 if valid_after is None else valid_after,
        "validBefore": NOW + 300 if valid_before is None else valid_before,
        "nonce": "0x" + "c" * 64,
    }


def _twa_call(
    policy,
    *,
    domain_overrides=None,
    message_overrides=None,
    twa_fields=None,
    now=NOW,
):
    domain = {
        "name": "United Stables",
        "version": "1",
        "chainId": BSC_MAINNET_CHAIN_ID,
        "verifyingContract": U_MAINNET,
    }
    if domain_overrides:
        domain.update(domain_overrides)
    types = {
        "EIP712Domain": EIP712DOMAIN_FIELDS,
        "TransferWithAuthorization": TWA_FIELDS if twa_fields is None else twa_fields,
    }
    msg = _twa_msg()
    if message_overrides:
        msg.update(message_overrides)
    return check(policy, domain, types, msg, now=now)


# ── strict_default behavior ───────────────────────────────────────────────


def test_strict_default_allows_u_mainnet_transfer_with_authorization():
    p = SigningPolicy.strict_default()
    pt = _twa_call(p)
    assert pt == "TransferWithAuthorization"


def test_strict_default_allows_u_testnet_transfer_with_authorization():
    p = SigningPolicy.strict_default()
    pt = _twa_call(
        p,
        domain_overrides={"chainId": BSC_TESTNET_CHAIN_ID, "verifyingContract": U_TESTNET},
    )
    assert pt == "TransferWithAuthorization"


def test_strict_default_rejects_unknown_verifying_contract():
    p = SigningPolicy.strict_default()
    with pytest.raises(PolicyViolation, match="not in allowlist") as exc:
        _twa_call(p, domain_overrides={"verifyingContract": "0x" + "1" * 40})
    assert exc.value.primary_type == "TransferWithAuthorization"
    assert exc.value.chain_id == BSC_MAINNET_CHAIN_ID


def test_strict_default_rejects_unknown_chain_id():
    p = SigningPolicy.strict_default()
    # chain_id 1 (Ethereum mainnet) — U mainnet address but wrong chain
    with pytest.raises(PolicyViolation, match="not in allowlist"):
        _twa_call(p, domain_overrides={"chainId": 1})


def test_strict_default_rejects_eip2612_permit():
    """U-token supports EIP-2612 Permit on-chain; denylist must block it."""
    p = SigningPolicy.strict_default()
    domain = {
        "name": "United Stables",
        "version": "1",
        "chainId": BSC_MAINNET_CHAIN_ID,
        "verifyingContract": U_MAINNET,
    }
    types = {"EIP712Domain": EIP712DOMAIN_FIELDS, "Permit": PERMIT_FIELDS}
    msg = {
        "owner": "0x" + "a" * 40,
        "spender": "0x" + "b" * 40,
        "value": 2**256 - 1,
        "nonce": 0,
        "deadline": 2_000_000_000,
    }
    with pytest.raises(PolicyViolation, match="denylisted") as exc:
        check(p, domain, types, msg, now=NOW)
    assert exc.value.primary_type == "Permit"


def test_strict_default_rejects_permit2_permit_single():
    p = SigningPolicy.strict_default()
    domain = {
        "name": "Permit2",
        "version": "1",
        "chainId": BSC_MAINNET_CHAIN_ID,
        "verifyingContract": "0x" + "2" * 40,
    }
    types = {"EIP712Domain": EIP712DOMAIN_FIELDS, "PermitSingle": PERMIT_FIELDS}
    with pytest.raises(PolicyViolation, match="denylisted"):
        check(p, domain, types, {}, now=NOW)


def test_denylist_takes_precedence_over_allowlist():
    """Even if a misconfigured policy puts Permit in allowlist, denylist wins."""
    p = SigningPolicy.strict_default().extend(primary_type_allowlist={"Permit"})
    assert "Permit" in p.primary_type_allowlist
    assert "Permit" in p.primary_type_denylist
    domain = {
        "name": "United Stables",
        "version": "1",
        "chainId": BSC_MAINNET_CHAIN_ID,
        "verifyingContract": U_MAINNET,
    }
    types = {"EIP712Domain": EIP712DOMAIN_FIELDS, "Permit": PERMIT_FIELDS}
    with pytest.raises(PolicyViolation, match="denylisted"):
        check(p, domain, types, {}, now=NOW)


# ── Validity window ──────────────────────────────────────────────────────


def test_rejects_validity_window_too_long():
    p = SigningPolicy.strict_default()
    with pytest.raises(PolicyViolation, match="window 1200s exceeds max 600s"):
        _twa_call(p, message_overrides={"validAfter": NOW - 600, "validBefore": NOW + 600})


def test_rejects_validBefore_too_far_in_future():
    p = SigningPolicy.strict_default()
    # window itself fine (300s) but validBefore is 1500s in future
    with pytest.raises(PolicyViolation, match="exceeds max 900s"):
        _twa_call(p, message_overrides={"validAfter": NOW + 1200, "validBefore": NOW + 1500})


def test_rejects_validBefore_le_validAfter():
    p = SigningPolicy.strict_default()
    with pytest.raises(PolicyViolation, match="must be >"):
        _twa_call(p, message_overrides={"validAfter": NOW + 100, "validBefore": NOW + 100})


def test_rejects_already_expired_validBefore():
    """validBefore in the past must be refused — otherwise budget is reserved
    for an authorization the chain will reject (or a non-standard token may
    settle), and the temporal guard is void."""
    p = SigningPolicy.strict_default()
    with pytest.raises(PolicyViolation, match="already expired"):
        _twa_call(p, message_overrides={"validAfter": NOW - 600, "validBefore": NOW - 300})


def test_rejects_missing_validity_fields_when_required():
    """TransferWithAuthorization without validBefore/validAfter must fail."""
    p = SigningPolicy.strict_default()
    domain = {
        "name": "United Stables",
        "version": "1",
        "chainId": BSC_MAINNET_CHAIN_ID,
        "verifyingContract": U_MAINNET,
    }
    types = {"EIP712Domain": EIP712DOMAIN_FIELDS, "TransferWithAuthorization": TWA_FIELDS}
    msg = {"from": "0x" + "a" * 40, "to": "0x" + "b" * 40, "value": 1}
    with pytest.raises(PolicyViolation, match="requires validBefore"):
        check(p, domain, types, msg, now=NOW)


# ── EIP-3009 field-shape pinning (SRC-1314) ──────────────────────────────
# The allowlist is name-scoped and `types` is caller-supplied (for x402 it
# comes straight out of an untrusted 402 response body). Keeping the
# allowlisted name while rewriting the field shape changes what the encoder
# accepts, which is how a value-domain guard got silently removed.


def _twa_fields_with(name, key, new_value):
    return [{**f, key: new_value} if f["name"] == name else f for f in TWA_FIELDS]


def test_rejects_value_declared_as_int256():
    """The exploit primitive: int256 makes a negative `value` encodable.

    Note the message here carries a perfectly ordinary positive value — this
    is rejected on field shape alone, so the guard stands on its own rather
    than depending on the amount checks downstream.
    """
    p = SigningPolicy.strict_default()
    with pytest.raises(PolicyViolation, match="canonical EIP-3009 field shape"):
        _twa_call(p, twa_fields=_twa_fields_with("value", "type", "int256"))


def test_rejects_narrowed_value_type():
    p = SigningPolicy.strict_default()
    with pytest.raises(PolicyViolation, match="canonical EIP-3009 field shape"):
        _twa_call(p, twa_fields=_twa_fields_with("value", "type", "uint128"))


def test_rejects_reordered_fields():
    """Order feeds the typeHash, so a reorder is a different struct."""
    p = SigningPolicy.strict_default()
    swapped = [TWA_FIELDS[1], TWA_FIELDS[0], *TWA_FIELDS[2:]]
    with pytest.raises(PolicyViolation, match="canonical EIP-3009 field shape"):
        _twa_call(p, twa_fields=swapped)


def test_rejects_extra_field():
    p = SigningPolicy.strict_default()
    with pytest.raises(PolicyViolation, match="canonical EIP-3009 field shape"):
        _twa_call(p, twa_fields=[*TWA_FIELDS, {"name": "memo", "type": "bytes32"}])


def test_rejects_missing_field():
    p = SigningPolicy.strict_default()
    with pytest.raises(PolicyViolation, match="canonical EIP-3009 field shape"):
        _twa_call(p, twa_fields=TWA_FIELDS[:-1])


def test_rejects_renamed_field():
    p = SigningPolicy.strict_default()
    with pytest.raises(PolicyViolation, match="canonical EIP-3009 field shape"):
        _twa_call(p, twa_fields=_twa_fields_with("value", "name", "amount"))


def test_rejects_non_list_field_descriptor():
    p = SigningPolicy.strict_default()
    with pytest.raises(PolicyViolation, match="must be a list of field descriptors"):
        _twa_call(p, twa_fields={"from": "address"})


def test_field_shape_violation_carries_diagnostics():
    p = SigningPolicy.strict_default()
    with pytest.raises(PolicyViolation) as exc:
        _twa_call(p, twa_fields=_twa_fields_with("value", "type", "int256"))
    assert exc.value.primary_type == "TransferWithAuthorization"
    assert exc.value.chain_id == BSC_MAINNET_CHAIN_ID
    assert exc.value.verifying_contract == U_MAINNET


def test_canonical_shape_still_accepted():
    """Guard against the pin being too tight to sign the real thing."""
    p = SigningPolicy.strict_default()
    assert _twa_call(p) == "TransferWithAuthorization"


def test_receive_with_authorization_shares_the_canonical_shape():
    p = SigningPolicy.strict_default()
    domain = {
        "name": "United Stables",
        "version": "1",
        "chainId": BSC_MAINNET_CHAIN_ID,
        "verifyingContract": U_MAINNET,
    }
    types = {
        "EIP712Domain": EIP712DOMAIN_FIELDS,
        "ReceiveWithAuthorization": TWA_FIELDS,
    }
    assert check(p, domain, types, _twa_msg(), now=NOW) == "ReceiveWithAuthorization"


def test_unknown_primary_type_is_not_shape_pinned():
    """Shape pinning is keyed on the struct name, so a differently-named custom
    type is untouched — permissive()/extend() callers keep working.
    """
    p = SigningPolicy.permissive(allow_in_production=True)
    domain = {
        "name": "Custom",
        "version": "1",
        "chainId": BSC_MAINNET_CHAIN_ID,
        "verifyingContract": U_MAINNET,
    }
    types = {
        "EIP712Domain": EIP712DOMAIN_FIELDS,
        "MyCustomStruct": [{"name": "whatever", "type": "int256"}],
    }
    assert check(p, domain, types, {"whatever": -5}, now=NOW) == "MyCustomStruct"


def test_shape_pin_applies_even_under_permissive():
    """The pin is keyed on the struct NAME, not on the policy.

    permissive() opts out of the allowlist, not out of the canonical shape: a
    struct calling itself TransferWithAuthorization is held to the EIP-3009
    shape regardless of ruleset, because the shape belongs to the name. This
    test exists to pin that decision down — it is the one behavioural
    consequence of the SRC-1314 fix that is not obvious from the allowlist.
    """
    p = SigningPolicy.permissive(allow_in_production=True)
    with pytest.raises(PolicyViolation, match="canonical EIP-3009 field shape"):
        _twa_call(p, twa_fields=_twa_fields_with("value", "type", "int256"))


def test_eip712domain_shape_is_not_pinned():
    """EIP712Domain is deliberately left unpinned — EIP-712 makes every domain
    field optional, so there is no single canonical shape to pin against.

    Documented as checked-and-cleared rather than overlooked: altering the
    domain shape changes the domainSeparator, so the signature is useless
    against the real token.
    """
    p = SigningPolicy.strict_default()
    odd_domain_fields = [
        *EIP712DOMAIN_FIELDS,
        {"name": "salt", "type": "bytes32"},
    ]
    domain = {
        "name": "United Stables",
        "version": "1",
        "chainId": BSC_MAINNET_CHAIN_ID,
        "verifyingContract": U_MAINNET,
    }
    types = {
        "EIP712Domain": odd_domain_fields,
        "TransferWithAuthorization": TWA_FIELDS,
    }
    assert check(p, domain, types, _twa_msg(), now=NOW) == "TransferWithAuthorization"


# ── Structure / domain shape ─────────────────────────────────────────────


def test_rejects_null_chainId():
    """chainId present but None — treated same as missing."""
    p = SigningPolicy.strict_default()
    with pytest.raises(PolicyViolation, match="missing chainId"):
        _twa_call(p, domain_overrides={"chainId": None})


def test_rejects_missing_chainId_drop_key():
    p = SigningPolicy.strict_default()
    domain = {"name": "United Stables", "version": "1", "verifyingContract": U_MAINNET}
    types = {"EIP712Domain": EIP712DOMAIN_FIELDS, "TransferWithAuthorization": TWA_FIELDS}
    with pytest.raises(PolicyViolation, match="missing chainId"):
        check(p, domain, types, _twa_msg(), now=NOW)


def test_rejects_missing_verifying_contract():
    p = SigningPolicy.strict_default()
    domain = {"name": "United Stables", "version": "1", "chainId": BSC_MAINNET_CHAIN_ID}
    types = {"EIP712Domain": EIP712DOMAIN_FIELDS, "TransferWithAuthorization": TWA_FIELDS}
    with pytest.raises(PolicyViolation, match="missing verifyingContract"):
        check(p, domain, types, _twa_msg(), now=NOW)


def test_rejects_multiple_non_domain_structs():
    p = SigningPolicy.strict_default()
    domain = {
        "name": "United Stables",
        "version": "1",
        "chainId": BSC_MAINNET_CHAIN_ID,
        "verifyingContract": U_MAINNET,
    }
    types = {
        "EIP712Domain": EIP712DOMAIN_FIELDS,
        "TransferWithAuthorization": TWA_FIELDS,
        "Permit": PERMIT_FIELDS,
    }
    with pytest.raises(PolicyViolation, match="multiple non-EIP712Domain"):
        check(p, domain, types, _twa_msg(), now=NOW)


# ── Composition ──────────────────────────────────────────────────────────


def test_extend_unions_allowlists():
    p = SigningPolicy.strict_default()
    p2 = p.extend(
        primary_type_allowlist={"Quote"},
        domain_allowlist={(56, "0x" + "9" * 40)},
    )
    # Original untouched
    assert p.primary_type_allowlist == EIP3009_TYPES
    # New extended
    assert "Quote" in p2.primary_type_allowlist
    assert "TransferWithAuthorization" in p2.primary_type_allowlist
    assert (56, "0x" + "9" * 40) in p2.domain_allowlist
    assert (56, U_MAINNET) in p2.domain_allowlist  # original kept


def test_extend_overrides_scalars():
    p = SigningPolicy.strict_default().extend(max_validity_window_seconds=300)
    assert p.max_validity_window_seconds == 300
    assert p.max_future_validity_seconds == 900  # untouched


def test_permissive_passes_unknown_domain_and_unknown_type():
    p = SigningPolicy.permissive()
    domain = {"chainId": 999, "verifyingContract": "0x" + "f" * 40}
    types = {
        "EIP712Domain": EIP712DOMAIN_FIELDS,
        "SomethingExotic": [{"name": "x", "type": "uint256"}],
    }
    msg = {"x": 1}
    # Must not raise
    pt = check(p, domain, types, msg, now=NOW)
    assert pt == "SomethingExotic"


# ── Error diagnostics ────────────────────────────────────────────────────


def test_policy_violation_carries_structured_diagnostics():
    p = SigningPolicy.strict_default()
    bad_addr = "0x" + "1" * 40
    try:
        _twa_call(p, domain_overrides={"verifyingContract": bad_addr})
    except PolicyViolation as e:
        assert e.primary_type == "TransferWithAuthorization"
        assert e.chain_id == BSC_MAINNET_CHAIN_ID
        # checksum form
        assert e.verifying_contract == bad_addr
        # __str__ contains all parts
        s = str(e)
        assert "TransferWithAuthorization" in s
        assert str(BSC_MAINNET_CHAIN_ID) in s
        assert bad_addr in s
    else:
        pytest.fail("expected PolicyViolation")


# ── infer_primary_type ───────────────────────────────────────────────────


def test_infer_primary_type_returns_non_domain():
    assert (
        infer_primary_type({"EIP712Domain": [], "TransferWithAuthorization": []})
        == "TransferWithAuthorization"
    )


def test_infer_primary_type_rejects_empty():
    with pytest.raises(PolicyViolation, match="no non-EIP712Domain"):
        infer_primary_type({"EIP712Domain": []})


def test_infer_primary_type_rejects_multiple():
    with pytest.raises(PolicyViolation, match="multiple"):
        infer_primary_type({"EIP712Domain": [], "A": [], "B": []})


# ── Bundle-level sanity ──────────────────────────────────────────────────


def test_eip3009_types_set_contents():
    assert EIP3009_TYPES == frozenset({"TransferWithAuthorization", "ReceiveWithAuthorization"})


def test_permit_unbounded_types_contents():
    assert PERMIT_UNBOUNDED_TYPES == frozenset({"Permit", "PermitSingle", "PermitBatch"})


# ── Serialization ────────────────────────────────────────────────────────


def test_to_dict_returns_sorted_deterministic_output():
    p = SigningPolicy.strict_default()
    d = p.to_dict()
    # Each list should be sorted (deterministic output for diff-friendly storage)
    assert d["domain_allowlist"] == sorted(d["domain_allowlist"])
    assert d["primary_type_allowlist"] == sorted(d["primary_type_allowlist"])
    # frozensets become lists
    assert isinstance(d["domain_allowlist"], list)
    assert isinstance(d["primary_type_allowlist"], list)
    # nested tuples become nested lists (TOML/JSON friendly)
    assert all(isinstance(pair, list) and len(pair) == 2 for pair in d["domain_allowlist"])


def test_from_dict_round_trips_strict_default():
    p = SigningPolicy.strict_default()
    p2 = SigningPolicy.from_dict(p.to_dict())
    assert p == p2


def test_from_dict_round_trips_extended():
    p = SigningPolicy.strict_default().extend(
        domain_allowlist={(1, "0x" + "9" * 40)},
        primary_type_allowlist={"MyOrder"},
        max_validity_window_seconds=300,
    )
    p2 = SigningPolicy.from_dict(p.to_dict())
    assert p == p2


def test_from_dict_handles_missing_keys_with_defaults():
    p = SigningPolicy.from_dict({})
    assert p.domain_allowlist == frozenset()
    assert p.max_validity_window_seconds == 600
    assert p.max_future_validity_seconds == 900
    assert p.allow_unknown_domain is False


def test_from_dict_rejects_malformed_domain_entry():
    with pytest.raises(ValueError, match=r"domain_allowlist\[0\]"):
        SigningPolicy.from_dict({"domain_allowlist": ["not-a-pair"]})


# ── __str__ ──────────────────────────────────────────────────────────────


def test_str_contains_canonical_sections():
    p = SigningPolicy.strict_default()
    s = str(p)
    assert "SigningPolicy(" in s
    assert "domain_allowlist (2 entries)" in s
    assert "TransferWithAuthorization" in s
    assert "Permit" in s
    assert "allow_unknown_domain=False" in s


def test_str_handles_empty_policy_cleanly():
    p = SigningPolicy(domain_allowlist=frozenset())
    s = str(p)
    assert "(none)" in s
    # primary_type allowlist empty → "(any)" marker for "no whitelist applied"
    assert "(any)" in s


# ── permissive() env guard ──────────────────────────────────────────────


@pytest.mark.parametrize(
    "env_value", ["prod", "production", "live", "mainnet-prod", "PROD", " Production ", "LIVE"]
)
def test_permissive_refuses_in_production(monkeypatch, env_value):
    monkeypatch.setenv("ENV", env_value)
    with pytest.raises(RuntimeError, match="indicates production"):
        SigningPolicy.permissive()


@pytest.mark.parametrize("env_value", ["", "dev", "development", "test", "staging", "qa"])
def test_permissive_allowed_in_non_production(monkeypatch, env_value):
    monkeypatch.setenv("ENV", env_value)
    p = SigningPolicy.permissive()
    assert p.allow_unknown_domain is True


def test_permissive_break_glass_in_production(monkeypatch):
    """allow_in_production=True bypasses the guard but still logs WARN."""
    monkeypatch.setenv("ENV", "production")
    p = SigningPolicy.permissive(allow_in_production=True)
    assert p.allow_unknown_domain is True


def test_permissive_reads_environment_var_fallback(monkeypatch):
    """Falls back to ENVIRONMENT when ENV is unset."""
    monkeypatch.delenv("ENV", raising=False)
    monkeypatch.setenv("ENVIRONMENT", "prod")
    with pytest.raises(RuntimeError, match="indicates production"):
        SigningPolicy.permissive()
