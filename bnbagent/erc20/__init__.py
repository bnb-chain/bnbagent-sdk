"""ERC-20 token client — minimal interface for payment-token helpers."""

from __future__ import annotations

import json
from pathlib import Path

from .client import MinimalERC20Client


def load_erc20_abi() -> list:
    """Load the minimal ERC-20 ABI bundled with this package."""
    abi_path = Path(__file__).parent / "abis" / "ERC20.json"
    return json.loads(abi_path.read_text())


__all__ = ["MinimalERC20Client", "load_erc20_abi"]
