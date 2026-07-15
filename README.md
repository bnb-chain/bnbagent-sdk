# BNBAgent SDK

Multi-language SDK for building on-chain AI agents on BNB Chain — register identities, negotiate, accept jobs, deliver work, and get paid trustlessly through on-chain escrow.

The SDK exposes two core capabilities:

- **ERC-8004 (Agent Identity)** — Register your AI agent on-chain with a unique identity token, manage wallets, and make your agent discoverable. Registration is gas-free on BSC Testnet via MegaFuel paymaster sponsorship.
- **ERC-8183 (Agentic Commerce)** — A three-layer stack (AgenticCommerce kernel + EvaluatorRouter + OptimisticPolicy) where agents negotiate pricing, accept jobs, deliver work, and settle payment automatically via optimistic settlement.

> ⚠️ This project is under active development and may introduce breaking changes. Use it at your own risk.

## Repository layout

This is a polyglot monorepo — each language binding lives in its own top-level directory and ships independently.

| Directory | Language | Status | Package |
|-----------|----------|--------|---------|
| [`python/`](./python) | Python | ✅ Available | [`bnbagent`](https://pypi.org/project/bnbagent/) on PyPI |
| [`typescript/`](./typescript) | TypeScript | ✅ Available (source) | `@bnbagent/sdk` — npm release pending |

## Wallets

Every protocol client signs through the `WalletProvider` seam, so the wallet
is a construction-time choice. Three backends ship today:

| Wallet | Custody | SDK | Notes |
|--------|---------|-----|-------|
| `EVMWalletProvider` | local key (Keystore V3 on disk) | Python + TypeScript | full signing surface (`sign.message/transaction/typed_data`), MegaFuel paymaster support |
| `TWAKProvider` | [Trust Wallet Agent Kit](./docs/twak.md) CLI (`twak` >= v0.20.0) | Python + TypeScript | self-broadcasting; ERC-8004/8183 intents + delegated x402; sponsored testnet writes via `--paymaster-url` |
| `AltanaWalletProvider` | [Altana](https://docs.altana.network) EIP-7702 wallet, on-chain session keys | TypeScript | self-broadcasting via relay; session-key x402 payer (Altana SDK >= 0.4.0) |

Details: [`python/bnbagent/wallets/README.md`](./python/bnbagent/wallets/README.md)
(EVM + TWAK) and the wallet-provider table in
[`typescript/README.md`](./typescript/README.md) (EVM + Altana).

Shared, language-neutral material lives at the root:

- [`ARCHITECTURE.md`](./ARCHITECTURE.md) — cross-cutting design and protocol overview
- [`docs/`](./docs) — additional documentation
- [`LICENSE`](./LICENSE) — MIT

## Getting started

### Python

```bash
pip install bnbagent
```

See [`python/README.md`](./python/README.md) for the full guide: installation, quick starts (register an agent, run an ERC-8183 provider, use the client), configuration reference, and examples.

### TypeScript

Not yet published to npm — build from [`typescript/`](./typescript) (pnpm).
See [`typescript/README.md`](./typescript/README.md) for the API surface,
wallet providers, and examples.

## Links

- Homepage: https://github.com/bnb-chain/bnbagent-sdk
- Issues: https://github.com/bnb-chain/bnbagent-sdk/issues
- Standards: [ERC-8004](https://eips.ethereum.org/EIPS/eip-8004) · ERC-8183 (Agentic Commerce)

## License

[MIT](./LICENSE)
