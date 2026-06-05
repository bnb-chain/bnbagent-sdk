"""
Contract Interface Module

Handles interactions with the ERC-8004 Identity Registry smart contract.
Provides methods for registering agents and querying agent information.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

from web3 import Web3
from web3.contract.contract import ContractFunction

from ..core.paymaster import Paymaster
from ..wallets.intents import (
    ERC8004_REGISTER,
    ERC8004_SET_AGENT_URI,
    ERC8004_SET_METADATA,
    ExecutionContext,
    Intent,
)
from ..wallets.local_executor import DEFAULT_RECEIPT_TIMEOUT

if TYPE_CHECKING:
    from ..wallets import WalletProvider

logger = logging.getLogger(__name__)


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

        # Execution backend. Each wallet decides how it executes intents:
        # a pure signer wraps itself in a LocalExecutor (build + sign +
        # broadcast via this web3/paymaster context); a self-broadcasting
        # wallet returns itself. No wallet-kind branching needed here.
        self._executor = self.wallet_provider.make_executor(
            ExecutionContext(
                web3=self.web3,
                paymaster=self.paymaster,
                receipt_timeout=self.receipt_timeout,
            )
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

    def _execute_transaction(
        self,
        function: ContractFunction,
        description: str = "transaction",
    ) -> dict[str, Any]:
        """
        Execute a contract transaction: build, sign, send, and wait for receipt.

        Delegates to the configured :class:`IntentExecutor` (by default a
        :class:`~bnbagent.wallets.local_executor.LocalExecutor`, which uses the
        paymaster when available and otherwise the standard Web3 flow with
        nonce management and retry). Retained as the internal seam so the
        higher-level ``register_agent`` / ``set_*`` methods stay unchanged.

        Args:
            function: The contract function to execute
            description: Description of the transaction for logging

        Returns:
            dict: Dictionary containing:
                - transactionHash: str - The transaction hash
                - receipt: TransactionReceipt - The transaction receipt
        """
        return self._executor.execute(Intent(call=function, description=description))

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

            # Execute via the configured backend. ``call`` drives the local
            # build/sign/broadcast path; ``name``/``kwargs`` let a semantic
            # backend (e.g. a CLI-backed wallet) rebuild the operation. The
            # high-level ``metadata`` (key/value strings, including the
            # injected ``built_with``) is passed so such backends can replay
            # entries their native ``register`` cannot carry inline.
            intent = Intent(
                name=ERC8004_REGISTER,
                kwargs={"agent_uri": agent_uri, "metadata": metadata},
                call=function,
                description="registration",
            )
            result = self._executor.execute(intent)
            tx_hash = result["transactionHash"]
            receipt = result.get("receipt")

            # Prefer an agentId surfaced directly by the executor (semantic
            # backends return it from their own output); otherwise parse the
            # Registered event from the local receipt.
            agent_id = result.get("agentId")
            if agent_id is None and getattr(receipt, "logs", None):
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

            function = self.contract.functions.setMetadata(agent_id, key, value_bytes)
            intent = Intent(
                name=ERC8004_SET_METADATA,
                kwargs={"agent_id": agent_id, "key": key, "value": value},
                call=function,
                description="set metadata",
            )
            result = self._executor.execute(intent)

            return {
                "success": True,
                "transactionHash": result["transactionHash"],
                "receipt": result.get("receipt"),
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

            function = self.contract.functions.setAgentURI(agent_id, agent_uri)
            intent = Intent(
                name=ERC8004_SET_AGENT_URI,
                kwargs={"agent_id": agent_id, "agent_uri": agent_uri},
                call=function,
                description="set agent URI",
            )
            result = self._executor.execute(intent)
            tx_hash = result["transactionHash"]

            logger.debug(f"Agent URI set successfully: {tx_hash}")

            return {
                "success": True,
                "transactionHash": tx_hash,
                "receipt": result.get("receipt"),
            }

        except Exception as e:
            logger.error(f"Failed to set agent URI: {str(e)}")
            raise RuntimeError(f"Failed to set agent URI: {str(e)}") from e
