"""
Test cases for data models
"""

import pytest

from bnbagent import AgentEndpoint


class TestAgentEndpoint:
    """Test cases for AgentEndpoint model"""

    def test_create_endpoint_required_fields(self):
        """Test creating endpoint with required fields"""
        endpoint = AgentEndpoint(
            name="A2A",
            endpoint="https://agent.example/.well-known/agent-card.json",
        )

        assert endpoint.name == "A2A"
        assert endpoint.endpoint == "https://agent.example/.well-known/agent-card.json"
        assert endpoint.version is None
        assert endpoint.capabilities == []

    def test_create_endpoint_with_optional_fields(self):
        """Test creating endpoint with optional fields"""
        endpoint = AgentEndpoint(
            name="MCP",
            endpoint="https://mcp.agent.example/",
            version="2025-06-18",
            capabilities=["tools", "prompts"],
        )

        assert endpoint.name == "MCP"
        assert endpoint.endpoint == "https://mcp.agent.example/"
        assert endpoint.version == "2025-06-18"
        assert endpoint.capabilities == ["tools", "prompts"]

    def test_endpoint_validation_name_required(self):
        """Test that name is required"""
        with pytest.raises(ValueError, match="name is required"):
            AgentEndpoint(name="", endpoint="https://example.com")

        with pytest.raises(ValueError, match="name is required"):
            AgentEndpoint(name=None, endpoint="https://example.com")

    def test_endpoint_validation_endpoint_required(self):
        """Test that endpoint is required"""
        with pytest.raises(ValueError, match="endpoint is required"):
            AgentEndpoint(name="A2A", endpoint="")

        with pytest.raises(ValueError, match="endpoint is required"):
            AgentEndpoint(name="A2A", endpoint=None)

    def test_endpoint_validation_url_format(self):
        """Test that endpoint must be a valid HTTP/HTTPS URL"""
        with pytest.raises(ValueError, match="endpoint must start with http:// or https://"):
            AgentEndpoint(name="A2A", endpoint="invalid-url")

        with pytest.raises(ValueError, match="endpoint must start with http:// or https://"):
            AgentEndpoint(name="A2A", endpoint="ftp://example.com")

    def test_endpoint_to_dict(self):
        """Test converting endpoint to dictionary"""
        endpoint = AgentEndpoint(
            name="A2A",
            endpoint="https://agent.example/.well-known/agent-card.json",
            version="0.3.0",
            capabilities=["tools"],
        )

        endpoint_dict = endpoint.to_dict()
        assert endpoint_dict["name"] == "A2A"
        assert endpoint_dict["endpoint"] == "https://agent.example/.well-known/agent-card.json"
        assert endpoint_dict["version"] == "0.3.0"
        assert endpoint_dict["capabilities"] == ["tools"]

    def test_endpoint_to_dict_omits_none(self):
        """Test that to_dict omits None values"""
        endpoint = AgentEndpoint(
            name="A2A",
            endpoint="https://agent.example/.well-known/agent-card.json",
        )

        endpoint_dict = endpoint.to_dict()
        assert "version" not in endpoint_dict
        assert "capabilities" not in endpoint_dict

    def test_endpoint_from_dict(self):
        """Test creating endpoint from dictionary"""
        endpoint_dict = {
            "name": "A2A",
            "endpoint": "https://agent.example/.well-known/agent-card.json",
            "version": "0.3.0",
        }

        endpoint = AgentEndpoint.from_dict(endpoint_dict)
        assert endpoint.name == "A2A"
        assert endpoint.endpoint == "https://agent.example/.well-known/agent-card.json"
        assert endpoint.version == "0.3.0"

    def test_endpoint_from_dict_missing_required(self):
        """Test that from_dict requires name and endpoint"""
        with pytest.raises(ValueError, match="must contain 'name' and 'endpoint' fields"):
            AgentEndpoint.from_dict({"name": "A2A"})

        with pytest.raises(ValueError, match="must contain 'name' and 'endpoint' fields"):
            AgentEndpoint.from_dict({"endpoint": "https://example.com"})
