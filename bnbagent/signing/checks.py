"""Pure checking functions for SigningPolicy enforcement.

Kept separate from :mod:`bnbagent.signing.policy` so they can be unit-tested
without instantiating a full SigningPolicy and so future variations (e.g.
async-loggable check, dry-run check) can compose them.
"""

from __future__ import annotations

import time
from typing import Any

from web3 import Web3

from .errors import PolicyViolation
from .policy import EIP3009_CANONICAL_FIELDS, EIP3009_TYPES, SigningPolicy

EIP712_DOMAIN_TYPE_NAME = "EIP712Domain"


def infer_primary_type(types: dict[str, Any]) -> str:
    """Return the single non-EIP712Domain struct name in ``types``.

    Raises PolicyViolation if there isn't exactly one. Multiple non-domain
    structs would create ambiguity over what gets signed and is explicitly
    rejected — caller must split into separate sign calls.
    """
    non_domain = [k for k in types.keys() if k != EIP712_DOMAIN_TYPE_NAME]
    if len(non_domain) == 0:
        raise PolicyViolation(
            "EIP-712 types contains no non-EIP712Domain struct"
        )
    if len(non_domain) > 1:
        raise PolicyViolation(
            f"EIP-712 types contains multiple non-EIP712Domain structs: "
            f"{non_domain}; sign one at a time to avoid primary-type ambiguity"
        )
    return non_domain[0]


def _checksum_or_none(addr: Any) -> str | None:
    if not isinstance(addr, str):
        return None
    try:
        return Web3.to_checksum_address(addr)
    except (ValueError, Exception):
        return None


def check(
    policy: SigningPolicy,
    domain: dict[str, Any],
    types: dict[str, Any],
    message: dict[str, Any],
    *,
    now: int | None = None,
) -> str:
    """Apply ``policy`` to a typed-data sign request.

    Returns the inferred ``primary_type`` on success. Raises
    :class:`PolicyViolation` on first failure (does not aggregate errors).

    Ordering matters — denylist before allowlist before domain before
    validity — so the most categorical refusal is reported first.

    Args:
        policy: The policy to enforce.
        domain: EIP-712 domain (must contain ``chainId`` and
            ``verifyingContract``).
        types: EIP-712 types dict.
        message: The struct being signed.
        now: Override current unix time (test seam). Defaults to
            ``time.time()``.
    """
    primary_type = infer_primary_type(types)

    # ── Structure: domain must have chainId + verifyingContract ──────
    if domain.get("chainId") is None:
        raise PolicyViolation(
            "EIP-712 domain missing chainId — refusing to sign",
            primary_type=primary_type,
        )
    if domain.get("verifyingContract") is None:
        raise PolicyViolation(
            "EIP-712 domain missing verifyingContract — refusing to sign",
            primary_type=primary_type,
        )

    try:
        chain_id = int(domain["chainId"])
    except (TypeError, ValueError) as e:
        raise PolicyViolation(
            f"EIP-712 domain chainId is not integer-coercible: "
            f"{domain['chainId']!r}",
            primary_type=primary_type,
        ) from e
    verifying = _checksum_or_none(domain["verifyingContract"])
    if verifying is None:
        raise PolicyViolation(
            f"EIP-712 domain verifyingContract is not a valid address: "
            f"{domain['verifyingContract']!r}",
            primary_type=primary_type,
            chain_id=chain_id,
        )

    # ── Denylist takes precedence (defense against allowlist misconfig) ──
    if primary_type in policy.primary_type_denylist:
        raise PolicyViolation(
            f"primary type {primary_type!r} is denylisted "
            f"(unbounded allowance type — unsafe for agent signing)",
            primary_type=primary_type,
            chain_id=chain_id,
            verifying_contract=verifying,
        )

    # ── Allowlist ────────────────────────────────────────────────────
    # Empty allowlist == "no whitelist applied" (caller opted out, e.g.
    # SigningPolicy.permissive() for tests). Strict policies always seed
    # a non-empty allowlist.
    if (
        policy.primary_type_allowlist
        and primary_type not in policy.primary_type_allowlist
    ):
        raise PolicyViolation(
            f"primary type {primary_type!r} not in allowlist "
            f"(extend SigningPolicy to opt in)",
            primary_type=primary_type,
            chain_id=chain_id,
            verifying_contract=verifying,
        )

    # ── Domain allowlist ─────────────────────────────────────────────
    if not policy.allow_unknown_domain:
        if (chain_id, verifying) not in policy.domain_allowlist:
            raise PolicyViolation(
                f"domain (chain_id={chain_id}, verifyingContract={verifying}) "
                f"not in allowlist; extend SigningPolicy if intentional",
                primary_type=primary_type,
                chain_id=chain_id,
                verifying_contract=verifying,
            )

    # ── Field-shape pinning for known structs ────────────────────────
    _check_field_shape(types, primary_type, chain_id, verifying)

    # ── Validity window (only if primary type requires it) ───────────
    if primary_type in policy.validity_required_primary_types:
        _check_validity(policy, primary_type, message, chain_id, verifying, now)

    return primary_type


def _check_field_shape(
    types: dict[str, Any],
    primary_type: str,
    chain_id: int,
    verifying: str,
) -> None:
    """Pin the field-shape of structs whose shape we know canonically.

    The allowlist above is name-scoped, and ``types`` is caller-supplied — for
    x402 it arrives verbatim in an untrusted 402 response body. Without this
    check an attacker keeps the allowlisted name and rewrites the field's
    Solidity type, which silently changes what the encoder will accept: the
    canonical ``uint256`` is what makes a negative ``value`` unencodable, so
    swapping in ``int256`` removes a value-domain guard the downstream layers
    were relying on. Field order and count matter too — both feed the typeHash.

    Scope is keyed on the struct **name**, not on the policy: anything calling
    itself ``TransferWithAuthorization`` is held to the EIP-3009 shape even
    under ``permissive()``, because the canonical shape is a property of the
    name rather than of the ruleset. Types with any other name are untouched,
    so custom ``extend()`` primary types keep working.

    Two neighbouring surfaces are deliberately left unpinned:

    * ``EIP712Domain`` — EIP-712 makes every domain field optional, so there is
      no single canonical shape to pin against (``salt`` is legitimate, so is
      omitting ``version``). Checked for exploitability: altering the domain
      shape changes the domainSeparator, which makes the resulting signature
      useless against the real token, and an out-of-range ``chainId`` cannot
      pass the domain allowlist either.
    * The Permit2 family (:data:`PERMIT2_SIGNATURE_TRANSFER_TYPES`) — opt-in
      via ``extend()`` only, and the SDK carries no canonical field table for
      them. Pin them here if they ever enter a default policy.
    """
    if primary_type not in EIP3009_TYPES:
        return
    fields = types.get(primary_type)
    if not isinstance(fields, list):
        raise PolicyViolation(
            f"types[{primary_type!r}] must be a list of field descriptors, "
            f"got {type(fields).__name__}",
            primary_type=primary_type,
            chain_id=chain_id,
            verifying_contract=verifying,
        )
    actual = tuple(
        (f.get("name"), f.get("type")) if isinstance(f, dict) else (None, None)
        for f in fields
    )
    if actual != EIP3009_CANONICAL_FIELDS:
        expected_repr = ", ".join(f"{n} {t}" for n, t in EIP3009_CANONICAL_FIELDS)
        actual_repr = ", ".join(f"{n} {t}" for n, t in actual)
        raise PolicyViolation(
            f"types[{primary_type!r}] does not match the canonical EIP-3009 "
            f"field shape — refusing to sign a struct whose encoding differs "
            f"from the on-chain type. expected ({expected_repr}), "
            f"got ({actual_repr})",
            primary_type=primary_type,
            chain_id=chain_id,
            verifying_contract=verifying,
        )


def _check_validity(
    policy: SigningPolicy,
    primary_type: str,
    message: dict[str, Any],
    chain_id: int,
    verifying: str,
    now: int | None,
) -> None:
    if "validBefore" not in message or "validAfter" not in message:
        raise PolicyViolation(
            f"primary type {primary_type!r} requires validBefore + validAfter "
            f"in message; refusing to sign open-ended authorization",
            primary_type=primary_type,
            chain_id=chain_id,
            verifying_contract=verifying,
        )
    try:
        valid_before = int(message["validBefore"])
        valid_after = int(message["validAfter"])
    except (TypeError, ValueError) as e:
        raise PolicyViolation(
            f"validBefore / validAfter not integer-coercible: {e}",
            primary_type=primary_type,
            chain_id=chain_id,
            verifying_contract=verifying,
        ) from e

    if valid_before <= valid_after:
        raise PolicyViolation(
            f"validBefore ({valid_before}) must be > validAfter ({valid_after})",
            primary_type=primary_type,
            chain_id=chain_id,
            verifying_contract=verifying,
        )

    window = valid_before - valid_after
    if window > policy.max_validity_window_seconds:
        raise PolicyViolation(
            f"validity window {window}s exceeds max "
            f"{policy.max_validity_window_seconds}s",
            primary_type=primary_type,
            chain_id=chain_id,
            verifying_contract=verifying,
        )

    current = int(now if now is not None else time.time())
    if valid_before <= current:
        raise PolicyViolation(
            f"validBefore ({valid_before}) is already expired (current={current})",
            primary_type=primary_type,
            chain_id=chain_id,
            verifying_contract=verifying,
        )
    future = valid_before - current
    if future > policy.max_future_validity_seconds:
        raise PolicyViolation(
            f"validBefore {valid_before} is {future}s in the future, "
            f"exceeds max {policy.max_future_validity_seconds}s",
            primary_type=primary_type,
            chain_id=chain_id,
            verifying_contract=verifying,
        )
