# API Reference

Complete API documentation for the BNBAgent SDK.

## Table of Contents

- [Workflow Examples](#workflow-examples)
- [ERC8004Agent](#erc8004agent)
  - [Constructor](#constructor)
  - [Agent Registration](#agent-registration)
  - [Agent Discovery](#agent-discovery)
  - [Agent Management](#agent-management)
  - [Metadata Operations](#metadata-operations)
  - [Utility Methods](#utility-methods)
  - [Properties](#properties)
- [EVMWalletProvider](#evmwalletprovider)
- [AgentEndpoint](#agentendpoint)

---

## Workflow Examples

### Register and Query Agent

```python
from bnbagent import ERC8004Agent, EVMWalletProvider, AgentEndpoint
import os

# 1. Initialize
wallet = EVMWalletProvider(password=os.getenv("WALLET_PASSWORD"))
sdk = ERC8004Agent(wallet_provider=wallet, network="bsc-testnet")

# 2. Check if agent already registered locally
info = sdk.get_local_agent_info("My Agent")
if info:
    print(f"Agent already registered with ID: {info['agent_id']}")
else:
    # 3. Generate agent URI
    agent_uri = sdk.generate_agent_uri(
        name="My Agent",
        description="A demo agent",
        endpoints=[
            AgentEndpoint(
                name="A2A",
                endpoint="https://agent.example/.well-known/agent-card.json",
                version="0.3.0"
            )
        ]
    )

    # 4. Register on-chain
    result = sdk.register_agent(agent_uri=agent_uri)
    print(f"Registered! Agent ID: {result['agentId']}")

# 5. Query agent info from chain
agent_info = sdk.get_agent_info(agent_id=1)
print(f"Owner: {agent_info['owner']}")
```

### Update Existing Agent

```python
# 1. Get current agent info
info = sdk.get_local_agent_info("My Agent")
agent_id = info['agent_id']

# 2. Generate new URI with updated info
new_uri = sdk.generate_agent_uri(
    name="My Agent",
    description="Updated description with new features",
    endpoints=[
        AgentEndpoint(name="A2A", endpoint="https://new-endpoint.example/agent.json")
    ],
    agent_id=agent_id  # Include to populate registrations field
)

# 3. Update on-chain
result = sdk.set_agent_uri(agent_id=agent_id, agent_uri=new_uri)
print(f"Updated! Tx: {result['transactionHash']}")
```

### Browse Registered Agents

```python
# List agents with pagination
total_agents = []
offset = 0
limit = 10

while True:
    result = sdk.get_all_agents(limit=limit, offset=offset)
    total_agents.extend(result['items'])

    if len(result['items']) < limit:
        break
    offset += limit

print(f"Found {len(total_agents)} agents")
for agent in total_agents:
    print(f"  #{agent['token_id']}: {agent['name']}")
```

---

## ERC8004Agent

Main SDK class for ERC-8004 on-chain agent operations.

### Constructor

```python
ERC8004Agent(
    wallet_provider: WalletProvider,
    network: str = "bsc-testnet",
    debug: bool = False
)
```

**Parameters:**
- `wallet_provider` (WalletProvider): Wallet provider instance (required)
- `network` (str): Network name. Currently only `"bsc-testnet"` is supported
- `debug` (bool): Enable debug logging (default: `False`)

**Raises:**
- `ValueError`: If wallet_provider is not provided or network is invalid
- `ConnectionError`: If RPC connection fails

**Example:**
```python
from bnbagent import ERC8004Agent, EVMWalletProvider

wallet = EVMWalletProvider(password="your-password")
sdk = ERC8004Agent(wallet_provider=wallet, network="bsc-testnet", debug=True)
```

---

### Agent Registration

#### `generate_agent_uri(name, description, endpoints, image=None, agent_id=None, supported_trust=None)`

Generate agent URI for registration.

**Parameters:**
- `name` (str): Agent name (required)
- `description` (str): Agent description (required)
- `endpoints` (list[AgentEndpoint]): List of endpoint configurations (required, at least one)
- `image` (str, optional): Agent image URL
- `agent_id` (int, optional): Agent ID for registrations field
- `supported_trust` (list[str], optional): List of supported trust mechanisms (e.g., `["reputation", "crypto-economic"]`)

**Returns:**
- `str`: Base64 encoded data URI

**Raises:**
- `ValueError`: If endpoints is empty or None

**Example:**
```python
from bnbagent import AgentEndpoint

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
    ],
    supported_trust=["reputation"]
)
```

---

#### `register_agent(agent_uri, metadata=None)`

Register a new agent on-chain.

**Parameters:**
- `agent_uri` (str): Agent URI string from `generate_agent_uri()` (required)
- `metadata` (list[dict], optional): List of metadata entries `[{'key': str, 'value': str}]`

**Returns:**
- `dict`: Registration result
  - `success` (bool): Whether registration succeeded
  - `transactionHash` (str): Transaction hash
  - `agentId` (int): The registered agent ID
  - `agentURI` (str): The final agent URI (with registrations field populated)
  - `receipt`: Transaction receipt

**Raises:**
- `ValueError`: If agent_uri is empty or invalid

**Notes:**
- Gas fees are sponsored by MegaFuel Paymaster on testnet
- After registration, the SDK automatically updates the agent URI with the `registrations` field populated
- Agent info is saved to local state file (`.bnbagent_state`)

**Example:**
```python
# Basic registration
result = sdk.register_agent(agent_uri=agent_uri)
print(f"Agent ID: {result['agentId']}")
print(f"Tx Hash: {result['transactionHash']}")

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

### Agent Discovery

#### `get_all_agents(limit=10, offset=0)`

List all registered agents.

**Parameters:**
- `limit` (int): Maximum number of agents to return (default: 10, max: 100)
- `offset` (int): Number of agents to skip for pagination (default: 0)

**Returns:**
- `dict`: Response containing:
  - `items` (list): List of agent objects
    - `token_id` (int): Agent ID
    - `name` (str): Agent name
    - `description` (str): Agent description
    - `owner_address` (str): Owner wallet address
    - `services` (dict): Service endpoints
    - `total_score` (float): Reputation score
  - `total` (int): Total number of agents
  - `limit` (int): Limit used
  - `offset` (int): Offset used

**Raises:**
- `ConnectionError`: If API request fails

**Example:**
```python
# List first 10 agents
agents = sdk.get_all_agents(limit=10)
for agent in agents['items']:
    print(f"Agent #{agent['token_id']}: {agent['name']}")

# Paginate through results
page1 = sdk.get_all_agents(limit=10, offset=0)
page2 = sdk.get_all_agents(limit=10, offset=10)
```

---

#### `get_agent_info(agent_id)`

Get information about a registered agent from chain.

**Parameters:**
- `agent_id` (int): The agent ID (token ID)

**Returns:**
- `dict`: Agent information
  - `agentId` (int): The agent ID
  - `agentAddress` (str): Deterministic agent address
  - `owner` (str): Owner address
  - `agentURI` (str): Agent URI

**Example:**
```python
info = sdk.get_agent_info(agent_id=1)
print(f"Agent address: {info['agentAddress']}")
print(f"Owner: {info['owner']}")
print(f"Agent URI: {info['agentURI']}")
```

---

#### `get_local_agent_info(name)`

Get agent info from local state file by name.

**Parameters:**
- `name` (str): The agent name to look up

**Returns:**
- `dict | None`: Agent info if found, otherwise `None`
  - `name` (str): Agent name
  - `agent_uri` (str): Agent URI
  - `agent_id` (int): Agent ID
  - `transaction_hash` (str): Registration transaction hash

**Notes:**
- Only reads local `.bnbagent_state` file, does not query on-chain
- Useful to check if agent was previously registered before re-registering

**Example:**
```python
# Check if agent exists locally before registering
info = sdk.get_local_agent_info("My Agent")
if info:
    print(f"Found agent with ID: {info['agent_id']}")
else:
    # Register new agent
    result = sdk.register_agent(agent_uri=agent_uri)
```

---

### Agent Management

#### `set_agent_uri(agent_id, agent_uri)`

Update agent URI for an existing agent.

**Parameters:**
- `agent_id` (int): The agent ID to update
- `agent_uri` (str): New agent URI string (required)

**Returns:**
- `dict`: Transaction result
  - `success` (bool): Whether update succeeded
  - `transactionHash` (str): Transaction hash
  - `receipt`: Transaction receipt
  - `agentURI` (str): The new agent URI

**Raises:**
- `ValueError`: If agent_uri is empty

**Notes:**
- Only the agent owner or approved operator can update
- Gas fees are sponsored by MegaFuel Paymaster on testnet

**Example:**
```python
new_uri = sdk.generate_agent_uri(
    name="Updated Agent",
    description="Updated description",
    endpoints=[AgentEndpoint(name="A2A", endpoint="https://...")],
    agent_id=1  # Include agent_id to populate registrations
)
result = sdk.set_agent_uri(agent_id=1, agent_uri=new_uri)
print(f"Updated: {result['transactionHash']}")
```

---

### Metadata Operations

#### `get_metadata(agent_id, key)`

Get metadata value for an agent.

**Parameters:**
- `agent_id` (int): The agent ID
- `key` (str): The metadata key

**Returns:**
- `str`: The metadata value (decoded from bytes)

**Raises:**
- `RuntimeError`: If metadata retrieval fails

**Example:**
```python
version = sdk.get_metadata(agent_id=1, key="version")
print(f"Version: {version}")
```

---

#### `set_metadata(agent_id, key, value)`

Set metadata for an agent.

**Parameters:**
- `agent_id` (int): The agent ID
- `key` (str): The metadata key
- `value` (str): The metadata value (will be encoded to bytes)

**Returns:**
- `dict`: Transaction result
  - `success` (bool): Whether update succeeded
  - `transactionHash` (str): Transaction hash
  - `receipt`: Transaction receipt

**Raises:**
- `RuntimeError`: If metadata update fails

**Notes:**
- Only the agent owner or approved operator can set metadata
- Gas fees are sponsored by MegaFuel Paymaster on testnet

**Example:**
```python
result = sdk.set_metadata(
    agent_id=1,
    key="version",
    value="2.0.0"
)
print(f"Metadata set: {result['transactionHash']}")
```

---

### Utility Methods

#### `parse_agent_uri(agent_uri)` (static)

Parse agent URI to JSON.

Supports multiple URI formats:
- Base64 data URI: `data:application/json;base64,...`
- HTTP/HTTPS URL: `https://...`

**Parameters:**
- `agent_uri` (str): The agent URI string

**Returns:**
- `dict | None`: Parsed JSON dictionary, or `None` if parsing fails

**Example:**
```python
info = sdk.get_agent_info(agent_id=1)
agent_data = sdk.parse_agent_uri(info['agentURI'])
if agent_data:
    print(f"Agent name: {agent_data['name']}")
    print(f"Description: {agent_data['description']}")
    print(f"Services: {agent_data.get('services', [])}")
```

---

### Properties

| Property | Type | Description |
|----------|------|-------------|
| `wallet_address` | `str` | The wallet address |
| `contract_address` | `str` | The Identity Registry contract address |
| `network` | `dict` | Network configuration dictionary |

---

## EVMWalletProvider

Wallet provider for EVM-compatible private key wallets.

### Constructor

```python
EVMWalletProvider(
    password: str,
    private_key: Optional[str] = None,
    state_file: str = ".bnbagent_state",
    debug: bool = False
)
```

**Parameters:**
- `password` (str): Password for encrypting/decrypting the wallet (required)
- `private_key` (str, optional): Private key to import. If not provided, generates new key or loads from state file
- `state_file` (str): Path to state file (default: `.bnbagent_state`)
- `debug` (bool): Enable debug logging

**Example:**
```python
# Create new wallet (or load existing)
wallet = EVMWalletProvider(password="secure-password")

# Import existing private key
wallet = EVMWalletProvider(
    password="secure-password",
    private_key="0x..."
)
```

### Methods

#### `export_keystore()`

Export wallet as Keystore V3 JSON.

**Returns:**
- `dict`: Keystore JSON object (compatible with MetaMask/Geth)

#### `export_private_key()`

Export raw private key (use with caution).

**Returns:**
- `str`: Private key with `0x` prefix

### Properties

| Property | Type | Description |
|----------|------|-------------|
| `address` | `str` | The wallet address |

---

## AgentEndpoint

Data class for agent endpoint configuration.

### Constructor

```python
AgentEndpoint(
    name: str,
    endpoint: str,
    version: Optional[str] = None,
    capabilities: Optional[List[str]] = None
)
```

**Parameters:**
- `name` (str): Endpoint name (e.g., "A2A", "MCP", "OASF", "web")
- `endpoint` (str): Endpoint URL (must start with `http://` or `https://`)
- `version` (str, optional): Protocol version
- `capabilities` (list[str], optional): List of capabilities

**Raises:**
- `ValueError`: If name or endpoint is empty, or endpoint is not a valid URL

**Example:**
```python
from bnbagent import AgentEndpoint

# A2A endpoint
a2a = AgentEndpoint(
    name="A2A",
    endpoint="https://agent.example/.well-known/agent-card.json",
    version="0.3.0"
)

# MCP endpoint
mcp = AgentEndpoint(
    name="MCP",
    endpoint="https://mcp.agent.example/",
    version="2025-06-18",
    capabilities=["tools", "prompts"]
)

# Web endpoint
web = AgentEndpoint(
    name="web",
    endpoint="https://agent.example/"
)
```

### Methods

#### `to_dict()`

Convert endpoint to dictionary (omits None values).

**Returns:**
- `dict`: Endpoint as dictionary

#### `from_dict(data)` (class method)

Create endpoint from dictionary.

**Parameters:**
- `data` (dict): Dictionary with `name` and `endpoint` fields

**Returns:**
- `AgentEndpoint`: New endpoint instance

**Raises:**
- `ValueError`: If required fields are missing
