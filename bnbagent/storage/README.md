# Storage

## Overview

The `storage` module provides a pluggable off-chain storage interface for the
bnbagent SDK. On-chain contracts store only content hashes; full data (service
records, deliverables, metadata) lives off-chain. Implementations handle
upload, download, and existence checks through a unified async API.

## Key Concepts

- **StorageProvider interface** -- an async abstract base class with three
  methods: `upload()`, `download()`, and `exists()`. A static
  `compute_hash()` helper produces `keccak256` digests for on-chain
  verification.
- **StorageConfig** -- a dataclass that centralizes storage settings. Use
  `StorageConfig.from_env()` to load from environment variables, then pass
  the config to `create_storage_provider()` to get the right provider.
- **Local vs IPFS** -- `LocalStorageProvider` writes JSON files to disk
  (`file://` URLs) for development. `IPFSStorageProvider` pins JSON via an
  HTTP API (Pinata-compatible) and returns `ipfs://` URLs for production.
- **Sync bridge** -- both providers offer a `save_sync()` convenience method
  for callers that are not in an async context.

## Quick Start

```python
from bnbagent.storage import StorageConfig, create_storage_provider

# From environment variables
config = StorageConfig.from_env()
storage = create_storage_provider(config)

# Manual -- local storage
from bnbagent.storage import LocalStorageProvider
storage = LocalStorageProvider("./my-data")

# Manual -- IPFS storage
config = StorageConfig(type="ipfs", api_key="your-pinata-jwt")
storage = create_storage_provider(config)
```

## API Reference

### `StorageProvider` (ABC)

Async abstract base class for all storage backends.

| Method | Description |
|---|---|
| `async upload(data, filename=None)` | Upload JSON dict. Returns a URL (`file://`, `ipfs://`). |
| `async download(url)` | Download and parse JSON from a URL. |
| `async exists(url)` | Check whether data at the URL exists. |
| `compute_hash(data)` (static) | `keccak256` of canonical JSON for on-chain verification. |
| `compute_content_hash(content)` (static) | `keccak256` of a raw string. |

### `StorageConfig`

Dataclass for storage configuration.

| Field | Type | Default | Description |
|---|---|---|---|
| `type` | `str` | `"local"` | Provider type: `"local"` or `"ipfs"`. |
| `base_dir` | `str` | `".agent-data"` | Local storage directory. |
| `api_key` | `str \| None` | `None` | Pinning service API key / JWT. |
| `api_url` | `str \| None` | `None` | Pinning API URL. |
| `gateway_url` | `str \| None` | `None` | IPFS gateway URL. |

### `create_storage_provider(config: StorageConfig)`

Factory function. Accepts a `StorageConfig` (required) and returns the
appropriate `StorageProvider` implementation.

### `LocalStorageProvider`

File-system storage for development and testing. Writes canonical JSON to
`base_dir` with restricted permissions (`0o600`). Path traversal is blocked.

### `IPFSStorageProvider`

IPFS pinning via HTTP API (Pinata, Infura, Web3.Storage). Requires `httpx`.

| Method | Description |
|---|---|
| `__init__(pinning_api_url, pinning_api_key, gateway_url)` | Create an IPFS provider. |
| `get_gateway_url(ipfs_url)` | Convert an `ipfs://` URL to an HTTP gateway URL. |
| `save_sync(data, filename)` | Synchronous upload for non-async callers. |

## Content Hashing

On-chain contracts store only a `keccak256` hash for verification:

```python
data = {"job": {"id": 42}, "result": "done"}
content_hash = StorageProvider.compute_hash(data)   # bytes32
```

The hash is computed over canonical JSON (`sort_keys=True`,
`separators=(",",":")`) to ensure deterministic output regardless of dict
ordering.

## Configuration

`StorageConfig.from_env()` reads the following environment variables:

| Variable | Description | Default |
|---|---|---|
| `STORAGE_PROVIDER` | `"local"` or `"ipfs"` | `"local"` |
| `STORAGE_LOCAL_PATH` | Directory for local storage | `".agent-data"` |
| `STORAGE_API_KEY` | Pinning API key (fallback: `PINATA_JWT`) | -- |
| `STORAGE_API_URL` | Storage API URL | -- |
| `STORAGE_GATEWAY_URL` | IPFS gateway URL (fallback: `PINATA_GATEWAY`) | -- |

## Related

- [`apex`](../apex/README.md) -- uses `StorageProvider` for service record persistence.
- [`core`](../core/README.md) -- module system and shared infrastructure.
