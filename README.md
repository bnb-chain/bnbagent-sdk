# BNBAgent SDK

Python SDK for ERC-8004 on-chain agent registration and management.

## Features

- **Agent Registration**: Register AI agents on-chain via ERC-8004 Identity Registry
- **Keystore Encryption**: Password-protected wallet (Keystore V3, compatible with MetaMask/Geth)
- **Gasless Transactions**: Integrated MegaFuel Paymaster for gas-free transactions
- **Agent URI Generation**: Generate EIP-8004 compliant agent metadata
- **Extensible Wallet**: WalletProvider abstraction for EVM/MPC wallets

## Network

### BSC Testnet (Chain ID: 97)

| Contract | Address |
|----------|---------|
| Identity Registry | `0x8004A818BFB912233c491871b3d84c89A494BD9e` |
| Reputation Registry | `0x8004B663056A597Dffe9eCcC1965A193B7388713` |

### BSC Mainnet (Chain ID: 56)

> **Note:** Mainnet support is not yet available. It will be supported soon. Stay tuned!


## Installation

### Prerequisites

- Python 3.10 or higher
- `uv` package manager (recommended) or `pip`

### Install from GitHub

```bash
pip install git+https://github.com/bnb-chain/bnbagent-sdk.git
```

### Install for Development

```bash
git clone https://github.com/bnb-chain/bnbagent-sdk.git
cd bnbagent-sdk

# Install with uv (recommended)
uv sync --extra dev

# Or install with pip
pip install -e ".[dev]"
```

## Quick Start

### Basic Usage

See `examples/testnet_usage.py` for a complete example.

```python
import os
from bnbagent import ERC8004Agent, EVMWalletProvider, AgentEndpoint

# Get password from environment variable
password = os.getenv("WALLET_PASSWORD")
if not password:
    raise ValueError("WALLET_PASSWORD environment variable is required")

# Create wallet provider
wallet = EVMWalletProvider(password=password)

# Initialize SDK with wallet provider
sdk = ERC8004Agent(
    wallet_provider=wallet,
    network="bsc-testnet",
    debug=True
)

# Generate agent URI
agent_uri = sdk.generate_agent_uri(
    name="My Agent",
    description="A test agent for demonstration",
    endpoints=[
        AgentEndpoint(
            name="A2A",
            endpoint="https://agent.example/.well-known/agent-card.json",
            version="0.3.0"
        )
    ]
)

# Register the agent
result = sdk.register_agent(agent_uri=agent_uri)

print(f"Agent registered with ID: {result['agentId']}")
print(f"Transaction hash: {result['transactionHash']}")
```

### Using Existing Private Key

```python
from bnbagent import ERC8004Agent, EVMWalletProvider

wallet = EVMWalletProvider(
    password="your-secure-password",
    private_key="0x...",
)

sdk = ERC8004Agent(wallet_provider=wallet, network="bsc-testnet")
```

## API Reference

### ERC8004Agent

Main SDK class for agent operations.

#### `generate_agent_uri(name, description, image=None, endpoints=None, agent_id=None, supported_trust=None)`

Generate agent URI for registration.

**Parameters:**
- `name` (str): Agent name (required)
- `description` (str): Agent description (required)
- `image` (str, optional): Agent image URL
- `endpoints` (list, optional): List of endpoint configurations
  - `name`: str (e.g., "A2A", "MCP")
  - `endpoint`: str (URL)
  - `version`: Optional str
  - `capabilities`: Optional list
- `agent_id` (int, optional): Agent ID for registrations field
- `supported_trust` (list, optional): List of supported trust mechanisms

**Returns:**
- `str`: Base64 encoded data URI

**Example:**
```python
agent_uri = sdk.generate_agent_uri(
    name="My Agent",
    description="A test agent",
    image="https://example.com/image.png",
    endpoints=[
        AgentEndpoint(
            name="A2A",
            endpoint="https://agent.example/.well-known/agent-card.json",
            version="0.3.0"
        )
    ]
)
```

---

#### `register_agent(agent_uri, metadata=None)`

Register a new agent on-chain.

**Parameters:**
- `agent_uri` (str): Agent URI string from `generate_agent_uri()`
- `metadata` (list, optional): List of metadata entries `[{'key': str, 'value': str}]`

**Returns:**
- `dict`: `{success, transactionHash, agentId, agentURI, receipt}`

**Example:**
```python
# Basic registration
result = sdk.register_agent(agent_uri=agent_uri)
print(f"Agent ID: {result['agentId']}")

# With metadata
result = sdk.register_agent(
    agent_uri=agent_uri,
    metadata=[
        {'key': 'version', 'value': '1.0.0'},
        {'key': 'author', 'value': 'John Doe'}
    ]
)
```

---

#### `get_agent_info(agent_id)`

Get information about a registered agent from chain.

**Parameters:**
- `agent_id` (int): The agent ID (token ID)

**Returns:**
- `dict`: `{agentId, agentAddress, owner, agentURI}`

**Example:**
```python
info = sdk.get_agent_info(agent_id=1)
print(f"Agent address: {info['agentAddress']}")
print(f"Owner: {info['owner']}")
print(f"Agent URI: {info['agentURI']}")
```

---

#### `parse_agent_uri(agent_uri)`

Parse agent URI to JSON.

**Parameters:**
- `agent_uri` (str): The agent URI string (base64 data URI or HTTP URL)

**Returns:**
- `dict | None`: Parsed JSON dictionary, or None if parsing fails

**Example:**
```python
info = sdk.get_agent_info(agent_id=1)
agent_data = sdk.parse_agent_uri(info['agentURI'])
if agent_data:
    print(f"Agent name: {agent_data['name']}")
    print(f"Services: {agent_data.get('services', [])}")
```

---

#### `update_agent_uri(agent_id, agent_uri)`

Update agent URI for an existing agent.

**Parameters:**
- `agent_id` (int): The agent ID to update
- `agent_uri` (str): New agent URI string

**Returns:**
- `dict`: `{success, transactionHash, receipt, agentURI}`

**Example:**
```python
new_uri = sdk.generate_agent_uri(
    name="Updated Agent",
    description="Updated description",
    endpoints=[AgentEndpoint(name="A2A", endpoint="https://...")]
)
result = sdk.update_agent_uri(agent_id=1, agent_uri=new_uri)
```

---

#### `get_metadata(agent_id, key)`

Get metadata value for an agent.

**Parameters:**
- `agent_id` (int): The agent ID
- `key` (str): The metadata key

**Returns:**
- `str`: The metadata value

**Example:**
```python
version = sdk.get_metadata(agent_id=1, key="version")
print(f"Version: {version}")
```

---

#### `set_metadata(agent_id, key, value)`

Set metadata for an agent (must be owner or operator).

**Parameters:**
- `agent_id` (int): The agent ID
- `key` (str): The metadata key
- `value` (str): The metadata value

**Returns:**
- `dict`: `{success, transactionHash, receipt}`

**Example:**
```python
result = sdk.set_metadata(
    agent_id=1,
    key="description",
    value="My agent description"
)
```

---

#### Properties

- `wallet_address` (str): The wallet address
- `contract_address` (str): The Identity Registry contract address
- `network` (dict): Network configuration

## Discover Agents

### List All Agents (via 8004scan API)

To discover registered agents, use the 8004scan REST API:

```bash
# List agents (chain_id: 97=testnet, 56=mainnet)
curl "https://www.8004scan.io/api/v1/agents?limit=10&offset=0&chain_id=97"
```

**Response (key fields):**
```json
{
  "items": [
    {
      "token_id": "2",
      "name": "My Agent",
      "description": "A test agent",
      "owner_address": "0x...",
      "chain_id": 97,
      "services": {
        "a2a": { "endpoint": "https://...", "version": "0.3.0" },
        "mcp": null
      },
      "total_score": 53.69
      // ... more fields
    }
  ],
  "total": 100,
  "limit": 10,
  "offset": 0
}
```

### Get Single Agent (via SDK)

For detailed agent information, use the SDK to query on-chain:

```python
info = sdk.get_agent_info(agent_id=1)
print(f"Agent address: {info['agentAddress']}")
print(f"Owner: {info['owner']}")

# Parse agent URI to get metadata
agent_data = sdk.parse_agent_uri(info['agentURI'])
print(f"Name: {agent_data['name']}")
print(f"Description: {agent_data['description']}")
print(f"Services: {agent_data.get('services', [])}")
```

## Wallet Management

### Export Wallet

```python
# Export as Keystore JSON (recommended)
keystore = wallet.export_keystore()
with open("my-wallet.json", "w") as f:
    json.dump(keystore, f)

# Export private key (use with caution!)
# private_key = wallet.export_private_key()
```

### State File

The SDK stores wallet state in `.bnbagent_state` using encrypted Keystore V3 format:
- AES-128-CTR encryption with scrypt key derivation
- File permissions: 0o600 (owner read/write only)
- Compatible with MetaMask/Geth keystore format

> **Warning:** Never commit `.bnbagent_state` to version control.

## Error Handling

```python
try:
    result = sdk.register_agent(agent_uri=agent_uri)
except ConnectionError as e:
    print(f"RPC connection failed: {e}")
except ValueError as e:
    print(f"Invalid input: {e}")
except RuntimeError as e:
    print(f"Transaction failed: {e}")
```

## Running Tests

```bash
# With uv
uv run pytest

# With pip
pytest

# With coverage
uv run pytest --cov=bnbagent
```

## Contributing

1. Follow existing code patterns
2. Include error handling
3. Update this README with new features
4. Run tests before submitting: `uv run pytest`

## License

This SDK is part of the ERC-8004 implementation project.
