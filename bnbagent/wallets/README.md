# Wallets

## Overview

The `wallets` module provides a pluggable wallet provider interface for
transaction signing in the bnbagent SDK. All protocol modules and config
objects accept a `WalletProvider` instance, making it straightforward to swap
between different signing backends without changing application code.

As of v0.2.0, `WalletProvider` is the **primary** way to configure signing
across the SDK. Both `BNBAgentConfig` and `APEXConfig` accept
`wallet_provider=` directly, or auto-wrap `private_key` + `wallet_password`
into an `EVMWalletProvider` at construction time (clearing the plaintext key
immediately).

## Key Concepts

- **WalletProvider interface** -- an abstract base class (`ABC`) defining
  three operations: `address` (property), `sign_transaction()`, and
  `sign_message()`. Any new signing backend only needs to implement these.
- **Keystore V3 encryption** -- `EVMWalletProvider` stores private keys
  encrypted using the standard Ethereum Keystore V3 format (scrypt KDF +
  AES-128-CTR), compatible with MetaMask and Geth.
- **In-memory mode** -- `EVMWalletProvider(persist=False)` creates a wallet
  in memory without writing to disk. Used internally when configs auto-wrap
  a `private_key` + `wallet_password` pair.
- **Auto-creation** -- when no private key is supplied and `persist=True`,
  `EVMWalletProvider` generates a new keypair and persists the encrypted
  keystore automatically.

## Quick Start

```python
from bnbagent.wallets import EVMWalletProvider

# Import an existing private key (encrypted + persisted to disk)
wallet = EVMWalletProvider(password="secure-pw", private_key="0x...")
print(wallet.address)

# In-memory only (no disk I/O — used by config auto-wrap)
wallet = EVMWalletProvider(password="pw", private_key="0x...", persist=False)

# Auto-generate a new wallet (persisted to .bnbagent_state)
wallet = EVMWalletProvider(password="secure-pw")
```

## API Reference

### `WalletProvider` (ABC)

Abstract base class all wallet providers must implement.

| Member | Description |
|---|---|
| `address` (property) | Wallet's Ethereum address. |
| `sign_transaction(tx)` | Sign a transaction dict. Returns `rawTransaction`, `hash`, `r`, `s`, `v`. |
| `sign_message(msg)` | EIP-191 personal sign. Returns `messageHash`, `r`, `s`, `v`, `signature`. |

### `EVMWalletProvider`

Production wallet provider backed by a local private key with Keystore V3
encryption.

| Method | Description |
|---|---|
| `__init__(password, private_key=None, persist=True)` | Import a key or load/create an encrypted wallet. |
| `export_private_key()` | Return the hex private key (handle with care). |
| `export_keystore()` | Return the Keystore V3 JSON dict. |
| `get_wallet_info()` | Return `{"address": "0x..."}` (no secrets). |

Constructor behavior:
1. If `private_key` is provided: import and encrypt it (save to disk only if `persist=True`).
2. If `persist=True` and no key: load existing keystore from state file, or create a new wallet.
3. If `persist=False` and no key: raises `ValueError` (key is required for in-memory mode).

### `MPCWalletProvider` (stub)

Placeholder for future MPC (Multi-Party Computation) wallet support.
Raises `NotImplementedError` on instantiation.

## Config Auto-Wrap

Both `BNBAgentConfig` and `APEXConfig` support a convenience pattern:

```python
from bnbagent.apex.config import APEXConfig
from bnbagent.wallets import EVMWalletProvider

# These are equivalent:
config = APEXConfig(
    wallet_provider=EVMWalletProvider(password="pw", private_key="0x...", persist=False)
)

config = APEXConfig(private_key="0x...", wallet_password="pw")
# -> __post_init__ auto-wraps into EVMWalletProvider(persist=False)
# -> private_key is cleared to "" (no plaintext retained)
```

The `from_env()` class methods read `PRIVATE_KEY` + `WALLET_PASSWORD` from
environment variables and perform the same auto-wrap.

## Implementing a Custom Provider

Subclass `WalletProvider` and implement the three required members:

```python
from bnbagent.wallets.wallet_provider import WalletProvider

class HardwareWalletProvider(WalletProvider):
    @property
    def address(self) -> str:
        return self._hw_address

    def sign_transaction(self, transaction: dict) -> dict:
        ...  # Delegate to hardware device

    def sign_message(self, message: str) -> dict:
        ...  # Delegate to hardware device
```

## Security Notes

- Private keys are **never** stored in plain text by `EVMWalletProvider`.
  Legacy plain-text state files are automatically migrated to Keystore V3
  on first load.
- Keystore files are saved with `0o600` permissions (owner read/write only).
- `export_private_key()` logs a warning -- avoid calling it in production.
- Config objects (`APEXConfig`, `BNBAgentConfig`) clear the `private_key`
  field to `""` immediately after wrapping into a `WalletProvider`. No
  plaintext private key is retained in config objects.

## Related

- [`erc8004`](../erc8004/README.md) -- uses `WalletProvider` for agent registration.
- [`apex`](../apex/README.md) -- uses `WalletProvider` via `APEXConfig` for job transactions.
- [`core`](../core/README.md) -- `ContractClientMixin` delegates signing to `WalletProvider`.
