"""Exceptions raised by SigningPolicy enforcement."""

from __future__ import annotations


class PolicyViolation(Exception):
    """A SigningPolicy check rejected a sign_typed_data request.

    Carries structured fields so callers can render user-facing diagnostics
    or branch on rejection reason without parsing the message string.
    """

    def __init__(
        self,
        reason: str,
        *,
        primary_type: str | None = None,
        chain_id: int | None = None,
        verifying_contract: str | None = None,
    ) -> None:
        self.reason = reason
        self.primary_type = primary_type
        self.chain_id = chain_id
        self.verifying_contract = verifying_contract
        msg_parts = [reason]
        if primary_type:
            msg_parts.append(f"primary_type={primary_type}")
        if chain_id is not None:
            msg_parts.append(f"chain_id={chain_id}")
        if verifying_contract:
            msg_parts.append(f"verifyingContract={verifying_contract}")
        super().__init__("; ".join(msg_parts))
