# ERC-8004 Identity Registry

## Overview

The `erc8004` module provides a high-level Python interface for the ERC-8004
on-chain agent identity registry on BNB Chain. It handles agent registration,
URI generation, metadata management, and endpoint discovery, so your code can
focus on agent logic rather than contract plumbing.

## Key Concepts

- **Agent registration** -- publish an agent identity on-chain with a single
  `register_agent()` call. The SDK builds the transaction, manages nonces,
  and optionally uses a paymaster for gasless registration.
- **Agent URI** -- an EIP-8004 compliant registration file encoded as a
  base64 data URI, containing the agent's name, description, endpoints, and
  on-chain registrations.
- **Endpoints** -- each agent declares one or more protocol endpoints (A2A,
  MCP, web) via `AgentEndpoint` dataclasses stored in the agent URI.
- **Metadata** -- arbitrary key/value strings attached to an agent ID, useful
  for description, version, tags, etc.

## Quick Start

```python
from bnbagent.wallets import EVMWalletProvider
from bnbagent.erc8004 import ERC8004Agent, AgentEndpoint

wallet = EVMWalletProvider(password="secure-pw", private_key="0x...")
sdk = ERC8004Agent(wallet_provider=wallet, network="bsc-testnet")

agent_uri = sdk.generate_agent_uri(
    name="my-agent",
    description="AI agent for document processing",
    endpoints=[
        AgentEndpoint(
            name="A2A",
            endpoint="https://myagent.example/.well-known/agent-card.json",
            version="0.3.0",
        )
    ],
)
result = sdk.register_agent(agent_uri=agent_uri)
print(f"Agent ID: {result['agentId']}")
```

## API Reference

### `ERC8004Agent`

Main entry point. Requires a `WalletProvider` (no raw private key accepted).

| Method | Description |
|---|---|
| `generate_agent_uri(name, description, endpoints, ...)` | Build an EIP-8004 compliant base64 data URI. |
| `register_agent(agent_uri, metadata=None)` | Register a new agent on-chain. Returns `agentId` and tx hash. |
| `get_agent_info(agent_id)` | Fetch on-chain agent record by numeric ID. |
| `get_all_agents(limit, offset)` | Paginated listing via 8004scan API. |
| `get_metadata(agent_id, key)` | Read a metadata value. |
| `set_metadata(agent_id, key, value)` | Write a metadata value (owner only). |
| `set_agent_uri(agent_id, agent_uri)` | Update the agent URI on-chain. |
| `get_local_agent_info(name)` | Check local state file for a previously registered agent. |
| `parse_agent_uri(agent_uri)` | Decode a base64 data URI or fetch an HTTP URI (with SSRF protection). |
| `wallet_address` | Property -- the connected wallet address. |
| `contract_address` | Property -- the registry contract address. |

### `AgentEndpoint`

Dataclass describing a single protocol endpoint.

| Field | Type | Description |
|---|---|---|
| `name` | `str` | Protocol name (`"A2A"`, `"MCP"`, `"web"`). |
| `endpoint` | `str` | URL (`https://...`). |
| `version` | `str \| None` | Optional protocol version. |
| `capabilities` | `list[str] \| None` | Optional capability tags. |

### `get_erc8004_config(network)`

Lazy configuration function (replaces the former `ERC8004_CONFIG` dict).
Returns a dict with network settings and the registry contract address.
Called at runtime (not import time) so environment variable overrides are
always respected.

### `ContractInterface`

Low-level wrapper around the ERC-8004 registry smart contract. Used
internally by `ERC8004Agent`; prefer the high-level class for most work.

## Architecture

```
ERC8004Agent  (high-level SDK)
  ├── WalletProvider   (signing — required)
  ├── ContractInterface (contract calls)
  │     └── Paymaster  (optional gasless tx)
  └── AgentURIGenerator (URI construction — duck-typed endpoints)
```

## Configuration

Network configuration is resolved lazily via `get_erc8004_config(network)`.
Override with env vars when needed:

| Variable | Description | Default |
|---|---|---|
| `RPC_URL` | JSON-RPC endpoint | Network default |
| `IDENTITY_REGISTRY_ADDRESS` | Registry contract address | Network default |

## Network Support

| Network | Status | Chain ID | Registry Contract |
|---------|--------|----------|-------------------|
| BSC Testnet | **Active** | 97 | `0x8004A818BFB912233c491871b3d84c89A494BD9e` |
| BSC Mainnet | Pending — contracts not yet deployed | 56 | -- |

## Related

- [`wallets`](../wallets/README.md) -- wallet providers used for signing.
- [`core`](../core/README.md) -- paymaster, nonce manager, module system.
- [`apex`](../apex/README.md) -- APEX commerce protocol built on top of agent identities.
