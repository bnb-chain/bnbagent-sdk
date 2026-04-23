# Core

## Overview

The `core` module contains the internal infrastructure shared across all
bnbagent protocol modules. It provides nonce management, paymaster
integration, a reusable contract transaction mixin, the plugin module system,
and ABI loading utilities.

Exception types are defined in the top-level `bnbagent.exceptions` module and
re-exported from `bnbagent.core` for convenience.

## Key Concepts

- **NonceManager** -- thread-safe singleton that seeds from the chain on
  first use (`pending` block), then increments locally. Automatically
  re-syncs when a nonce-related RPC error is detected. Keyed by
  `(rpc_url, account)` so multiple clients sharing a wallet share one
  manager.
- **Paymaster** -- ERC-4337 paymaster client for sponsoring gas fees via
  services like MegaFuel. Exposes `isSponsorable()`,
  `eth_sendRawTransaction()`, and `eth_getTransactionCount()`.
- **ContractClientMixin** -- mixin class that encapsulates build-sign-send
  logic with automatic nonce management and exponential-backoff retry on
  rate-limit and nonce errors. Prefers `WalletProvider.sign_transaction()`
  when available, falls back to raw `private_key` signing. Used by
  `APEXClient`, `CommerceClient`, `RouterClient`, and `PolicyClient`.
- **ModuleRegistry** -- discovers, validates dependencies, and initializes
  `BNBAgentModule` plugins. Supports built-in modules, explicit
  registration, and `pyproject.toml` entry points.
- **BNBAgentModule** -- abstract base class for protocol modules with a
  well-defined lifecycle: `__init__` -> `initialize(config)` ->
  `get_actions()` -> `shutdown()`.

## API Reference

### `NonceManager`

| Method | Description |
|---|---|
| `NonceManager.for_account(w3, account)` | Get or create a singleton for this wallet + RPC. |
| `get_nonce()` | Return the next nonce (thread-safe). |
| `handle_error(error, used_nonce)` | Re-sync from chain if nonce error; returns `True` to retry. |
| `reset()` | Force re-seed on next `get_nonce()` call. |

### `Paymaster`

| Method | Description |
|---|---|
| `__init__(paymaster_url, debug=False)` | Create a paymaster client. |
| `isSponsorable(tx)` | Check if a transaction qualifies for gas sponsorship. |
| `eth_sendRawTransaction(signed_tx)` | Send a signed tx through the paymaster. |
| `eth_getTransactionCount(address)` | Get nonce via the paymaster RPC. |

### `ContractClientMixin`

Mixin for web3 contract clients. Subclasses set `self.w3`,
`self._wallet_provider`, and `self._account`. A `None` wallet provider
yields a read-only client (writes raise `RuntimeError`).

Signing flows exclusively through `wallet_provider.sign_transaction()` —
raw private keys are never accepted at this layer.

| Method | Description |
|---|---|
| `_send_tx(fn, value=0, gas=2_000_000, skip_preflight=False)` | Build, sign, and send a write transaction with retry. Includes pre-flight `eth_call` simulation (surfaces revert reasons before spending gas), dynamic gas price with 20% buffer, and on-chain revert detection (`receipt.status == 0`). Pass `skip_preflight=True` when the node returns opaque `0x` reverts. |
| `_call_with_retry(fn)` | Execute a read-only contract call with rate-limit retry. |

### `ModuleRegistry`

| Method | Description |
|---|---|
| `register(module)` | Explicitly register a `BNBAgentModule`. |
| `discover()` | Auto-discover built-in and entry-point modules. |
| `initialize_all(config)` | Initialize all modules in dependency order. |
| `get(name)` | Retrieve a registered module by name. |
| `list_modules()` | Return `ModuleInfo` for all registered modules. |
| `shutdown_all()` | Shut down all modules in reverse order. |

### `BNBAgentModule` (ABC)

| Method | Description |
|---|---|
| `info()` | Return `ModuleInfo(name, version, description, dependencies)`. |
| `default_config()` | Return default config dict (lazy, calls `resolve_network()`). |
| `initialize(config, **kwargs)` | Receive merged config and shared infra. |
| `get_actions()` | Return AI-invocable action descriptors (reserved). |
| `shutdown()` | Clean up resources. |

## Transaction Retry Strategy

`ContractClientMixin._send_tx()` uses the following retry policy:

| Constant | Default | Description |
|---|---|---|
| `MAX_RETRIES` | `5` | Maximum send attempts per transaction. |
| `RETRY_BASE_DELAY` | `1.0` s | Base delay for exponential backoff on rate limits. |

Nonce errors trigger an immediate re-sync from the chain and retry. Rate
limits (`429` / `"too many requests"`) use exponential backoff
(`base * 2^attempt`). All other errors are raised immediately.

## Third-Party Module Entry Points

Third-party modules can register via `pyproject.toml`:

```toml
[project.entry-points."bnbagent.modules"]
my_module = "my_package:create_module"
```

`ModuleRegistry.discover()` will find and register the module automatically.

## Related

- [`erc8004`](../erc8004/README.md) -- ERC-8004 module, uses `Paymaster` and `ModuleRegistry`.
- [`apex`](../apex/README.md) -- APEX module, uses `ContractClientMixin` and `NonceManager`.
- [`wallets`](../wallets/README.md) -- `ContractClientMixin` delegates signing to `WalletProvider`.
- [`storage`](../storage/README.md) -- off-chain storage used alongside core infra.
