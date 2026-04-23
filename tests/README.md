# Test Suite

Unit tests for the BNBAgent SDK. All tests use `pytest` with `unittest.mock` — no live blockchain connection required.

## Running Tests

```bash
# Install dev dependencies
uv sync --extra dev

# Run all tests
pytest

# Run specific file
pytest tests/test_apex_client.py

# Verbose output
pytest -v
```

## Test Files

| File | Module Under Test |
|------|-------------------|
| `test_sdk.py` | `ERC8004Agent` — registration, discovery, URI parsing, metadata |
| `test_agent_uri.py` | `AgentURIGenerator` — URI generation and base64 encoding |
| `test_models.py` | `AgentEndpoint` — validation and serialization |
| `test_wallet.py` | `EVMWalletProvider` — keystore encryption, signing |
| `test_apex_client.py` | `APEXClient` — facade construction, approve_floor strategy, delegation |
| `test_apex_config.py` | `APEXConfig` — validation, env var loading |
| `test_apex_job_ops.py` | `APEXJobOps` — async verify / submit / auto-settle |
| `test_negotiation.py` | `NegotiationHandler` — terms, hashing, price validation |
| `test_service_record.py` | `ServiceRecord` — serialization, canonical JSON, hash computation |
| `test_nonce_manager.py` | `NonceManager` — singleton, thread safety, error recovery |
| `test_paymaster.py` | `Paymaster` — RPC helpers, sponsorability checks |
| `test_local_storage.py` | `LocalStorageProvider` — file I/O, permissions, path traversal |
| `test_ipfs_storage.py` | `IPFSStorageProvider` — Pinata upload, CID validation, gateway |
| `test_storage_factory.py` | `create_storage_provider` — factory selection, env config |
| `test_module_system.py` | `BNBAgentModule` / `ModuleRegistry` — module composition |
| `test_multicall.py` | `multicall_read` — Multicall3 batch reads |
