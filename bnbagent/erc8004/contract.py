"""
Contract Interface Module

Handles interactions with the ERC-8004 Identity Registry smart contract.
Provides methods for registering agents and querying agent information.
"""

from __future__ import annotations

import concurrent.futures as _cf
import json
import logging
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from web3 import Web3
from web3.contract.contract import ContractFunction
from web3.types import TxReceipt

from ..core.contract_mixin import MAX_RETRIES, MIN_GAS_PRICE_WEI, RETRY_BASE_DELAY
from ..core.nonce_manager import NonceManager
from ..core.paymaster import Paymaster

if TYPE_CHECKING:
    from ..wallets import WalletProvider

logger = logging.getLogger(__name__)

# Default seconds to wait for a transaction receipt. web3.py's own default
# (120s) is too short on congested BNB Chain / paymaster-relayed paths.
DEFAULT_RECEIPT_TIMEOUT = 300


class ContractInterface:
    """
    Interface for interacting with ERC-8004 Identity Registry contract.

    Provides methods for:
    - Registering agents
    - Getting agent information
    - Setting and getting metadata
    """

    def __init__(
        self,
        web3: Web3,
        contract_address: str,
        wallet_provider: WalletProvider,
        paymaster: Paymaster | None = None,
        debug: bool = False,
        receipt_timeout: int = DEFAULT_RECEIPT_TIMEOUT,
    ):
        """
        Initialize the contract interface.

        Args:
            web3: Web3 instance connected to the blockchain
            contract_address: Address of the ERC-8004 Identity Registry contract
            wallet_provider: Wallet provider for signing transactions
            paymaster: Optional Paymaster instance for gas sponsorship.
                      If provided, used for nonce retrieval and transaction sending.
                      If None, uses standard Web3 transaction flow.
            debug: Enable debug logging
            receipt_timeout: Seconds to wait for a transaction receipt
                            (default: ``DEFAULT_RECEIPT_TIMEOUT`` = 300).
        """
        self.web3 = web3
        self.contract_address = Web3.to_checksum_address(contract_address)
        self.wallet_provider = wallet_provider
        self.paymaster = paymaster
        self.debug = debug
        self.receipt_timeout = receipt_timeout

        # Create contract instance
        self.contract = self.web3.eth.contract(
            address=self.contract_address, abi=self._get_default_abi()
        )

        if self.paymaster:
            logger.debug(
                "Initialized contract interface at %s with paymaster: %s",
                self.contract_address,
                self.paymaster.paymaster_url,
            )
        else:
            logger.debug(
                "Initialized contract interface at %s without paymaster (using standard Web3)",
                self.contract_address,
            )

    def _get_default_abi(self) -> list[dict[str, Any]]:
        """
        Get the default ERC-8004 Identity Registry ABI from file.

        Returns:
            List of ABI function definitions
        """
        # Get the path to the ABI file relative to this module
        abi_file_path = Path(__file__).parent / "abis" / "IdentityRegistry.json"

        try:
            with open(abi_file_path) as f:
                return json.load(f)
        except Exception as e:
            raise ValueError(f"Failed to load ABI from file {abi_file_path}: {str(e)}") from e

    def _run_preflight(self, transaction: dict, description: str) -> None:
        """Simulate the transaction via eth_call before broadcasting.

        Surfaces revert reasons early without spending gas. Mirrors the same
        pre-flight logic in core/contract_mixin.py:_send_tx().
        """
        call_params = {
            "from": transaction.get("from"),
            "to": transaction.get("to"),
            "data": transaction.get("data", "0x"),
            "value": transaction.get("value", 0),
            "gas": transaction.get("gas", 2_000_000),
        }
        with _cf.ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(self.web3.eth.call, call_params)
            try:
                future.result(timeout=10)
            except _cf.TimeoutError:
                logger.warning(
                    "[ContractInterface] Pre-flight eth_call timed out for %s, proceeding",
                    description,
                )
            except Exception as preflight_err:
                err_str = str(preflight_err)
                if "'0x'" in err_str or err_str.strip().endswith(", '0x')"):
                    logger.warning(
                        "[ContractInterface] Pre-flight returned opaque 0x revert for %s, proceeding",
                        description,
                    )
                else:
                    logger.error(
                        "[ContractInterface] Pre-flight revert for %s: %s",
                        description,
                        preflight_err,
                    )
                    raise RuntimeError(
                        f"Transaction would revert: {preflight_err}"
                    ) from preflight_err

    def _execute_transaction(
        self,
        function: ContractFunction,
        description: str = "transaction",
    ) -> dict[str, Any]:
        """
        Execute a contract transaction: build, sign, send, and wait for receipt.

        Automatically uses paymaster if available, otherwise uses standard Web3 transaction flow.

        Args:
            function: The contract function to execute
            description: Description of the transaction for logging

        Returns:
            dict: Dictionary containing:
                - transactionHash: str - The transaction hash
                - receipt: TransactionReceipt - The transaction receipt
        """
        try:
            wallet_address = self.wallet_provider.address
            gas_estimate = function.estimate_gas({"from": wallet_address})
            logger.debug(f"Gas estimate: {gas_estimate}")
            gas_limit = int(gas_estimate * 1.2)  # Add 20% buffer

            # Use paymaster if available, otherwise use standard Web3
            if self.paymaster:
                # Get nonce from paymaster
                nonce = self.paymaster.eth_getTransactionCount(wallet_address, "pending")
                logger.debug(f"Got nonce from paymaster: {nonce}")

                # Build transaction
                transaction = function.build_transaction(
                    {
                        "from": wallet_address,
                        "chainId": self.web3.eth.chain_id,
                        "nonce": nonce,
                        "gas": gas_limit,
                        "gasPrice": max(self.web3.eth.gas_price, MIN_GAS_PRICE_WEI),
                    }
                )

                logger.debug(f"Building {description} transaction: {transaction}")

                # Pre-flight simulation to surface revert reason before spending gas
                self._run_preflight(transaction, description)

                # Check if transaction is sponsorable
                is_sponsorable = self.paymaster.isSponsorable(transaction)
                if not is_sponsorable:
                    logger.error("Transaction is not sponsorable")
                    raise ValueError("Transaction is not sponsorable")
                else:
                    logger.debug("Transaction is sponsorable")
                    transaction["gasPrice"] = 0

                # Sign transaction via wallet provider
                signed_txn = self.wallet_provider.sign_transaction(transaction)
                signed_tx_hex = signed_txn["rawTransaction"].hex()

                # Send transaction via paymaster
                tx_hash_hex = self.paymaster.eth_sendRawTransaction(
                    signed_tx_hex, tx_options={"UserAgent": "bnbagent/v1.0.0"}
                )
                # Convert hex string to bytes for receipt waiting
                if not tx_hash_hex.startswith("0x"):
                    tx_hash_hex = "0x" + tx_hash_hex
                tx_hash = bytes.fromhex(tx_hash_hex[2:])  # Remove 0x prefix
                logger.debug(f"Transaction sent via paymaster: {tx_hash_hex}")
            else:
                # Standard Web3 path with NonceManager + retry on transient
                # errors. Mirrors core/contract_mixin.py:_send_tx() — keep both
                # in sync.
                nonce_mgr = NonceManager.for_account(self.web3, wallet_address)
                last_error: Exception | None = None
                tx_hash = None
                tx_hash_hex = ""

                for attempt in range(MAX_RETRIES):
                    nonce = nonce_mgr.get_nonce()
                    try:
                        # Floor at MIN_GAS_PRICE_WEI and add 20% headroom so a
                        # low eth_gasPrice on quiet networks doesn't leave the
                        # tx stranded in mempool below the miner cutoff.
                        try:
                            gas_price = max(
                                int(self.web3.eth.gas_price * 1.2),
                                MIN_GAS_PRICE_WEI,
                            )
                        except Exception:
                            gas_price = MIN_GAS_PRICE_WEI

                        transaction = function.build_transaction(
                            {
                                "from": wallet_address,
                                "chainId": self.web3.eth.chain_id,
                                "nonce": nonce,
                                "gasPrice": gas_price,
                                "gas": gas_limit,
                            }
                        )
                        logger.debug(f"Building {description} transaction: {transaction}")

                        self._run_preflight(transaction, description)

                        signed_txn = self.wallet_provider.sign_transaction(transaction)
                        tx_hash = self.web3.eth.send_raw_transaction(
                            signed_txn["rawTransaction"]
                        )
                        tx_hash_hex = tx_hash.hex()
                        if not tx_hash_hex.startswith("0x"):
                            tx_hash_hex = "0x" + tx_hash_hex
                        logger.debug(f"Transaction sent via Web3: {tx_hash_hex}")
                        break
                    except Exception as send_err:
                        last_error = send_err
                        error_str = str(send_err).lower()

                        if nonce_mgr.handle_error(send_err, nonce) and attempt < MAX_RETRIES - 1:
                            logger.warning(
                                f"[ContractInterface] Nonce error, retry "
                                f"{attempt + 1}/{MAX_RETRIES}"
                            )
                            continue

                        is_rate_limit = (
                            "429" in error_str or "too many requests" in error_str
                        )
                        if is_rate_limit and attempt < MAX_RETRIES - 1:
                            delay = RETRY_BASE_DELAY * (2**attempt)
                            logger.warning(
                                f"[ContractInterface] Rate limited, retry "
                                f"{attempt + 1}/{MAX_RETRIES} in {delay:.1f}s"
                            )
                            time.sleep(delay)
                            continue

                        # Non-retryable: invalidate cached nonce so the next
                        # caller re-seeds from chain rather than leaving a gap.
                        nonce_mgr.reset()
                        raise

                if tx_hash is None:
                    # All retries exhausted with retryable errors.
                    raise last_error  # type: ignore[misc]

            # Wait for receipt (always use Web3 for receipt waiting)
            receipt = self.web3.eth.wait_for_transaction_receipt(
                tx_hash, timeout=self.receipt_timeout
            )

            if receipt["status"] == 0:
                logger.error(
                    "[ContractInterface] %s reverted on-chain: tx=%s block=%s gasUsed=%s",
                    description,
                    tx_hash_hex,
                    receipt["blockNumber"],
                    receipt["gasUsed"],
                )
                raise RuntimeError(f"Transaction reverted on-chain: {tx_hash_hex}")

            logger.debug(f"Transaction confirmed: {receipt}")

            return {
                "transactionHash": tx_hash_hex,
                "receipt": receipt,
            }

        except Exception as e:
            logger.error(f"Failed to execute {description}: {str(e)}")
            raise

    def _inject_built_with(
        self, metadata: list[dict[str, str]] | None
    ) -> list[dict[str, str]]:
        from .constants import BUILT_WITH_KEY, get_built_with_value

        items = list(metadata) if metadata else []
        if not any(e.get("key") == BUILT_WITH_KEY for e in items):
            items.append({"key": BUILT_WITH_KEY, "value": get_built_with_value()})
        return items

    def register_agent(
        self,
        agent_uri: str,
        metadata: list[dict[str, str]] | None = None,
    ) -> dict[str, Any]:
        """
        Register a new agent on-chain.

        Args:
            agent_uri: Agent URI for the agent (required)
            metadata: Optional list of metadata entries, each with 'key' (str) and 'value' (bytes)

        Returns:
            dict: Transaction receipt with agentId in the events
        """
        try:
            metadata = self._inject_built_with(metadata)
            # Convert metadata values from string to bytes for on-chain storage
            # Note: ABI uses "metadataKey" and "metadataValue" as field names
            metadata_bytes = [
                {
                    "metadataKey": entry["key"],
                    "metadataValue": entry["value"].encode("utf-8"),
                }
                for entry in metadata
            ]
            logger.debug(
                f"Registering with agentURI and {len(metadata_bytes)} metadata entries"
            )
            function = self.contract.functions.register(agent_uri, metadata_bytes)

            # Log function selector for debugging
            logger.debug(f"Function selector: {function.abi.get('name', 'unknown')}")
            logger.debug(f"Contract address: {self.contract_address}")

            # Execute transaction
            result = self._execute_transaction(function, description="registration")
            tx_hash = result["transactionHash"]
            receipt: TxReceipt = result["receipt"]

            # Extract agentId from events
            agent_id = None
            if receipt.logs:
                # Parse Registered event
                registered_event = self.contract.events.Registered()
                for log in receipt.logs:
                    try:
                        event_data = registered_event.process_log(log)
                        agent_id = event_data["args"]["agentId"]
                        break
                    except Exception:
                        continue

            return {
                "success": True,
                "transactionHash": tx_hash,
                "agentId": agent_id,
                "receipt": receipt,
            }

        except Exception as e:
            logger.error(f"Failed to register agent: {str(e)}")
            raise RuntimeError(f"Agent registration failed: {str(e)}") from e

    def get_agent_info(self, agent_id: int) -> dict[str, Any]:
        """
        Get information about an agent.

        Args:
            agent_id: The agent ID (token ID)

        Returns:
            dict: Agent information including wallet, owner, agentURI
        """
        try:
            logger.debug(f"Fetching agent info for agentId: {agent_id}")

            # Get agent wallet (address associated with the agent)
            agent_wallet = self.contract.functions.getAgentWallet(agent_id).call()

            # Get owner
            owner = self.contract.functions.ownerOf(agent_id).call()

            # Get agent URI (from contract's tokenURI function)
            agent_uri = self.contract.functions.tokenURI(agent_id).call()

            return {
                "agentId": agent_id,
                "agentAddress": agent_wallet,  # agentAddress is an alias for agentWallet
                "agentWallet": agent_wallet,
                "owner": owner,
                "agentURI": agent_uri,
            }

        except Exception as e:
            logger.error(f"Failed to get agent info: {str(e)}")
            raise RuntimeError(f"Failed to get agent info: {str(e)}") from e

    def get_metadata(self, agent_id: int, key: str) -> str:
        """
        Get metadata for an agent.

        Args:
            agent_id: The agent ID
            key: The metadata key

        Returns:
            str: The metadata value (decoded from bytes)
        """
        try:
            logger.debug(f"Getting metadata for agentId={agent_id}, key={key}")

            value_bytes = self.contract.functions.getMetadata(agent_id, key).call()
            # Convert bytes to string
            return value_bytes.decode("utf-8")

        except Exception as e:
            logger.error(f"Failed to get metadata: {str(e)}")
            raise RuntimeError(f"Failed to get metadata: {str(e)}") from e

    def set_metadata(self, agent_id: int, key: str, value: str) -> dict[str, Any]:
        """
        Set metadata for an agent.

        Args:
            agent_id: The agent ID
            key: The metadata key
            value: The metadata value (string, will be encoded to bytes)

        Returns:
            dict: Transaction receipt
        """
        try:
            logger.debug(f"Setting metadata for agentId={agent_id}, key={key}")

            # Convert string to bytes for on-chain storage
            value_bytes = value.encode("utf-8")

            # Execute transaction
            function = self.contract.functions.setMetadata(agent_id, key, value_bytes)
            result = self._execute_transaction(function, description="set metadata")
            tx_hash = result["transactionHash"]
            receipt = result["receipt"]

            return {
                "success": True,
                "transactionHash": tx_hash,
                "receipt": receipt,
            }

        except Exception as e:
            logger.error(f"Failed to set metadata: {str(e)}")
            raise RuntimeError(f"Failed to set metadata: {str(e)}") from e

    def set_agent_uri(self, agent_id: int, agent_uri: str) -> dict[str, Any]:
        """
        Set agent URI for an agent using the setAgentURI function.

        Args:
            agent_id: The agent ID
            agent_uri: The new agent URI

        Returns:
            dict: Transaction receipt

        Note:
            This function uses the setAgentURI() function from the contract,
            which updates the tokenURI directly as per EIP-8004 specification.
        """
        try:
            logger.debug(f"Setting agent URI for agentId={agent_id}: {agent_uri[:50]}...")

            # Execute transaction
            function = self.contract.functions.setAgentURI(agent_id, agent_uri)
            result = self._execute_transaction(function, description="set agent URI")
            tx_hash = result["transactionHash"]
            receipt = result["receipt"]

            logger.debug(f"Agent URI set successfully: {tx_hash}")

            return {
                "success": True,
                "transactionHash": tx_hash,
                "receipt": receipt,
            }

        except Exception as e:
            logger.error(f"Failed to set agent URI: {str(e)}")
            raise RuntimeError(f"Failed to set agent URI: {str(e)}") from e
