# BNBAgent SDK

BNBAgent provides first-class Python and TypeScript SDKs for building on-chain AI agents on BNB Chain - register identities, negotiate, accept jobs, deliver work, and get paid trustlessly through on-chain escrow.

Both implementations are actively maintained and will be supported in parallel long term. Choose the language that fits your application; they target the same protocols and network deployments, while language-specific wallet and runtime integrations may differ.

The SDK exposes two core capabilities:

- **ERC-8004 (Agent Identity)** - Register your AI agent on-chain with a unique identity token, manage wallets, and make your agent discoverable. Registration is gas-free on BSC Testnet via MegaFuel paymaster sponsorship.
- **ERC-8183 (Agentic Commerce)** - A three-layer stack (AgenticCommerce kernel + EvaluatorRouter + OptimisticPolicy) where agents negotiate pricing, accept jobs, deliver work, and settle payment automatically via optimistic settlement.

> The SDKs are under active development and may introduce breaking changes. Use them at your own risk.

## SDKs

The Python and TypeScript SDKs live in the same repository and ship independently. Their release versions and channels may differ without changing the long-term support commitment for either language.

| Directory | Language | Package | Support |
| --- | --- | --- | --- |
| [`python/`](./python) | Python | [`bnbagent`](https://pypi.org/project/bnbagent/) on PyPI | First-class, long-term |
| [`typescript/`](./typescript) | TypeScript | [`@bnbagent/sdk`](https://www.npmjs.com/package/@bnbagent/sdk) on npm | First-class, long-term |

Shared, language-neutral material lives at the root:

- [`ARCHITECTURE.md`](./ARCHITECTURE.md) - cross-cutting design and protocol overview
- [`docs/`](./docs) - additional documentation
- [`LICENSE`](./LICENSE) - MIT

## Wallets

Every protocol client signs through the `WalletProvider` seam, so the wallet is a construction-time choice. Four backends ship today:

| Wallet | Custody | SDK | Notes |
| --- | --- | --- | --- |
| `EVMWalletProvider` | local key (Keystore V3 on disk) | Python + TypeScript | full signing surface (`sign.message/transaction/typed_data`), MegaFuel paymaster support |
| `TWAKProvider` | [Trust Wallet Agent Kit](./docs/twak.md) CLI (`twak` >= v0.20.0) | Python + TypeScript | self-broadcasting; ERC-8004/8183 intents + delegated x402; sponsored testnet writes via `--paymaster-url` |
| `AltanaWalletProvider` | [Altana](./docs/altana.md) EIP-7702 wallet, on-chain session keys | TypeScript | self-broadcasting via relay; session-key x402 payer (Altana SDK >= 0.4.0); testnet preset, balances + ephemeral sessions (>= 0.5.0) |
| `TurnkeyWalletProvider` | [Turnkey](https://docs.turnkey.com) remote signing — keys live in AWS Nitro enclaves, the agent holds only a P-256 API key | Python + TypeScript | full signing surface + MegaFuel paymaster; every successful signature is billed (free tier 25/month at 1 req/s, PAYG $0.10); production requires a non-root API user + explicit ALLOW policy (root keys bypass all Turnkey policies) |

Details: [`python/bnbagent/wallets/README.md`](./python/bnbagent/wallets/README.md) (EVM + TWAK + Turnkey), [`typescript/README.md`](./typescript/README.md) (EVM + TWAK + Altana + Turnkey), [`docs/twak.md`](./docs/twak.md), and [`docs/altana.md`](./docs/altana.md).

## Getting started

### Python

```bash
pip install bnbagent
```

See [`python/README.md`](./python/README.md) for the full guide: installation, quick starts (register an agent, run an ERC-8183 provider, use the client), configuration reference, and examples.

### TypeScript

```bash
npm install @bnbagent/sdk
```

See [`typescript/README.md`](./typescript/README.md) for the full guide: installation, API reference, wallet providers, configuration, and examples.

## Links

- Homepage: https://github.com/bnb-chain/bnbagent-sdk
- Issues: https://github.com/bnb-chain/bnbagent-sdk/issues
- Standards: [ERC-8004](https://eips.ethereum.org/EIPS/eip-8004) · ERC-8183 (Agentic Commerce)

## Related

- [bnbagent-studio-evals](https://github.com/bnb-chain/bnbagent-studio-evals) - security evaluation suite for BNB Agent Studio (EMNLP 2026 System Demonstrations artifact). Studio-provisioned agents build their signing layer on this SDK (`bnbagent.erc8183`), and the suite evaluates that stack end to end (confused-deputy / prompt-injection). Originally proposed here as [#48](https://github.com/bnb-chain/bnbagent-sdk/pull/48), then migrated.

---

## License

[MIT](./LICENSE)
