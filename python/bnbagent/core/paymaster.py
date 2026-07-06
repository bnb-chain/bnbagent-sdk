"""
Paymaster module for ERC-4337 account abstraction.

Provides methods for interacting with paymaster services to sponsor gas fees.
"""

from __future__ import annotations

import logging
from typing import Any

import requests
from web3 import Web3

logger = logging.getLogger(__name__)


def _to_hex(value: int | str | bytes | None, default: str = "0x0") -> str:
    """
    Convert a value to hex string format.

    Args:
        value: Value to convert (int, str, bytes, or None)
        default: Default hex string if value is None or empty

    Returns:
        str: Hex string with 0x prefix
    """
    if value is None:
        return default

    if isinstance(value, int):
        return Web3.to_hex(value)
    elif isinstance(value, bytes):
        return "0x" + value.hex() if value else default
    elif isinstance(value, str):
        if value.startswith("0x"):
            return value
        elif value:
            return f"0x{value}"
        else:
            return default
    else:
        return default


def _to_address_hex(address: str | None, default: str = "0x0") -> str:
    """
    Convert an address to checksummed hex string format.

    Args:
        address: Address string or None
        default: Default address if None or empty

    Returns:
        str: Checksummed address hex string
    """
    if not address:
        return default

    if isinstance(address, str):
        try:
            return Web3.to_checksum_address(address)
        except Exception as e:
            logger.warning(f"Invalid address '{address}', using default '{default}': {e}")
            return default
    else:
        return default


class Paymaster:
    """
    Paymaster client for ERC-4337 account abstraction.

    Handles communication with paymaster services to sponsor gas fees for transactions.
    """

    def __init__(
        self,
        paymaster_url: str,
        debug: bool = False,
    ):
        """
        Initialize the Paymaster client.

        Args:
            paymaster_url: URL of the paymaster service
            debug: Enable debug logging (default: False)

        Example:
            >>> from bnbagent import Paymaster
            >>> paymaster = Paymaster(
            ...     paymaster_url="https://bsc-megafuel.nodereal.io",
            ...     debug=True
            ... )
        """
        self.paymaster_url = paymaster_url
        self.debug = debug

        logger.debug(f"Initialized Paymaster with URL: {paymaster_url}")

    def _make_rpc_request(
        self,
        method: str,
        params: list,
        request_id: int = 1,
        headers: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """
        Make an RPC request to the paymaster service.

        Args:
            method: RPC method name
            params: Method parameters
            request_id: Request ID (default: 1)
            headers: Optional extra HTTP headers

        Returns:
            dict: RPC response

        Raises:
            requests.exceptions.RequestException: If the request fails
        """
        payload = {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": method,
            "params": params,
        }

        merged_headers = dict(headers) if headers else {}
        merged_headers.setdefault("Content-Type", "application/json")

        try:
            logger.debug(f"Making RPC request: {method} to {self.paymaster_url}")
            response = requests.post(
                self.paymaster_url,
                headers=merged_headers,
                json=payload,
                timeout=30,
            )
            response.raise_for_status()
            result = response.json()

            if "error" in result:
                error_msg = result["error"].get("message", "Unknown error")
                error_data = result["error"].get("data", {})
                error_code = result["error"].get("code", -1)
                logger.error(f"RPC error [{error_code}]: {error_msg}")
                if error_data:
                    logger.error(f"Error data: {error_data}")
                raise RuntimeError(f"RPC error [{error_code}]: {error_msg}")

            return result
        except requests.exceptions.RequestException as e:
            logger.error(f"Failed to make RPC request: {e}")
            if hasattr(e, "response") and e.response is not None:
                logger.error(f"Response: {e.response.text}")
            raise

    def eth_getTransactionCount(
        self,
        address: str,
        block: str = "latest",
    ) -> int:
        """
        Get the transaction count (nonce) for an address.

        Args:
            address: Ethereum address (hex string with 0x prefix)
            block: Block number or tag (default: "latest")

        Returns:
            int: Transaction count (nonce)

        Example:
            >>> nonce = paymaster.eth_getTransactionCount("0x...")
            >>> print(f"Nonce: {nonce}")
        """
        if not address.startswith("0x"):
            address = "0x" + address

        # Ensure address is checksummed
        address = Web3.to_checksum_address(address)

        result = self._make_rpc_request(
            method="eth_getTransactionCount",
            params=[address, block],
        )

        nonce_hex = result.get("result")
        if nonce_hex is None:
            raise ValueError("Failed to get transaction count: missing 'result' field")

        # Convert hex to int
        nonce = int(nonce_hex, 16)
        logger.debug(f"Transaction count for {address}: {nonce}")

        return nonce

    def eth_sendRawTransaction(
        self,
        signed_transaction: str,
        tx_options: dict[str, Any] | None = None,
    ) -> str:
        """
        Send a signed raw transaction to the paymaster service.

        Args:
            signed_transaction: Signed transaction hex string (with 0x prefix)

        Returns:
            str: Transaction hash

        Example:
            >>> tx_hash = paymaster.eth_sendRawTransaction("0x...")
            >>> print(f"Transaction hash: {tx_hash}")
        """
        if not signed_transaction.startswith("0x"):
            signed_transaction = "0x" + signed_transaction

        logger.debug(f"Sending raw transaction: {signed_transaction[:100]}...")
        result = self._make_rpc_request(
            method="eth_sendRawTransaction",
            params=[signed_transaction],
            headers=tx_options,
        )

        tx_hash = result.get("result")
        if tx_hash is None:
            raise ValueError("Failed to send raw transaction: missing 'result' field")

        logger.debug(f"Transaction sent: {tx_hash}")

        return tx_hash

    def isSponsorable(self, tx: dict[str, Any]) -> bool:
        """
        Check if a transaction is sponsorable by the paymaster.

        Args:
            tx: Transaction dictionary with keys: to, from, value, data, gas

        Returns:
            bool: True if transaction is sponsorable, False otherwise
        """
        # Convert transaction parameters to hex format
        to_hex = _to_address_hex(tx.get("to"))
        from_hex = _to_address_hex(tx.get("from"))
        value_hex = _to_hex(tx.get("value", 0))
        data_hex = _to_hex(tx.get("data", b""))
        gas_hex = _to_hex(tx.get("gas", 0))

        logger.debug(
            "Checking if transaction is sponsorable:"
            f" {to_hex}, {from_hex},"
            f" {value_hex}, {data_hex}, {gas_hex}"
        )
        result = self._make_rpc_request(
            method="pm_isSponsorable",
            params=[
                {
                    "to": to_hex,
                    "from": from_hex,
                    "value": value_hex,
                    "data": data_hex,
                    "gas": gas_hex,
                }
            ],
        )
        res = result.get("result")
        if res is None:
            logger.error("Invalid response: missing 'result' field")
            return False
        return res.get("sponsorable", False)
