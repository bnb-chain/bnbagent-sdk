"""
Agent URI generation utility.

Generates EIP-8004 compliant agent registration files and agent URIs.
"""

import json
import base64
from typing import Optional, Dict, Any, List
from web3 import Web3

from ..models import AgentEndpoint


class AgentURIGenerator:
    """
    Generator for EIP-8004 compliant agent registration files and agent URIs.
    """

    @staticmethod
    def generate_registration_file(
        name: str,
        description: str,
        endpoints: List[AgentEndpoint],
        image: Optional[str] = None,
        agent_id: Optional[int] = None,
        identity_registry: Optional[str] = None,
        chain_id: Optional[int] = None,
        supported_trust: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """
        Generate an EIP-8004 compliant agent registration file.

        Args:
            name: Agent name (required)
            description: Agent description (required)
            endpoints: List of AgentEndpoint instances (required, at least one)
            image: Optional agent image URL
            agent_id: Optional agent ID for registrations field
            identity_registry: Optional registry address for registrations field
            chain_id: Optional chain ID for registrations field
            supported_trust: Optional list of supported trust mechanisms

        Returns:
            dict: EIP-8004 compliant registration file

        Raises:
            ValueError: If endpoints is empty or None

        Example:
            >>> from bnbagent import AgentEndpoint
            >>> file = AgentURIGenerator.generate_registration_file(
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
        """
        if not name or not description:
            raise ValueError("name and description are required")

        if not endpoints or len(endpoints) == 0:
            raise ValueError(
                "endpoints is required and must contain at least one endpoint"
            )

        # Convert endpoints to dictionaries
        endpoint_dicts = []
        for endpoint in endpoints:
            if not isinstance(endpoint, AgentEndpoint):
                raise ValueError("endpoints must be a list of AgentEndpoint instances")
            endpoint_dicts.append(endpoint.to_dict())

        # Build registrations array
        registrations = []
        if agent_id is not None and identity_registry and chain_id is not None:
            registrations.append(
                {
                    "agentId": agent_id,
                    "agentRegistry": f"eip155:{chain_id}:{identity_registry}",
                }
            )

        # Build registration file
        registration_file = {
            "type": "https://eips.ethereum.org/EIPS/eip-8004#registration-v1",
            "name": name,
            "description": description,
            "image": image or "",
            "services": endpoint_dicts,
            "registrations": registrations,
        }

        # Add supportedTrust if provided
        if supported_trust:
            registration_file["supportedTrust"] = supported_trust

        return registration_file

    @staticmethod
    def calculate_file_hash(registration_file: Dict[str, Any]) -> str:
        """
        Calculate the hash of a registration file.

        Args:
            registration_file: Registration file dictionary

        Returns:
            str: Hex string of the file hash (with 0x prefix)
        """
        file_json = json.dumps(registration_file, sort_keys=True, separators=(",", ":"))
        file_bytes = file_json.encode("utf-8")
        file_hash = Web3.keccak(file_bytes)
        return Web3.to_hex(file_hash)

    @staticmethod
    def generate_agent_uri(
        name: str,
        description: str,
        endpoints: List[AgentEndpoint],
        image: Optional[str] = None,
        agent_id: Optional[int] = None,
        identity_registry: Optional[str] = None,
        chain_id: Optional[int] = None,
        supported_trust: Optional[List[str]] = None,
    ) -> str:
        """
        Generate agent URI for an agent registration.

        Always returns a base64 data URI format (data:application/json;base64,...).

        Args:
            name: Agent name (required)
            description: Agent description (required)
            endpoints: List of AgentEndpoint instances (required, at least one)
            image: Optional agent image URL
            agent_id: Optional agent ID for registrations field
            identity_registry: Optional registry address for registrations field
            chain_id: Optional chain ID for registrations field
            supported_trust: Optional list of supported trust mechanisms

        Returns:
            str: The generated base64 data URI

        Raises:
            ValueError: If endpoints is empty or None

        Example:
            >>> from bnbagent import AgentEndpoint
            >>> agent_uri = AgentURIGenerator.generate_agent_uri(
            ...     name="My Agent",
            ...     description="A test agent",
            ...     endpoints=[AgentEndpoint(name="A2A", endpoint="https://...")]
            ... )
            >>> print(agent_uri)
        """
        # Generate registration file
        registration_file = AgentURIGenerator.generate_registration_file(
            name=name,
            description=description,
            endpoints=endpoints,
            image=image,
            agent_id=agent_id,
            identity_registry=identity_registry,
            chain_id=chain_id,
            supported_trust=supported_trust,
        )

        # Generate base64 data URI (always)
        base64_str = AgentURIGenerator.encode_registration_file_to_base64(
            registration_file
        )
        agent_uri = f"data:application/json;base64,{base64_str}"

        return agent_uri

    @staticmethod
    def encode_registration_file_to_base64(registration_file: Dict[str, Any]) -> str:
        """
        Encode registration file to base64 string.

        Args:
            registration_file: Registration file dictionary

        Returns:
            str: Base64 encoded string of the registration file JSON

        Example:
            >>> file = AgentURIGenerator.generate_registration_file(...)
            >>> base64_str = AgentURIGenerator.encode_registration_file_to_base64(file)
        """
        file_json = json.dumps(registration_file, sort_keys=True, separators=(",", ":"))
        file_bytes = file_json.encode("utf-8")
        base64_str = base64.b64encode(file_bytes).decode("utf-8")
        return base64_str

    @staticmethod
    def decode_registration_file_from_base64(base64_str: str) -> Dict[str, Any]:
        """
        Decode base64 string to registration file.

        Args:
            base64_str: Base64 encoded string (with or without data URI prefix)

        Returns:
            dict: Registration file dictionary

        Example:
            >>> file = AgentURIGenerator.decode_registration_file_from_base64(base64_str)
        """
        # Handle data URI format: data:application/json;base64,{base64_string}
        if base64_str.startswith("data:application/json;base64,"):
            base64_str = base64_str.split(",", 1)[1]

        file_bytes = base64.b64decode(base64_str)
        file_json = file_bytes.decode("utf-8")
        registration_file = json.loads(file_json)
        return registration_file
