"""ERC-20 token client — minimal interface for payment-token helpers."""

from __future__ import annotations

from ..core.abis import load_abi
from .client import MinimalERC20Client


def load_erc20_abi() -> list:
    """Load the minimal ERC-20 ABI bundled with this package."""
    return load_abi("ERC20.json")


__all__ = ["MinimalERC20Client", "load_erc20_abi"]
