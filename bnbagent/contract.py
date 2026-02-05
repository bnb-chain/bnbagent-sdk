"""
Contract Interface Module

Handles interactions with the ERC-8004 Identity Registry smart contract.
Provides methods for registering agents and querying agent information.
"""

import json
from pathlib import Path
from typing import Optional, Dict, Any, List, TYPE_CHECKING
from web3 import Web3
from web3.contract.contract import ContractFunction
from web3.types import TxReceipt
from .utils.logger import get_logger
from .paymaster import Paymaster

if TYPE_CHECKING:
    from .wallets import WalletProvider


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
        wallet_provider: "WalletProvider",
        paymaster: Optional[Paymaster] = None,
        debug: bool = False,
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
        """
        self.web3 = web3
        self.contract_address = Web3.to_checksum_address(contract_address)
        self.wallet_provider = wallet_provider
        self.paymaster = paymaster
        self.debug = debug
        self._logger = get_logger(f"{__name__}.{self.__class__.__name__}", debug=debug)

        # Create contract instance
        self.contract = self.web3.eth.contract(
            address=self.contract_address, abi=self._get_default_abi()
        )

        if self.paymaster:
            self._logger.debug(
                f"Initialized contract interface at {self.contract_address} with paymaster: {self.paymaster.paymaster_url}"
            )
        else:
            self._logger.debug(
                f"Initialized contract interface at {self.contract_address} without paymaster (using standard Web3)"
            )

    def _get_default_abi(self) -> List[Dict[str, Any]]:
        """
        Get the default ERC-8004 Identity Registry ABI from file.

        Returns:
            List of ABI function definitions
        """
        # Get the path to the ABI file relative to this module
        abi_file_path = Path(__file__).parent / "abis" / "IdentityRegistry.json"

        try:
            with open(abi_file_path, "r") as f:
                return json.load(f)
        except Exception as e:
            raise ValueError(f"Failed to load ABI from file {abi_file_path}: {str(e)}")

    def _execute_transaction(
        self,
        function: ContractFunction,
        description: str = "transaction",
    ) -> Dict[str, Any]:
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
            self._logger.debug(f"Gas estimate: {gas_estimate}")
            gas_limit = int(gas_estimate * 1.2)  # Add 20% buffer

            # Use paymaster if available, otherwise use standard Web3
            if self.paymaster:
                # Get nonce from paymaster
                nonce = self.paymaster.eth_getTransactionCount(
                    wallet_address, "pending"
                )
                self._logger.debug(f"Got nonce from paymaster: {nonce}")

                # Build transaction
                transaction = function.build_transaction(
                    {
                        "from": wallet_address,
                        "chainId": self.web3.eth.chain_id,
                        "nonce": nonce,
                        "gas": gas_limit,
                        "gasPrice": self.web3.eth.gas_price,
                    }
                )

                self._logger.debug(f"Building {description} transaction: {transaction}")

                # Check if transaction is sponsorable
                is_sponsorable = self.paymaster.isSponsorable(transaction)
                if not is_sponsorable:
                    self._logger.error(f"Transaction is not sponsorable")
                    raise ValueError("Transaction is not sponsorable")
                else:
                    self._logger.debug(f"Transaction is sponsorable")
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
                self._logger.debug(f"Transaction sent via paymaster: {tx_hash_hex}")
            else:
                # Use standard Web3 transaction flow (no paymaster)
                # Get nonce from Web3
                nonce = self.web3.eth.get_transaction_count(
                    wallet_address, "pending"
                )
                self._logger.debug(f"Got nonce from Web3: {nonce}")

                # Get gas price from network
                gas_price = self.web3.eth.gas_price
                self._logger.debug(f"Gas price: {gas_price}")

                # Build transaction
                transaction = function.build_transaction(
                    {
                        "from": wallet_address,
                        "chainId": self.web3.eth.chain_id,
                        "nonce": nonce,
                        "gasPrice": gas_price,
                        "gas": gas_limit,
                    }
                )

                self._logger.debug(f"Building {description} transaction: {transaction}")

                # Sign transaction via wallet provider
                signed_txn = self.wallet_provider.sign_transaction(transaction)
                signed_tx_hex = signed_txn["rawTransaction"].hex()

                # Send transaction via Web3
                tx_hash = self.web3.eth.send_raw_transaction(signed_tx_hex)
                tx_hash_hex = tx_hash.hex()
                # Ensure 0x prefix (defensive programming, though Web3 usually includes it)
                if not tx_hash_hex.startswith("0x"):
                    tx_hash_hex = "0x" + tx_hash_hex
                self._logger.debug(f"Transaction sent via Web3: {tx_hash_hex}")

            # Wait for receipt (always use Web3 for receipt waiting)
            receipt = self.web3.eth.wait_for_transaction_receipt(tx_hash)

            self._logger.debug(f"Transaction confirmed: {receipt}")

            return {
                "transactionHash": tx_hash_hex,
                "receipt": receipt,
            }

        except Exception as e:
            self._logger.error(f"Failed to execute {description}: {str(e)}")
            raise

    def register_agent(
        self,
        agent_uri: str,
        metadata: Optional[List[Dict[str, str]]] = None,
    ) -> Dict[str, Any]:
        """
        Register a new agent on-chain.

        Args:
            agent_uri: Agent URI for the agent (required)
            metadata: Optional list of metadata entries, each with 'key' (str) and 'value' (bytes)

        Returns:
            dict: Transaction receipt with agentId in the events
        """
        try:
            # Build transaction based on parameters
            if metadata:
                # Convert metadata values from string to bytes for on-chain storage
                # Note: ABI uses "metadataKey" and "metadataValue" as field names
                metadata_bytes = [
                    {
                        "metadataKey": entry["key"],
                        "metadataValue": entry["value"].encode("utf-8"),
                    }
                    for entry in metadata
                ]
                self._logger.debug(
                    f"Registering with agentURI and {len(metadata_bytes)} metadata entries"
                )
                # Register with agentURI and metadata
                function = self.contract.functions.register(agent_uri, metadata_bytes)
            else:
                self._logger.debug(
                    f"Registering with agentURI only: {agent_uri[:50]}..."
                )
                # Register with agentURI only
                function = self.contract.functions.register(agent_uri)

            # Log function selector for debugging
            self._logger.debug(
                f"Function selector: {function.abi.get('name', 'unknown')}"
            )
            self._logger.debug(f"Contract address: {self.contract_address}")

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
                    except:
                        continue

            return {
                "success": True,
                "transactionHash": tx_hash,
                "agentId": agent_id,
                "receipt": receipt,
            }

        except Exception as e:
            self._logger.error(f"Failed to register agent: {str(e)}")
            raise RuntimeError(f"Agent registration failed: {str(e)}")

    def get_agent_info(self, agent_id: int) -> Dict[str, Any]:
        """
        Get information about an agent.

        Args:
            agent_id: The agent ID (token ID)

        Returns:
            dict: Agent information including wallet, owner, agentURI
        """
        try:
            self._logger.debug(f"Fetching agent info for agentId: {agent_id}")

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
            self._logger.error(f"Failed to get agent info: {str(e)}")
            raise RuntimeError(f"Failed to get agent info: {str(e)}")

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
            self._logger.debug(f"Getting metadata for agentId={agent_id}, key={key}")

            value_bytes = self.contract.functions.getMetadata(agent_id, key).call()
            # Convert bytes to string
            return value_bytes.decode("utf-8")

        except Exception as e:
            self._logger.error(f"Failed to get metadata: {str(e)}")
            raise RuntimeError(f"Failed to get metadata: {str(e)}")

    def set_metadata(self, agent_id: int, key: str, value: str) -> Dict[str, Any]:
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
            self._logger.debug(f"Setting metadata for agentId={agent_id}, key={key}")

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
            self._logger.error(f"Failed to set metadata: {str(e)}")
            raise RuntimeError(f"Failed to set metadata: {str(e)}")

    def set_agent_uri(self, agent_id: int, agent_uri: str) -> Dict[str, Any]:
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
            self._logger.debug(
                f"Setting agent URI for agentId={agent_id}: {agent_uri[:50]}..."
            )

            # Execute transaction
            function = self.contract.functions.setAgentURI(agent_id, agent_uri)
            result = self._execute_transaction(function, description="set agent URI")
            tx_hash = result["transactionHash"]
            receipt = result["receipt"]

            self._logger.debug(f"Agent URI set successfully: {tx_hash}")

            return {
                "success": True,
                "transactionHash": tx_hash,
                "receipt": receipt,
            }

        except Exception as e:
            self._logger.error(f"Failed to set agent URI: {str(e)}")
            raise RuntimeError(f"Failed to set agent URI: {str(e)}")
