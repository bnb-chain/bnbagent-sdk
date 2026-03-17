"""Shared ABI loaders for common contract interfaces."""

import json
from pathlib import Path

_ABI_DIR = Path(__file__).parent / "abis"


def load_erc20_abi() -> list:
    """Load minimal ERC20 ABI (approve, balanceOf, allowance, transfer, allocateTo)."""
    with open(_ABI_DIR / "ERC20.json") as f:
        return json.load(f)
