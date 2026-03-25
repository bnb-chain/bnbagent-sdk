# ERC-8183 Hook Contracts

Production-ready hook contracts for ERC-8183 Agentic Commerce. These hooks add trust, safety, and attestation layers to the job lifecycle using [BAS (BNB Attestation Service)](https://bascan.io) for on-chain attestations.

## Hooks

### 1. MutualAttestationHook — Airbnb-Style Bilateral Reviews

Both client **and** provider leave on-chain reviews (via BAS) after job completion, building two-sided reputation.

**Why it matters:** One-sided reviews create incentive problems. Clients can post vague specs without accountability. Providers can deliver poor work while blaming the spec. Mutual reviews fix this.

**Flow:**
```
Job completes → 7-day review window opens
  → Client rates provider (1-5 stars + comment) → BAS attestation
  → Provider rates client (1-5 stars + comment) → BAS attestation
  → Both done? → MutualReviewComplete event
```

**Features:**
- Only job participants can review (enforced on-chain)
- One review per party per job
- Non-revocable BAS attestations — reviews are permanent facts
- Configurable review window (default 7 days)
- Works for both completed and rejected jobs

### 2. TokenSafetyHook — Pre-Funding Token Verification

Checks ERC-20 tokens for honeypot/rug indicators **before** a job is funded, preventing providers from accepting payment in worthless tokens.

**Why it matters:** ERC-8183 jobs are funded with arbitrary ERC-20 tokens. A malicious client could fund a job with a honeypot token that can't be sold after the provider receives it.

**Flow:**
```
Client funds job → beforeAction hook triggers
  → Query token safety oracle (honeypot check, tax check, liquidity check)
  → Oracle says unsafe? → Revert funding
  → Oracle says safe? → Allow funding to proceed
```

**Features:**
- Pluggable oracle interface (`ITokenSafetyOracle`)
- Owner can override with token whitelist
- Pairable with any token safety API (GoPlus, Honeypot.is, etc.)
- Upgradeable (UUPS pattern)

## Architecture

These hooks plug into ERC-8183's `beforeAction` / `afterAction` callback system:

```
Client/Provider action
    │
    ▼
ERC-8183 Contract
    │
    ├── beforeAction(jobId, selector, data) → Hook validates
    │                                          ↳ revert = block action
    │
    ├── [execute action]
    │
    └── afterAction(jobId, selector, data) → Hook records state
                                              ↳ e.g., record completion for reviews
```

## BAS (BNB Attestation Service) Deployment Addresses

These hooks use [BAS](https://github.com/bnb-attestation-service/bas-contract) — BNB Chain's native attestation service (EAS-compatible fork with the same `IEAS` interface).

| Chain | BAS Contract | Schema Registry |
|-------|-------------|-----------------|
| **BSC Mainnet** | `0x247Fe62d887bc9410c3848DF2f322e52DA9a51bC` | `0x5e905F77f59491F03eBB78c204986aaDEB0C6bDa` |
| **BSC Testnet** | `0x6c2270298b1e6046898a322acB3Cbad6F99f7CBD` | `0x08C8b8417313fF130526862f90cd822B55002D72` |
| **opBNB Mainnet** | `0x5e905F77f59491F03eBB78c204986aaDEB0C6bDa` | `0x65CFBDf1EA0ACb7492Ecc1610cfBf79665DC631B` |
| **opBNB Testnet** | `0x5e905F77f59491F03eBB78c204986aaDEB0C6bDa` | `0x65CFBDf1EA0ACb7492Ecc1610cfBf79665DC631B` |

> **Cross-chain note:** For Base/Ethereum/Optimism deployments, use [EAS](https://docs.attest.org/docs/quick--start/contracts) instead (same interface). The `TokenSafetyHook` works independently on any EVM chain without BAS/EAS.

## Testing

```bash
cd contracts
forge install
forge test -vv
```

## Related

- [TrustEvaluator](../trust-evaluator/) — Trust-based fast-path evaluator
- [ERC-8183 spec](https://eips.ethereum.org/EIPS/eip-8183)
- [BAS (BNB Attestation Service)](https://github.com/bnb-attestation-service/bas-contract)
- [BAS Explorer](https://bascan.io)

## License

MIT
