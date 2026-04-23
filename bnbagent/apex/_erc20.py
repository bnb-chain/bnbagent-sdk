"""Minimal ERC-20 client used by ``APEXClient`` for payment-token helpers.

Underscore-prefixed: not part of the public SDK surface. External callers
should use the helpers exposed on ``APEXClient`` (``token_decimals``,
``token_balance``, ``approve_payment_token``, ...) which delegate here.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from web3 import Web3

from ..core.contract_mixin import ContractClientMixin
from ..wallets.wallet_provider import WalletProvider


def _load_abi() -> list:
    abi_path = Path(__file__).parent / "abis" / "ERC20Minimal.json"
    return json.loads(abi_path.read_text())


class MinimalERC20Client(ContractClientMixin):
    """Only ``decimals / symbol / balanceOf / allowance / approve``."""

    def __init__(
        self,
        web3: Web3,
        token_address: str,
        wallet_provider: WalletProvider | None = None,
    ) -> None:
        self.w3 = web3
        self.address = Web3.to_checksum_address(token_address)
        self.contract = self.w3.eth.contract(address=self.address, abi=_load_abi())
        self._wallet_provider = wallet_provider
        self._account = wallet_provider.address if wallet_provider is not None else None

    # ── Views ──

    def decimals(self) -> int:
        return self._call_with_retry(self.contract.functions.decimals())

    def symbol(self) -> str:
        return self._call_with_retry(self.contract.functions.symbol())

    def balance_of(self, account: str) -> int:
        return self._call_with_retry(
            self.contract.functions.balanceOf(Web3.to_checksum_address(account))
        )

    def allowance(self, owner: str, spender: str) -> int:
        return self._call_with_retry(
            self.contract.functions.allowance(
                Web3.to_checksum_address(owner),
                Web3.to_checksum_address(spender),
            )
        )

    # ── Writes ──

    def approve(self, spender: str, amount: int) -> dict[str, Any]:
        fn = self.contract.functions.approve(Web3.to_checksum_address(spender), amount)
        return self._send_tx(fn)
