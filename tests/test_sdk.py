"""
Test cases for ERC8004Agent SDK based on examples/basic_usage.py
"""

import pytest
from unittest.mock import Mock, patch
from bnbagent import ERC8004Agent, AgentEndpoint


class TestERC8004Agent:
    """Test cases for ERC8004Agent SDK initialization and basic operations"""

    # Default test configuration
    DEFAULT_NETWORK = "bsc-testnet"
    DEFAULT_CONTRACT_ADDRESS = "0x8004A41392bdd4A4F12339447ab4B8719D562e51"

    @pytest.fixture
    def mock_contract_interface(self):
        """Mock ContractInterface instance"""
        mock_contract = Mock()
        mock_contract.contract_address = self.DEFAULT_CONTRACT_ADDRESS
        mock_contract.register_agent.return_value = {
            "success": True,
            "transactionHash": "0x" + "0" * 64,
            "agentId": 1,
            "receipt": Mock(status=1, logs=[]),
        }
        mock_contract.get_agent_info.return_value = {
            "agentId": 1,
            "agentAddress": "0x" + "1" * 40,
            "owner": "0x" + "2" * 40,
            "agentURI": "data:application/json;base64,eyJuYW1lIjoiTXkgVGVzdCBBZ2VudCJ9",
        }
        mock_contract.get_metadata.return_value = "test value"
        mock_contract.set_metadata.return_value = {
            "success": True,
            "transactionHash": "0x" + "0" * 64,
            "receipt": Mock(status=1, logs=[]),
        }
        mock_contract.set_agent_uri.return_value = {
            "success": True,
            "transactionHash": "0x" + "0" * 64,
            "agentURI": "data:application/json;base64,eyJuYW1lIjoiVXBkYXRlZCBBZ2VudCJ9",
            "receipt": Mock(status=1, logs=[]),
            "updatedBy": "0x" + "2" * 40,
        }
        return mock_contract

    @pytest.fixture
    def mock_wallet_provider(self):
        """Mock WalletProvider instance"""
        mock_wallet = Mock()
        mock_wallet.address = "0x" + "3" * 40
        return mock_wallet

    @pytest.fixture
    def sdk(self, mock_contract_interface, mock_wallet_provider):
        """Create SDK instance with mocked contract interface"""
        with patch(
            "bnbagent.erc8004_agent.ContractInterface"
        ) as mock_contract_class, patch(
            "bnbagent.erc8004_agent.Web3"
        ) as mock_web3_class:
            # Mock Web3 connection check
            mock_web3 = Mock()
            mock_web3.is_connected.return_value = True
            mock_web3.eth.chain_id = 1337  # Local network chain ID
            mock_web3_class.return_value = mock_web3

            # Mock ContractInterface
            mock_contract_class.return_value = mock_contract_interface

            sdk = ERC8004Agent(
                wallet_provider=mock_wallet_provider,
                network=self.DEFAULT_NETWORK,
                debug=True,
            )
            return sdk

    def test_sdk_initialization(self, sdk):
        """Test SDK initialization"""
        assert sdk is not None
        assert sdk.wallet_address is not None
        assert sdk.contract_address is not None

    def test_generate_agent_uri(self, sdk):
        """Test Example 1: Generate Agent URI"""
        agent_uri = sdk.generate_agent_uri(
            name="My Test Agent",
            description="A test agent for demonstration",
            image="https://example.com/image.png",
            endpoints=[
                AgentEndpoint(
                    name="A2A",
                    endpoint="https://agent.example/.well-known/agent-card.json",
                    version="0.3.0",
                )
            ],
        )

        assert isinstance(agent_uri, str)
        assert agent_uri.startswith("data:application/json;base64,")

    def test_register_agent_with_agent_uri(self, sdk):
        """Test registering agent with generated agent URI"""
        agent_uri = sdk.generate_agent_uri(
            name="My Test Agent",
            description="A test agent for demonstration",
            endpoints=[
                AgentEndpoint(
                    name="A2A",
                    endpoint="https://agent.example/.well-known/agent-card.json",
                )
            ],
        )

        result = sdk.register_agent(agent_uri=agent_uri)

        assert "agentId" in result
        assert "transactionHash" in result
        assert result["agentId"] == 1

    def test_register_agent_auto_generate_uri(self, sdk):
        """Test registering agent with generated URI"""
        # First generate agent URI
        agent_uri = sdk.generate_agent_uri(
            name="Auto-Generated Agent",
            description="Agent with auto-generated URI",
            endpoints=[
                AgentEndpoint(
                    name="A2A",
                    endpoint="https://agent.example/.well-known/agent-card.json",
                )
            ],
        )

        # Then register with the generated URI
        result = sdk.register_agent(agent_uri=agent_uri)

        assert "agentId" in result
        assert "agentURI" in result
        assert "transactionHash" in result

    def test_get_agent_info(self, sdk):
        """Test getting agent information"""
        info = sdk.get_agent_info(agent_id=1)

        assert "agentId" in info
        assert "agentAddress" in info
        assert "owner" in info
        assert "agentURI" in info
        assert info["agentId"] == 1

    def test_parse_agent_uri_base64(self, sdk):
        """Test parsing base64 agent URI"""
        base64_uri = "data:application/json;base64,eyJuYW1lIjoiTXkgVGVzdCBBZ2VudCIsImRlc2NyaXB0aW9uIjoiQSB0ZXN0IGFnZW50In0="
        agent_data = sdk.parse_agent_uri(base64_uri)

        assert agent_data is not None
        assert "name" in agent_data
        assert "description" in agent_data

    def test_parse_agent_uri_http(self, sdk):
        """Test parsing HTTP agent URI"""
        with patch("bnbagent.erc8004_agent.requests.get") as mock_get:
            mock_response = Mock()
            mock_response.json.return_value = {
                "name": "Test Agent",
                "description": "Test Description",
            }
            mock_response.raise_for_status = Mock()
            mock_get.return_value = mock_response

            agent_data = sdk.parse_agent_uri("https://example.com/agent.json")

            assert agent_data is not None
            assert "name" in agent_data
            assert "description" in agent_data

    def test_register_agent_with_metadata(self, sdk):
        """Test Example 2: Register agent with metadata"""
        # First generate a valid agent URI
        agent_uri = sdk.generate_agent_uri(
            name="My Test Agent",
            description="A test agent for demonstration",
            endpoints=[
                AgentEndpoint(
                    name="A2A",
                    endpoint="https://agent.example/.well-known/agent-card.json",
                )
            ],
        )

        result = sdk.register_agent(
            agent_uri=agent_uri,
            metadata=[
                {"key": "name", "value": "My Test Agent"},
                {"key": "version", "value": "1.0.0"},
                {"key": "description", "value": "A test agent for demonstration"},
            ],
        )

        assert "agentId" in result
        assert result["agentId"] == 1

    def test_get_metadata(self, sdk):
        """Test getting metadata for an agent"""
        value = sdk.get_metadata(agent_id=1, key="name")

        assert value == "test value"

    def test_set_metadata(self, sdk):
        """Test Example 3: Set metadata for existing agent"""
        result = sdk.set_metadata(
            agent_id=1,
            key="updated_info",
            value="This metadata was added later",
        )

        assert "transactionHash" in result
        assert "success" in result

    def test_set_agent_uri(self, sdk):
        """Test updating agent URI"""
        # First generate new agent URI
        agent_uri = sdk.generate_agent_uri(
            name="Updated Agent",
            description="Updated description",
            endpoints=[
                AgentEndpoint(
                    name="A2A",
                    endpoint="https://updated.agent.example/.well-known/agent-card.json",
                )
            ],
        )

        # Then set with the generated URI
        result = sdk.set_agent_uri(agent_id=1, agent_uri=agent_uri)

        assert "transactionHash" in result
        assert "agentURI" in result

    def test_agent_endpoint_model(self):
        """Test AgentEndpoint model validation"""
        # Valid endpoint
        endpoint = AgentEndpoint(
            name="A2A",
            endpoint="https://agent.example/.well-known/agent-card.json",
            version="0.3.0",
        )
        assert endpoint.name == "A2A"
        assert endpoint.endpoint == "https://agent.example/.well-known/agent-card.json"
        assert endpoint.version == "0.3.0"

        # Test to_dict
        endpoint_dict = endpoint.to_dict()
        assert endpoint_dict["name"] == "A2A"
        assert (
            endpoint_dict["endpoint"]
            == "https://agent.example/.well-known/agent-card.json"
        )
        assert endpoint_dict["version"] == "0.3.0"

        # Test from_dict
        endpoint2 = AgentEndpoint.from_dict(endpoint_dict)
        assert endpoint2.name == endpoint.name
        assert endpoint2.endpoint == endpoint.endpoint

        # Test validation - invalid URL
        with pytest.raises(
            ValueError, match="endpoint must start with http:// or https://"
        ):
            AgentEndpoint(name="A2A", endpoint="invalid-url")

        # Test validation - missing name
        with pytest.raises(ValueError, match="name is required"):
            AgentEndpoint(name="", endpoint="https://example.com")

    def test_parse_agent_uri_invalid(self, sdk):
        """Test parsing invalid agent URI"""
        result = sdk.parse_agent_uri("invalid-uri")
        assert result is None

        result = sdk.parse_agent_uri("")
        assert result is None

    def test_register_agent_requires_agent_uri(self, sdk):
        """Test that register_agent requires agent_uri"""
        with pytest.raises(ValueError, match="agent_uri is required"):
            sdk.register_agent(agent_uri="")

        with pytest.raises(TypeError):
            # Missing required argument
            sdk.register_agent()

    def test_generate_agent_uri_requires_endpoints(self, sdk):
        """Test that generate_agent_uri requires endpoints"""
        with pytest.raises(ValueError, match="endpoints is required"):
            sdk.generate_agent_uri(name="Test", description="Test", endpoints=None)

        with pytest.raises(ValueError, match="endpoints is required"):
            sdk.generate_agent_uri(name="Test", description="Test", endpoints=[])
