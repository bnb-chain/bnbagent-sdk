"""SigningPolicy — declarative ruleset for guarding sign_typed_data calls.

The policy is the SDK's first line of defense against blind-sign attacks
delivered through an EIP-712 typed-data payload. It is intentionally
**fail-closed by default**: an unknown ``verifyingContract`` or an
unrecognised ``primaryType`` is refused. Callers extend the policy with
explicit allowlist entries when they know what they are doing.

Design references:
- Phase 0 on-chain verification of U token (United Stables, version "1")
  confirmed both EIP-3009 and EIP-2612 Permit support, making the Permit
  denylist a real defense, not a theoretical one.
- CDP Server Wallets ``evmTypedDataVerifyingContract`` /
  ``evmTypedDataField`` policy schema (Coinbase) — same shape.
- x402-fetch ``maxValue`` default of 0.1 USDC informs the validity
  window defaults.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field, replace
from typing import Any

from ..networks import known_payment_tokens

logger = logging.getLogger(__name__)


# ── Canonical EIP-712 primary types ────────────────────────────────────────
#
# Source: EIP-3009 (canonical names "TransferWithAuthorization" /
# "ReceiveWithAuthorization"); EIP-2612 ("Permit"); Uniswap Permit2 source
# at github.com/Uniswap/permit2 (allowance-transfer: PermitSingle/PermitBatch;
# signature-transfer: PermitTransferFrom/PermitBatchTransferFrom).

EIP3009_TYPES: frozenset[str] = frozenset({
    "TransferWithAuthorization",
    "ReceiveWithAuthorization",
})

PERMIT_UNBOUNDED_TYPES: frozenset[str] = frozenset({
    "Permit",          # EIP-2612 — long-lived allowance to a spender
    "PermitSingle",    # Permit2 AllowanceTransfer — long-lived allowance
    "PermitBatch",     # Permit2 AllowanceTransfer (batch)
})

# Permit2 SignatureTransfer family — opt-in only, NOT in default allowlist.
# These are *safer* than the unbounded family because the spender contract
# binds (to, requestedAmount) at call time, but full enforcement requires
# witness validation we don't yet do. Callers explicitly extend the policy
# to use these.
PERMIT2_SIGNATURE_TRANSFER_TYPES: frozenset[str] = frozenset({
    "PermitTransferFrom",
    "PermitBatchTransferFrom",
})


@dataclass(frozen=True)
class SigningPolicy:
    """Immutable signing ruleset.

    Construct via :meth:`strict_default` (recommended) or :meth:`permissive`
    (testing only). Extend with :meth:`extend` to add new known domains or
    primary types.

    Attributes:
        domain_allowlist: ``(chain_id, checksum_address)`` pairs that are
            allowed as the EIP-712 ``verifyingContract``. Anything else is
            refused unless :attr:`allow_unknown_domain` is True.
        primary_type_allowlist: EIP-712 primary type names accepted.
        primary_type_denylist: Names refused unconditionally; takes
            precedence over the allowlist so a misconfiguration can't
            re-enable a dangerous type.
        validity_required_primary_types: Primary types that MUST carry
            valid ``validBefore`` / ``validAfter`` fields, validated
            against :attr:`max_validity_window_seconds` and
            :attr:`max_future_validity_seconds`.
        max_validity_window_seconds: Upper bound on
            ``validBefore - validAfter``. Defaults to 600s (10 min) which
            matches x402's reference client expectations.
        max_future_validity_seconds: Upper bound on
            ``validBefore - now``. Defaults to 900s (15 min) to leave a
            clock-skew buffer.
        allow_unknown_domain: Bypass :attr:`domain_allowlist`. Must be set
            explicitly; default ``False``.
    """

    domain_allowlist: frozenset[tuple[int, str]] = field(default_factory=frozenset)
    primary_type_allowlist: frozenset[str] = field(default_factory=frozenset)
    primary_type_denylist: frozenset[str] = field(default_factory=frozenset)
    validity_required_primary_types: frozenset[str] = field(default_factory=frozenset)
    max_validity_window_seconds: int = 600
    max_future_validity_seconds: int = 900
    allow_unknown_domain: bool = False

    # ── Factory presets ────────────────────────────────────────────────

    @classmethod
    def strict_default(cls) -> "SigningPolicy":
        """Recommended fail-closed default for direct-SDK callers.

        Defaults are deliberately narrow:
        - domain: only the U-token deployments registered in
          ``bnbagent.networks``;
        - allowlist: only EIP-3009 ``TransferWithAuthorization`` and
          ``ReceiveWithAuthorization`` — the well-understood single-use
          authorisation pattern x402 uses;
        - denylist: every unbounded Permit variant
          (ERC-2612 / Permit2 AllowanceTransfer);
        - validity: required for the allowlisted EIP-3009 types, capped
          at 600s window / 900s future.

        Permit2 SignatureTransfer types are intentionally **not**
        allowlisted by default; extend the policy if you need them.
        """
        return cls(
            domain_allowlist=known_payment_tokens(),
            primary_type_allowlist=EIP3009_TYPES,
            primary_type_denylist=PERMIT_UNBOUNDED_TYPES,
            validity_required_primary_types=EIP3009_TYPES,
            max_validity_window_seconds=600,
            max_future_validity_seconds=900,
            allow_unknown_domain=False,
        )

    #: Environment values (case-insensitive) that block ``permissive()``
    #: construction unless the caller passes ``allow_in_production=True``.
    PRODUCTION_ENV_MARKERS: frozenset[str] = frozenset({
        "prod", "production", "live", "mainnet-prod",
    })

    @classmethod
    def permissive(cls, *, allow_in_production: bool = False) -> "SigningPolicy":
        """⚠️ Testing-only escape: allow_unknown_domain=True and empty deny/allow.

        Refuses to construct when ``ENV`` or ``ENVIRONMENT`` env vars indicate
        a production-class environment (case-insensitive match against
        :attr:`PRODUCTION_ENV_MARKERS`: ``prod`` / ``production`` / ``live`` /
        ``mainnet-prod``). Pass ``allow_in_production=True`` for break-glass
        scenarios where you understand the consequences.

        Logs a WARNING on construction (always — even outside production) so
        the bypass shows up in audit grep. Never use this in agent-reachable
        code paths.

        Raises:
            RuntimeError: When env indicates production and
                ``allow_in_production`` is not set.
        """
        env_raw = os.environ.get("ENV") or os.environ.get("ENVIRONMENT") or ""
        env = env_raw.strip().lower()
        if env in cls.PRODUCTION_ENV_MARKERS and not allow_in_production:
            raise RuntimeError(
                f"SigningPolicy.permissive() refused: ENV={env_raw!r} indicates "
                f"production (matches {sorted(cls.PRODUCTION_ENV_MARKERS)}). "
                f"Pass allow_in_production=True if this is intentional (e.g. "
                f"break-glass)."
            )
        logger.warning(
            "SigningPolicy.permissive() in use — POLICY DISABLED. "
            "This bypasses ALL signing guards; only acceptable in tests. "
            "(env=%r, allow_in_production=%s)", env_raw, allow_in_production,
        )
        return cls(
            domain_allowlist=frozenset(),
            primary_type_allowlist=frozenset(),
            primary_type_denylist=frozenset(),
            validity_required_primary_types=frozenset(),
            allow_unknown_domain=True,
        )

    # ── Composition ────────────────────────────────────────────────────

    def extend(
        self,
        *,
        domain_allowlist: set[tuple[int, str]] | frozenset[tuple[int, str]] | None = None,
        primary_type_allowlist: set[str] | frozenset[str] | None = None,
        primary_type_denylist: set[str] | frozenset[str] | None = None,
        validity_required_primary_types: set[str] | frozenset[str] | None = None,
        max_validity_window_seconds: int | None = None,
        max_future_validity_seconds: int | None = None,
        allow_unknown_domain: bool | None = None,
    ) -> "SigningPolicy":
        """Return a new policy with extended/overridden fields.

        Set-like arguments are *unioned* with the current value (additive).
        Scalar arguments replace the current value when provided.
        """
        kwargs: dict[str, Any] = {}
        if domain_allowlist is not None:
            kwargs["domain_allowlist"] = self.domain_allowlist | frozenset(
                domain_allowlist
            )
        if primary_type_allowlist is not None:
            kwargs["primary_type_allowlist"] = self.primary_type_allowlist | frozenset(
                primary_type_allowlist
            )
        if primary_type_denylist is not None:
            kwargs["primary_type_denylist"] = self.primary_type_denylist | frozenset(
                primary_type_denylist
            )
        if validity_required_primary_types is not None:
            kwargs["validity_required_primary_types"] = (
                self.validity_required_primary_types
                | frozenset(validity_required_primary_types)
            )
        if max_validity_window_seconds is not None:
            kwargs["max_validity_window_seconds"] = max_validity_window_seconds
        if max_future_validity_seconds is not None:
            kwargs["max_future_validity_seconds"] = max_future_validity_seconds
        if allow_unknown_domain is not None:
            kwargs["allow_unknown_domain"] = allow_unknown_domain
        return replace(self, **kwargs)

    # ── Serialization ──────────────────────────────────────────────────

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a plain dict suitable for JSON / TOML round-trips.

        Sets become sorted lists for deterministic output; tuples become
        nested lists (TOML-friendly). Round-trips via :meth:`from_dict`.
        """
        return {
            "domain_allowlist": sorted(
                [list(pair) for pair in self.domain_allowlist]
            ),
            "primary_type_allowlist": sorted(self.primary_type_allowlist),
            "primary_type_denylist": sorted(self.primary_type_denylist),
            "validity_required_primary_types": sorted(
                self.validity_required_primary_types
            ),
            "max_validity_window_seconds": self.max_validity_window_seconds,
            "max_future_validity_seconds": self.max_future_validity_seconds,
            "allow_unknown_domain": self.allow_unknown_domain,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "SigningPolicy":
        """Reconstruct a SigningPolicy from its :meth:`to_dict` output.

        Missing keys fall back to the dataclass defaults (empty sets /
        600s window / 900s future / False unknown-domain) — same shape as
        constructing ``SigningPolicy()`` directly. Lists are converted to
        frozensets; nested-list domain entries become tuples.

        Raises:
            ValueError: On malformed entries (e.g. a domain entry that is
                not a two-element list).
        """
        raw_domains = d.get("domain_allowlist", []) or []
        domain_pairs: set[tuple[int, str]] = set()
        for i, entry in enumerate(raw_domains):
            if not isinstance(entry, (list, tuple)) or len(entry) != 2:
                raise ValueError(
                    f"domain_allowlist[{i}] must be a [chain_id, address] "
                    f"pair, got {entry!r}"
                )
            domain_pairs.add((int(entry[0]), str(entry[1])))
        return cls(
            domain_allowlist=frozenset(domain_pairs),
            primary_type_allowlist=frozenset(d.get("primary_type_allowlist", []) or []),
            primary_type_denylist=frozenset(d.get("primary_type_denylist", []) or []),
            validity_required_primary_types=frozenset(
                d.get("validity_required_primary_types", []) or []
            ),
            max_validity_window_seconds=int(d.get("max_validity_window_seconds", 600)),
            max_future_validity_seconds=int(d.get("max_future_validity_seconds", 900)),
            allow_unknown_domain=bool(d.get("allow_unknown_domain", False)),
        )

    # ── Human-readable output ──────────────────────────────────────────

    def __str__(self) -> str:
        """Multi-line operator-friendly summary; safe for logs + `bcs` CLI."""
        n_domains = len(self.domain_allowlist)
        lines = [
            "SigningPolicy(",
            f"  domain_allowlist ({n_domains} {'entry' if n_domains == 1 else 'entries'}):",
        ]
        for cid, addr in sorted(self.domain_allowlist):
            lines.append(f"    - chain_id={cid} verifyingContract={addr}")
        if n_domains == 0:
            lines.append("    (none)")
        lines.append(
            f"  primary_type_allowlist={sorted(self.primary_type_allowlist) or '(any)'}"
        )
        lines.append(
            f"  primary_type_denylist={sorted(self.primary_type_denylist) or '(none)'}"
        )
        lines.append(
            f"  validity: window<={self.max_validity_window_seconds}s, "
            f"future<={self.max_future_validity_seconds}s, "
            f"required_for={sorted(self.validity_required_primary_types) or '(none)'}"
        )
        lines.append(f"  allow_unknown_domain={self.allow_unknown_domain}")
        lines.append(")")
        return "\n".join(lines)
