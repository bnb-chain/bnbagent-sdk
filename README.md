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
| `typescript/` | TypeScript | 🚧 Planned | — |

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

## Links

- Homepage: https://github.com/bnb-chain/bnbagent-sdk
- Issues: https://github.com/bnb-chain/bnbagent-sdk/issues
- Standards: [ERC-8004](https://eips.ethereum.org/EIPS/eip-8004) · ERC-8183 (Agentic Commerce)

## License

[MIT](./LICENSE)
