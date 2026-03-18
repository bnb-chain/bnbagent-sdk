"""
ERC8004Agent SDK - Main SDK Class

Provides a high-level interface for on-chain agent registration and management.
Handles wallet management, contract interactions, and provides convenient methods
for common operations.
"""

from __future__ import annotations

import concurrent.futures
import ipaddress
import logging
import socket
from typing import Any
from urllib.parse import urlparse

import requests
from web3 import Web3

from ..constants import SCAN_API_URL
from ..core.paymaster import Paymaster
from ..utils.agent_uri import AgentURIGenerator
from ..wallets import WalletProvider
from .constants import get_erc8004_config
from .contract import ContractInterface
from .models import AgentEndpoint

logger = logging.getLogger(__name__)


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

        logger.debug("Initializing ERC8004Agent SDK...")

        # Handle network configuration
        self._network_config = get_erc8004_config(network)

        rpc_url = self._network_config.get("rpc_url")
        network_name = self._network_config.get("name")
        contract_address = self._network_config.get("registry_contract")

        if not contract_address:
            raise ValueError(f"registry_contract not found in {network_name} config")

        logger.debug(f"Using network: {network_name} ({rpc_url})")
        logger.debug(f"Contract address: {contract_address}")

        # Initialize Web3 connection
        self.web3 = Web3(Web3.HTTPProvider(rpc_url))

        if not self.web3.is_connected():
            raise ConnectionError(f"Failed to connect to RPC: {rpc_url}")

        logger.debug(f"Connected to blockchain: {rpc_url}")

        # Use provided wallet provider
        self.wallet_provider = wallet_provider
        logger.debug(f"Using wallet provider: {type(wallet_provider).__name__}")
        logger.debug(f"Wallet address: {self.wallet_provider.address}")

        # Initialize paymaster (optional, not required for local network)
        paymaster = None
        use_paymaster = self._network_config.get("paymaster", False)
        if use_paymaster:
            paymaster_url = self._network_config.get("paymaster_url")
            if not paymaster_url:
                raise ValueError(
                    f"paymaster_url not found in {network_name}"
                    " config. Paymaster is required for"
                    " this network."
                )
            paymaster = Paymaster(paymaster_url=paymaster_url, debug=debug)
            logger.debug(f"Initialized paymaster: {paymaster_url}")
        else:
            logger.debug("Paymaster not used for local network")

        # Initialize contract interface (uses default ABI)
        # Note: paymaster can be None for local network
        self.contract = ContractInterface(
            web3=self.web3,
            contract_address=contract_address,
            wallet_provider=self.wallet_provider,
            paymaster=paymaster,
            debug=debug,
        )

        logger.debug("SDK initialized successfully")

    def generate_agent_uri(
        self,
        name: str,
        description: str,
        endpoints: list[AgentEndpoint],
        image: str | None = None,
        agent_id: int | None = None,
        supported_trust: list[str] | None = None,
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
            raise ValueError("endpoints is required and must contain at least one endpoint")

        logger.debug("Generating agent URI...")

        # Get chain ID from network config
        chain_id = self._network_config.get("chain_id")
        if chain_id is None:
            # Try to get chain ID from Web3
            try:
                chain_id = self.web3.eth.chain_id
            except Exception:
                logger.warning("Could not determine chain ID")

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

        logger.debug(f"Agent URI generated: {agent_uri}")

        return agent_uri

    def get_local_agent_info(self, name: str) -> dict[str, Any] | None:
        """
        Find an agent registered by this wallet, by name.

        Queries the on-chain registry (via indexer API) and returns the first
        agent whose name matches and whose owner is this wallet's address.

        Args:
            name: The agent name to look up

        Returns:
            Dict with 'name', 'agent_id', 'agent_uri', 'owner_address'
            if found, otherwise None.
        """
        if not name:
            return None

        try:
            my_address = self.wallet_address.lower()
            result = self.get_all_agents(limit=100, offset=0)
            for agent in result.get("items", []):
                if (
                    agent.get("owner_address", "").lower() == my_address
                    and agent.get("name", "").lower() == name.lower()
                ):
                    return {
                        "name": agent.get("name"),
                        "agent_id": agent.get("token_id"),
                        "agent_uri": agent.get("agent_uri", ""),
                        "owner_address": agent.get("owner_address"),
                    }
            return None
        except Exception:
            return None

    def register_agent(
        self,
        agent_uri: str,
        metadata: list[dict[str, str]] | None = None,
    ) -> dict[str, Any]:
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

        logger.debug("Registering agent on-chain...")

        # Parse agent URI to get name
        agent_data = self.parse_agent_uri(agent_uri)
        if not agent_data:
            raise ValueError("Failed to parse agent URI")

        agent_name = agent_data.get("name")
        if not agent_name:
            raise ValueError("Agent URI does not contain a name field")

        try:
            result = self.contract.register_agent(agent_uri=agent_uri, metadata=metadata)

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
                        logger.debug(
                            f"Updating agent URI with registrations for agentId={agent_id}"
                        )
                        self.contract.set_agent_uri(agent_id, final_agent_uri)
                        logger.info(f"Updated agent URI with registrations (agentId={agent_id})")
                except Exception as e:
                    logger.warning(
                        f"Failed to update agent URI with registrations: {str(e)}. "
                        "The agent is registered but registrations field may be empty."
                    )

            # Add final agentURI to result
            result["agentURI"] = final_agent_uri

            logger.info(
                f"Agent registered successfully: "
                f"agentId={result['agentId']}, "
                f"txHash={result['transactionHash']}"
            )

            return result

        except Exception as e:
            logger.error(f"Agent registration failed: {str(e)}")
            raise

    def get_agent_info(self, agent_id: int) -> dict[str, Any]:
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
        logger.debug(f"Fetching agent info for agentId: {agent_id}")

        try:
            info = self.contract.get_agent_info(agent_id)

            logger.debug(f"Agent info retrieved: {info}")

            return info

        except Exception as e:
            logger.error(f"Failed to get agent info: {str(e)}")
            raise

    def get_all_agents(
        self,
        limit: int = 10,
        offset: int = 0,
    ) -> dict[str, Any]:
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

        logger.debug(f"Fetching agents: chain_id={chain_id}, limit={limit}, offset={offset}")

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
            logger.debug(
                f"Retrieved {len(data.get('items', []))} agents (total: {data.get('total', 0)})"
            )

            return data

        except requests.exceptions.RequestException as e:
            logger.error(f"Failed to fetch agents from 8004scan: {str(e)}")
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
        logger.debug(f"Getting metadata for agentId={agent_id}, key={key}")

        try:
            return self.contract.get_metadata(agent_id, key)
        except Exception as e:
            logger.error(f"Failed to get metadata: {str(e)}")
            raise

    def set_metadata(self, agent_id: int, key: str, value: str) -> dict[str, Any]:
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
        logger.debug(f"Setting metadata for agentId={agent_id}, key={key}")

        try:
            return self.contract.set_metadata(agent_id, key, value)
        except Exception as e:
            logger.error(f"Failed to set metadata: {str(e)}")
            raise

    def set_agent_uri(
        self,
        agent_id: int,
        agent_uri: str,
    ) -> dict[str, Any]:
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

        logger.debug(f"Setting agent URI for agentId: {agent_id}")

        try:
            # Set agent URI using setAgentURI function
            result = self.contract.set_agent_uri(agent_id, agent_uri)
            result["agentURI"] = agent_uri
            return result

        except Exception as e:
            logger.error(f"Failed to set agent URI: {str(e)}")
            raise

    @staticmethod
    def parse_agent_uri(agent_uri: str) -> dict[str, Any] | None:
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

        # Handle HTTP/HTTPS URL (with SSRF protection)
        if agent_uri.startswith("http://") or agent_uri.startswith("https://"):
            try:
                # SSRF protection: block private/reserved IP ranges
                parsed = urlparse(agent_uri)
                hostname = parsed.hostname
                if not hostname:
                    return None

                # Block known cloud metadata hostnames
                _BLOCKED_HOSTNAMES = {
                    "metadata.google.internal",
                    "metadata.goog",
                    "169.254.169.254",
                }
                if hostname.lower() in _BLOCKED_HOSTNAMES:
                    return None

                # Resolve hostname with a timeout to avoid hanging on
                # adversarial DNS servers
                def _resolve():
                    return socket.getaddrinfo(hostname, None)

                try:
                    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                        resolved_ips = pool.submit(_resolve).result(timeout=5)
                except (concurrent.futures.TimeoutError, socket.gaierror, ValueError, OSError):
                    return None

                # Pick the first resolved IP and validate it
                if not resolved_ips:
                    return None

                safe_ip_str = None
                for _, _, _, _, sockaddr in resolved_ips:
                    ip = ipaddress.ip_address(sockaddr[0])

                    # Unmap IPv6-mapped IPv4 (e.g. ::ffff:127.0.0.1)
                    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
                        ip = ip.ipv4_mapped

                    if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
                        return None
                    # Block cloud metadata IP
                    if str(ip) == "169.254.169.254":
                        return None

                    if safe_ip_str is None:
                        safe_ip_str = str(ip)

                if safe_ip_str is None:
                    return None

                # Build the request URL using the resolved IP directly to
                # prevent DNS rebinding (a second resolution returning a
                # different, internal IP).  The original Host header is
                # preserved so the remote server routes correctly.
                port = parsed.port
                if port:
                    netloc = f"{safe_ip_str}:{port}"
                else:
                    netloc = safe_ip_str
                safe_url = parsed._replace(netloc=netloc).geturl()

                response = requests.get(
                    safe_url,
                    timeout=10,
                    allow_redirects=False,
                    headers={"Host": hostname},
                )
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
    def network(self) -> dict[str, Any]:
        """
        Get the network configuration.

        Returns:
            Dict[str, Any]: The network configuration dictionary
        """
        return self._network_config
