# BNBAgent SDK

Python SDK for ERC-8004 on-chain agent registration and management.

## Features

- **Agent Registration**: Register AI agents on-chain via ERC-8004 Identity Registry
- **Agent Discovery**: Query and browse registered agents
- **Zero Gas Fees**: MegaFuel Paymaster sponsorship for contract operations
- **Keystore Encryption**: Password-protected wallet (Keystore V3, compatible with MetaMask/Geth)
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

> **Requirements:** Python 3.10+ and `uv` (recommended) or `pip`

```bash
pip install git+https://github.com/bnb-chain/bnbagent-sdk.git
```

For development:

```bash
git clone https://github.com/bnb-chain/bnbagent-sdk.git
cd bnbagent-sdk
uv sync --extra dev  # or: pip install -e ".[dev]"
```

## Quick Start

```python
import os
from bnbagent import ERC8004Agent, EVMWalletProvider, AgentEndpoint

# Create wallet and SDK
wallet = EVMWalletProvider(password=os.getenv("WALLET_PASSWORD"))
sdk = ERC8004Agent(wallet_provider=wallet, network="bsc-testnet")

# Generate and register agent
agent_uri = sdk.generate_agent_uri(
    name="My Agent",
    description="A test agent",
    endpoints=[
        AgentEndpoint(
            name="A2A",
            endpoint="https://agent.example/.well-known/agent-card.json",
            version="0.3.0"
        )
    ]
)

result = sdk.register_agent(agent_uri=agent_uri)
print(f"Agent registered with ID: {result['agentId']}")
```

## Core Operations

### Register Agent

```python
result = sdk.register_agent(agent_uri=agent_uri)
# Returns: {agentId, transactionHash, agentURI, ...}
```

### Discover Agents

```python
# List agents with pagination
agents = sdk.get_all_agents(limit=10, offset=0)
for agent in agents['items']:
    print(f"#{agent['token_id']}: {agent['name']}")

# Get single agent info (on-chain)
info = sdk.get_agent_info(agent_id=1)
```

### Update Agent

```python
new_uri = sdk.generate_agent_uri(
    name="Updated Agent",
    description="New description",
    endpoints=[...],
    agent_id=1  # Include for registrations field
)
sdk.set_agent_uri(agent_id=1, agent_uri=new_uri)
```

### Metadata

```python
# Get/set metadata
version = sdk.get_metadata(agent_id=1, key="version")
sdk.set_metadata(agent_id=1, key="version", value="2.0.0")
```

## Examples

See [`examples/testnet_usage.py`](examples/testnet_usage.py) for a complete working example including:
- Wallet creation and management
- Agent registration
- Agent discovery and querying
- Metadata operations

## Documentation

- **[API Reference](bnbagent/README.md)** - Complete API documentation with workflow examples

## Security

The SDK stores encrypted wallet state in `.bnbagent_state`:

- **Encryption**: AES-128-CTR with scrypt key derivation (Keystore V3)
- **File permissions**: `0o600` (owner read/write only)
- **Format**: Compatible with MetaMask/Geth keystore

### Best Practices

1. **Never commit secrets**: Add `.bnbagent_state` to `.gitignore`
2. **Use environment variables**: Store `WALLET_PASSWORD` in env, not in code
3. **Backup your wallet**: Export keystore JSON and store securely

```python
# Export wallet for backup
keystore = wallet.export_keystore()
with open("backup-wallet.json", "w") as f:
    json.dump(keystore, f)
```

### Environment Variables

| Variable | Description |
|----------|-------------|
| `WALLET_PASSWORD` | Password for wallet encryption/decryption |

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

## Development

### Running Tests

```bash
uv run pytest              # Run tests
uv run pytest --cov=bnbagent  # With coverage
```

### Contributing

1. Follow existing code patterns
2. Include error handling
3. Add tests for new features
4. Run tests before submitting

## License

This SDK is part of the ERC-8004 implementation project.
