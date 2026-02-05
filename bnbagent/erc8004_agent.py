"""
ERC8004Agent SDK - Main SDK Class

Provides a high-level interface for on-chain agent registration and management.
Handles wallet management, contract interactions, and provides convenient methods
for common operations.
"""

from typing import Optional, Dict, Any, List
from web3 import Web3
import requests
from .utils.logger import get_logger
from .utils.agent_uri import AgentURIGenerator
from .utils.state_file import StateFileManager
from .wallets import WalletProvider
from .contract import ContractInterface
from .models import AgentEndpoint
from .constants import TESTNET_CONFIG, SCAN_API_URL
from .paymaster import Paymaster


class ERC8004Agent:
    """
    Main SDK class for ERC-8004 on-chain agent operations.

    Features:
    - Supports multiple wallet types via WalletProvider interface
    - Agent registration and information retrieval
    - Debug mode for detailed logging
    - Extensible design for EVM, MPC, and other wallet types
    """

    def __init__(
        self,
        wallet_provider: WalletProvider,
        network: str = "bsc-testnet",
        debug: bool = False,
    ):
        """
        Initialize the ERC8004Agent SDK.

        Args:
            wallet_provider: Wallet provider instance (required).
                            Use EVMWalletProvider for private key wallets,
                            or MPCWalletProvider for MPC wallets.
            network: Network name. Currently only "bsc-testnet" is supported.
            debug: Enable debug logging (default: False)

        Raises:
            ValueError: If wallet_provider is not provided or network is invalid.

        Example:
            >>> from bnbagent import ERC8004Agent, EVMWalletProvider
            >>>
            >>> # Create wallet provider
            >>> wallet = EVMWalletProvider(password="your-secure-password")
            >>>
            >>> # Create SDK with wallet provider
            >>> sdk = ERC8004Agent(
            ...     wallet_provider=wallet,
            ...     network="bsc-testnet",
            ...     debug=True
            ... )
        """
        if wallet_provider is None:
            raise ValueError(
                "wallet_provider is required. "
                "Use EVMWalletProvider(password='...') for private key wallets."
            )

        self.debug = debug
        self._logger = get_logger(f"{__name__}.{self.__class__.__name__}", debug=debug)

        self._logger.debug("Initializing ERC8004Agent SDK...")

        # Handle network configuration
        if network == "bsc-testnet":
            self._network_config = TESTNET_CONFIG.copy()
        else:
            raise ValueError(
                f"Unknown network '{network}'. Currently only 'bsc-testnet' is supported."
            )

        rpc_url = self._network_config.get("rpc_url")
        network_name = self._network_config.get("name")
        contract_address = self._network_config.get("registry_contract")

        if not contract_address:
            raise ValueError(f"registry_contract not found in {network_name} config")

        self._logger.debug(f"Using network: {network_name} ({rpc_url})")
        self._logger.debug(f"Contract address: {contract_address}")

        # Initialize Web3 connection
        self.web3 = Web3(Web3.HTTPProvider(rpc_url))

        # Initialize state file manager
        self.state_manager = StateFileManager(debug=debug)

        if not self.web3.is_connected():
            raise ConnectionError(f"Failed to connect to RPC: {rpc_url}")

        self._logger.debug(f"Connected to blockchain: {rpc_url}")

        # Use provided wallet provider
        self.wallet_provider = wallet_provider
        self._logger.debug(f"Using wallet provider: {type(wallet_provider).__name__}")
        self._logger.debug(f"Wallet address: {self.wallet_provider.address}")

        # Initialize paymaster (optional, not required for local network)
        paymaster = None
        use_paymaster = self._network_config.get("paymaster", False)
        if use_paymaster:
            paymaster_url = self._network_config.get("paymaster_url")
            if not paymaster_url:
                raise ValueError(
                    f"paymaster_url not found in {network_name} config. Paymaster is required for this network."
                )
            paymaster = Paymaster(paymaster_url=paymaster_url, debug=debug)
            self._logger.debug(f"Initialized paymaster: {paymaster_url}")
        else:
            self._logger.debug("Paymaster not used for local network")

        # Initialize contract interface (uses default ABI)
        # Note: paymaster can be None for local network
        self.contract = ContractInterface(
            web3=self.web3,
            contract_address=contract_address,
            wallet_provider=self.wallet_provider,
            paymaster=paymaster,
            debug=debug,
        )

        self._logger.debug("SDK initialized successfully")

    def generate_agent_uri(
        self,
        name: str,
        description: str,
        endpoints: List[AgentEndpoint],
        image: Optional[str] = None,
        agent_id: Optional[int] = None,
        supported_trust: Optional[List[str]] = None,
    ) -> str:
        """
        Generate agent URI for agent registration.

        Creates an EIP-8004 compliant agent registration file and returns a base64 data URI.
        To avoid re-registering, check local state with get_local_agent_info(name);
        if not None, the name is in local state and you have the stored info.

        Args:
            name: Agent name (required)
            description: Agent description (required)
            endpoints: List of AgentEndpoint instances (required, at least one)
            image: Optional agent image URL
            agent_id: Optional agent ID for registrations field
            supported_trust: Optional list of supported trust mechanisms

        Returns:
            str: The generated base64 data URI

        Raises:
            ValueError: If endpoints is empty or None

        Example:
            >>> from bnbagent import AgentEndpoint
            >>> agent_uri = sdk.generate_agent_uri(
            ...     name="My Agent",
            ...     description="A test agent",
            ...     image="https://example.com/image.png",
            ...     endpoints=[
            ...         AgentEndpoint(
            ...             name="A2A",
            ...             endpoint="https://agent.example/.well-known/agent-card.json",
            ...             version="0.3.0"
            ...         )
            ...     ]
            ... )
            >>> print(f"Agent URI: {agent_uri}")
        """
        if not endpoints or len(endpoints) == 0:
            raise ValueError(
                "endpoints is required and must contain at least one endpoint"
            )

        self._logger.debug("Generating agent URI...")

        # Get chain ID from network config
        chain_id = self._network_config.get("chain_id")
        if chain_id is None:
            # Try to get chain ID from Web3
            try:
                chain_id = self.web3.eth.chain_id
            except Exception:
                self._logger.warning("Could not determine chain ID")

        # Get contract address for registrations field
        identity_registry = self.contract.contract_address

        agent_uri = AgentURIGenerator.generate_agent_uri(
            name=name,
            description=description,
            image=image,
            endpoints=endpoints,
            agent_id=agent_id,
            identity_registry=identity_registry,
            chain_id=chain_id,
            supported_trust=supported_trust,
        )

        self._logger.debug(f"Agent URI generated: {agent_uri}")

        return agent_uri

    def get_local_agent_info(self, name: str) -> Optional[Dict[str, Any]]:
        """
        Get agent info from local state file by name.

        This only reads the local .bnbagent_state file; it does not check on-chain data.
        Use it to see if a name is present in your local state and to get stored
        agent_uri / agent_id (e.g. to compare URI and call set_agent_uri if needed).

        Args:
            name: The agent name to look up in local state

        Returns:
            Optional[Dict[str, Any]]: If the name exists in local state, dict with
                'name', 'agent_uri', 'agent_id', 'transaction_hash'; otherwise None.
            To check "is in local state?" use: get_local_agent_info(name) is not None.

        Example:
            >>> info = sdk.get_local_agent_info("My Agent")
            >>> if info:
            ...     new_uri = sdk.generate_agent_uri(...)
            ...     if info["agent_uri"] != new_uri:
            ...         sdk.set_agent_uri(info["agent_id"], new_uri)
            ... else:
            ...     sdk.register_agent(agent_uri=sdk.generate_agent_uri(...))
        """
        if not name:
            return None

        try:
            registered_agents = self.state_manager.get("registered_agents", [])
            if not isinstance(registered_agents, list):
                return None

            for agent in registered_agents:
                if isinstance(agent, dict) and agent.get("name") == name:
                    return agent
            return None
        except Exception:
            return None

    def _update_agent_uri_in_state(self, agent_id: int, agent_uri: str) -> None:
        """
        Update agent_uri for an agent in state file by agent_id.
        No-op if state file does not exist or agent_id is not in registered_agents.
        """
        try:
            if not self.state_manager.exists():
                return
            registered_agents = self.state_manager.get("registered_agents", [])
            if not isinstance(registered_agents, list):
                return
            for i, agent in enumerate(registered_agents):
                if not isinstance(agent, dict):
                    continue
                # Compare agent_id (may be int from chain or from JSON)
                aid = agent.get("agent_id")
                if aid is None:
                    continue
                if int(aid) == int(agent_id):
                    registered_agents[i] = {**agent, "agent_uri": agent_uri}
                    self.state_manager.set("registered_agents", registered_agents)
                    self._logger.debug(
                        f"Updated agent_uri for agentId={agent_id} in state file"
                    )
                    return
        except Exception as e:
            self._logger.warning(f"Failed to update agent_uri in state file: {str(e)}")

    def register_agent(
        self,
        agent_uri: str,
        metadata: Optional[List[Dict[str, str]]] = None,
    ) -> Dict[str, Any]:
        """
        Register a new agent on-chain.

        To avoid duplicate names in local state, check get_local_agent_info(name)
        first; if not None, the name is already in your local state.

        Args:
            agent_uri: Agent URI string (required). Use generate_agent_uri() to generate one.
            metadata: Optional list of metadata entries. Each entry should be:
                {'key': str, 'value': str}

        Returns:
            dict: Registration result with:
                - success: bool
                - transactionHash: str
                - agentId: int (the registered agent ID)
                - receipt: TransactionReceipt
                - agentURI: str (the agent URI used)

        Example:
            >>> # First, generate agent URI
            >>> from bnbagent import AgentEndpoint
            >>> agent_uri = sdk.generate_agent_uri(
            ...     name="My Agent",
            ...     description="A test agent",
            ...     endpoints=[AgentEndpoint(name="A2A", endpoint="https://...")]
            ... )
            >>>
            >>> # Then, register with the generated URI
            >>> result = sdk.register_agent(agent_uri=agent_uri)
        """
        if not agent_uri:
            raise ValueError("agent_uri is required")

        self._logger.debug("Registering agent on-chain...")

        # Parse agent URI to get name
        agent_data = self.parse_agent_uri(agent_uri)
        if not agent_data:
            raise ValueError("Failed to parse agent URI")

        agent_name = agent_data.get("name")
        if not agent_name:
            raise ValueError("Agent URI does not contain a name field")

        try:
            result = self.contract.register_agent(
                agent_uri=agent_uri, metadata=metadata
            )

            # Get the assigned agentId
            agent_id = result.get("agentId")

            # Regenerate agent URI with agentId and agentRegistry in registrations field
            final_agent_uri = agent_uri
            if agent_id is not None:
                try:
                    # Rebuild endpoints from parsed data
                    endpoints = []
                    for svc in agent_data.get("services", []):
                        endpoints.append(
                            AgentEndpoint(
                                name=svc.get("name", ""),
                                endpoint=svc.get("endpoint", ""),
                                version=svc.get("version"),
                            )
                        )

                    if endpoints:
                        # Regenerate URI with agentId included
                        final_agent_uri = self.generate_agent_uri(
                            name=agent_data.get("name", ""),
                            description=agent_data.get("description", ""),
                            image=agent_data.get("image"),
                            endpoints=endpoints,
                            agent_id=agent_id,
                            supported_trust=agent_data.get("supportedTrust")
                            or agent_data.get("supportedTrusts"),
                        )

                        # Update on-chain URI with registrations field populated
                        self._logger.debug(
                            f"Updating agent URI with registrations for agentId={agent_id}"
                        )
                        self.contract.set_agent_uri(agent_id, final_agent_uri)
                        self._logger.info(
                            f"Updated agent URI with registrations (agentId={agent_id})"
                        )
                except Exception as e:
                    self._logger.warning(
                        f"Failed to update agent URI with registrations: {str(e)}. "
                        "The agent is registered but registrations field may be empty."
                    )

            # Add final agentURI to result
            result["agentURI"] = final_agent_uri

            # Save registered agent to state file (add to list)
            try:
                registered_agents = self.state_manager.get("registered_agents", [])
                if not isinstance(registered_agents, list):
                    registered_agents = []

                # Check if agent already exists in list (shouldn't happen, but be safe)
                agent_exists = False
                for i, agent in enumerate(registered_agents):
                    if agent.get("name") == agent_name:
                        # Update existing agent info
                        registered_agents[i] = {
                            "name": agent_name,
                            "agent_uri": final_agent_uri,
                            "agent_id": result.get("agentId"),
                            "transaction_hash": result.get("transactionHash"),
                        }
                        agent_exists = True
                        break

                if not agent_exists:
                    # Add new agent info
                    registered_agents.append(
                        {
                            "name": agent_name,
                            "agent_uri": final_agent_uri,
                            "agent_id": result.get("agentId"),
                            "transaction_hash": result.get("transactionHash"),
                        }
                    )

                self.state_manager.set("registered_agents", registered_agents)
                self._logger.debug(
                    f"Saved registered agent '{agent_name}' (agentId={result.get('agentId')}) to state file"
                )
            except Exception as e:
                self._logger.warning(
                    f"Failed to save registered agent to state file: {str(e)}"
                )

            self._logger.info(
                f"Agent registered successfully: "
                f"agentId={result['agentId']}, "
                f"txHash={result['transactionHash']}"
            )

            return result

        except Exception as e:
            self._logger.error(f"Agent registration failed: {str(e)}")
            raise

    def get_agent_info(self, agent_id: int) -> Dict[str, Any]:
        """
        Get information about a registered agent.

        Args:
            agent_id: The agent ID (token ID) to query

        Returns:
            dict: Agent information with:
                - agentId: int
                - agentAddress: str (deterministic agent address)
                - owner: str (owner address)
                - agentURI: str

        Example:
            >>> info = sdk.get_agent_info(agent_id=1)
            >>> print(f"Agent owner: {info['owner']}")
            >>> print(f"Agent URI: {info['agentURI']}")
        """
        self._logger.debug(f"Fetching agent info for agentId: {agent_id}")

        try:
            info = self.contract.get_agent_info(agent_id)

            self._logger.debug(f"Agent info retrieved: {info}")

            return info

        except Exception as e:
            self._logger.error(f"Failed to get agent info: {str(e)}")
            raise

    def get_all_agents(
        self,
        limit: int = 10,
        offset: int = 0,
    ) -> Dict[str, Any]:
        """
        List all registered agents.

        This method queries the indexer API to discover registered agents.
        It does not require on-chain calls.

        Args:
            limit: Maximum number of agents to return (default: 10, max: 100)
            offset: Number of agents to skip for pagination (default: 0)

        Returns:
            dict: Response containing:
                - items: List of agent objects with fields like:
                    - token_id: Agent ID
                    - name: Agent name
                    - description: Agent description
                    - owner_address: Owner wallet address
                    - services: Dict of service endpoints
                    - total_score: Reputation score
                - total: Total number of agents matching query
                - limit: Limit used in query
                - offset: Offset used in query

        Raises:
            ConnectionError: If API request fails

        Example:
            >>> # List first 10 agents
            >>> agents = sdk.get_all_agents(limit=10)
            >>> for agent in agents['items']:
            ...     print(f"Agent #{agent['token_id']}: {agent['name']}")

            >>> # Paginate through results
            >>> page1 = sdk.get_all_agents(limit=10, offset=0)
            >>> page2 = sdk.get_all_agents(limit=10, offset=10)
        """
        chain_id = self._network_config.get("chain_id")

        self._logger.debug(
            f"Fetching agents: chain_id={chain_id}, limit={limit}, offset={offset}"
        )

        # Build query parameters
        params = {
            "chain_id": chain_id,
            "limit": min(limit, 100),  # Cap at 100
            "offset": offset,
        }

        try:
            response = requests.get(
                f"{SCAN_API_URL}/agents",
                params=params,
                timeout=30,
            )
            response.raise_for_status()

            data = response.json()
            self._logger.debug(
                f"Retrieved {len(data.get('items', []))} agents "
                f"(total: {data.get('total', 0)})"
            )

            return data

        except requests.exceptions.RequestException as e:
            self._logger.error(f"Failed to fetch agents from 8004scan: {str(e)}")
            raise ConnectionError(f"8004scan API request failed: {str(e)}") from e

    def get_metadata(self, agent_id: int, key: str) -> str:
        """
        Get metadata value for an agent.

        Args:
            agent_id: The agent ID
            key: The metadata key

        Returns:
            str: The metadata value (automatically decoded from bytes)

        Example:
            >>> value = sdk.get_metadata(agent_id=1, key="description")
            >>> print(value)
        """
        self._logger.debug(f"Getting metadata for agentId={agent_id}, key={key}")

        try:
            return self.contract.get_metadata(agent_id, key)
        except Exception as e:
            self._logger.error(f"Failed to get metadata: {str(e)}")
            raise

    def set_metadata(self, agent_id: int, key: str, value: str) -> Dict[str, Any]:
        """
        Set metadata for an agent (must be owner or operator).

        Args:
            agent_id: The agent ID
            key: The metadata key
            value: The metadata value (string, will be automatically encoded to bytes)

        Returns:
            dict: Transaction result with:
                - success: bool
                - transactionHash: str
                - receipt: TransactionReceipt

        Example:
            >>> result = sdk.set_metadata(
            ...     agent_id=1,
            ...     key="description",
            ...     value="My agent description"
            ... )
        """
        self._logger.debug(f"Setting metadata for agentId={agent_id}, key={key}")

        try:
            return self.contract.set_metadata(agent_id, key, value)
        except Exception as e:
            self._logger.error(f"Failed to set metadata: {str(e)}")
            raise

    def set_agent_uri(
        self,
        agent_id: int,
        agent_uri: str,
    ) -> Dict[str, Any]:
        """
        Set agent URI for an agent.

        Args:
            agent_id: The agent ID to update
            agent_uri: New agent URI string (required). Use generate_agent_uri() to generate one.

        Returns:
            dict: Transaction result with:
                - success: bool
                - transactionHash: str
                - receipt: TransactionReceipt
                - agentURI: str (the agent URI used)

        Example:
            >>> # First, generate new agent URI
            >>> from bnbagent import AgentEndpoint
            >>> agent_uri = sdk.generate_agent_uri(
            ...     name="Updated Agent",
            ...     description="Updated description",
            ...     endpoints=[AgentEndpoint(name="A2A", endpoint="https://...")]
            ... )
            >>>
            >>> # Then, set with the generated URI
            >>> result = sdk.set_agent_uri(agent_id=1, agent_uri=agent_uri)
        """
        if not agent_uri:
            raise ValueError("agent_uri is required")

        self._logger.debug(f"Setting agent URI for agentId: {agent_id}")

        try:
            # Set agent URI using setAgentURI function
            result = self.contract.set_agent_uri(agent_id, agent_uri)
            result["agentURI"] = agent_uri

            # Update state file so stored agent_uri stays in sync with on-chain
            self._update_agent_uri_in_state(agent_id, agent_uri)

            return result

        except Exception as e:
            self._logger.error(f"Failed to set agent URI: {str(e)}")
            raise

    @staticmethod
    def parse_agent_uri(agent_uri: str) -> Optional[Dict[str, Any]]:
        """
        Parse agent URI to JSON.

        Supports multiple URI formats:
        - Base64 data URI: `data:application/json;base64,...` - decodes and parses
        - HTTP/HTTPS URL: `http://...` or `https://...` - fetches and parses JSON

        Args:
            agent_uri: The agent URI string

        Returns:
            dict: Parsed JSON dictionary, or None if parsing fails or URI format is not supported

        Example:
            >>> info = sdk.get_agent_info(agent_id=1)
            >>> agent_data = sdk.parse_agent_uri(info['agentURI'])
            >>> if agent_data:
            ...     print(f"Agent name: {agent_data['name']}")
            ...     print(f"Agent description: {agent_data['description']}")
        """
        if not agent_uri:
            return None

        # Handle base64 data URI
        if agent_uri.startswith("data:application/json;base64,"):
            try:
                return AgentURIGenerator.decode_registration_file_from_base64(agent_uri)
            except Exception:
                return None

        # Handle HTTP/HTTPS URL
        if agent_uri.startswith("http://") or agent_uri.startswith("https://"):
            try:
                response = requests.get(agent_uri, timeout=10)
                response.raise_for_status()
                return response.json()
            except Exception:
                return None

        # Unsupported format
        return None

    @property
    def wallet_address(self) -> str:
        """
        Get the wallet address.

        Returns:
            str: The Ethereum address of the wallet
        """
        return self.wallet_provider.address

    @property
    def contract_address(self) -> str:
        """
        Get the contract address.

        Returns:
            str: The ERC-8004 Identity Registry contract address
        """
        return self.contract.contract_address

    @property
    def network(self) -> Dict[str, Any]:
        """
        Get the network configuration.

        Returns:
            Dict[str, Any]: The network configuration dictionary
        """
        return self._network_config
