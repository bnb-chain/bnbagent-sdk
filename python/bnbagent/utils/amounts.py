"""Token amount conversion between human-readable and raw on-chain units.

The SDK's protocol clients work in raw integer units (wei-style, ``10**decimals``).
These helpers convert at the boundary where humans (or configs) speak decimal
amounts. ``Decimal``-based on purpose — ``float`` arithmetic corrupts 18-decimal
amounts (``int(1.1 * 10**18)`` is already wrong).
"""

from __future__ import annotations

from decimal import Decimal


def to_raw(amount: str | int | float, decimals: int) -> int:
    """Convert a human-readable amount (e.g. ``"1.5"``) to raw on-chain units."""
    return int(Decimal(str(amount)) * (Decimal(10) ** decimals))


def from_raw(raw: int, decimals: int) -> str:
    """Convert raw on-chain units to a human-readable decimal string."""
    value = Decimal(raw) / (Decimal(10) ** decimals)
    return format(value.normalize(), "f")
