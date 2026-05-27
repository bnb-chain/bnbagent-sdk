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
from .policy import SigningPolicy

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

    # ── Validity window (only if primary type requires it) ───────────
    if primary_type in policy.validity_required_primary_types:
        _check_validity(policy, primary_type, message, chain_id, verifying, now)

    return primary_type


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
