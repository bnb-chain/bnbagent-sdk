"""
Test cases for AgentURI generation and parsing
"""

import pytest
import base64
import json
from bnbagent import AgentEndpoint
from bnbagent.utils.agent_uri import AgentURIGenerator


class TestAgentURIGenerator:
    """Test cases for AgentURIGenerator"""

    def test_generate_registration_file(self):
        """Test generating registration file"""
        file = AgentURIGenerator.generate_registration_file(
            name="Test Agent",
            description="A test agent",
            image="https://example.com/image.png",
            endpoints=[
                AgentEndpoint(
                    name="A2A",
                    endpoint="https://agent.example/.well-known/agent-card.json",
                    version="0.3.0",
                )
            ],
        )

        assert file["type"] == "https://eips.ethereum.org/EIPS/eip-8004#registration-v1"
        assert file["name"] == "Test Agent"
        assert file["description"] == "A test agent"
        assert file["image"] == "https://example.com/image.png"
        assert len(file["services"]) == 1
        assert file["services"][0]["name"] == "A2A"

    def test_generate_registration_file_requires_name_description(self):
        """Test that name and description are required"""
        endpoints = [
            AgentEndpoint(
                name="A2A",
                endpoint="https://agent.example/.well-known/agent-card.json",
            )
        ]
        with pytest.raises(ValueError, match="name and description are required"):
            AgentURIGenerator.generate_registration_file(
                name="", description="Test", endpoints=endpoints
            )

        with pytest.raises(ValueError, match="name and description are required"):
            AgentURIGenerator.generate_registration_file(
                name="Test", description="", endpoints=endpoints
            )

    def test_generate_registration_file_requires_endpoints(self):
        """Test that endpoints are required"""
        with pytest.raises(ValueError, match="endpoints is required"):
            AgentURIGenerator.generate_registration_file(
                name="Test", description="Test", endpoints=None
            )

        with pytest.raises(ValueError, match="endpoints is required"):
            AgentURIGenerator.generate_registration_file(
                name="Test", description="Test", endpoints=[]
            )

    def test_generate_agent_uri(self):
        """Test generating agent URI (base64)"""
        agent_uri = AgentURIGenerator.generate_agent_uri(
            name="Test Agent",
            description="A test agent",
            endpoints=[
                AgentEndpoint(
                    name="A2A",
                    endpoint="https://agent.example/.well-known/agent-card.json",
                )
            ],
        )

        assert isinstance(agent_uri, str)
        assert agent_uri.startswith("data:application/json;base64,")

        # Decode and verify content
        base64_str = agent_uri.split(",", 1)[1]
        decoded = json.loads(base64.b64decode(base64_str).decode("utf-8"))
        assert decoded["name"] == "Test Agent"
        assert decoded["description"] == "A test agent"
        assert len(decoded["services"]) == 1

    def test_generate_agent_uri_with_registrations(self):
        """Test generating agent URI with registrations field"""
        agent_uri = AgentURIGenerator.generate_agent_uri(
            name="Test Agent",
            description="A test agent",
            endpoints=[
                AgentEndpoint(
                    name="A2A",
                    endpoint="https://agent.example/.well-known/agent-card.json",
                )
            ],
            agent_id=1,
            identity_registry="0x5FbDB2315678afecb367f032d93F642f64180aa3",
            chain_id=97,
        )

        base64_str = agent_uri.split(",", 1)[1]
        decoded = json.loads(base64.b64decode(base64_str).decode("utf-8"))
        assert "registrations" in decoded
        assert len(decoded["registrations"]) == 1
        assert decoded["registrations"][0]["agentId"] == 1
        assert (
            "eip155:97:0x5FbDB2315678afecb367f032d93F642f64180aa3"
            in decoded["registrations"][0]["agentRegistry"]
        )

    def test_encode_decode_registration_file(self):
        """Test encoding and decoding registration file"""
        registration_file = {
            "type": "https://eips.ethereum.org/EIPS/eip-8004#registration-v1",
            "name": "Test Agent",
            "description": "A test agent",
            "image": "",
            "services": [],
            "registrations": [],
        }

        # Encode
        base64_str = AgentURIGenerator.encode_registration_file_to_base64(
            registration_file
        )
        assert isinstance(base64_str, str)

        # Decode
        decoded = AgentURIGenerator.decode_registration_file_from_base64(base64_str)
        assert decoded["name"] == registration_file["name"]
        assert decoded["description"] == registration_file["description"]

    def test_decode_registration_file_with_prefix(self):
        """Test decoding registration file with data URI prefix"""
        registration_file = {
            "name": "Test Agent",
            "description": "A test agent",
        }
        base64_str = AgentURIGenerator.encode_registration_file_to_base64(
            registration_file
        )
        data_uri = f"data:application/json;base64,{base64_str}"

        decoded = AgentURIGenerator.decode_registration_file_from_base64(data_uri)
        assert decoded["name"] == registration_file["name"]
