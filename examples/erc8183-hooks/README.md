# ERC-8183 Hook Contracts

Production-ready hook contracts for ERC-8183 Agentic Commerce. These hooks add trust, safety, and attestation layers to the job lifecycle.

## Hooks

### 1. MutualAttestationHook — Airbnb-Style Bilateral Reviews

Both client **and** provider leave on-chain reviews (via EAS) after job completion, building two-sided reputation.

**Why it matters:** One-sided reviews create incentive problems. Clients can post vague specs without accountability. Providers can deliver poor work while blaming the spec. Mutual reviews fix this.

**Flow:**
```
Job completes → 7-day review window opens
  → Client rates provider (1-5 stars + comment) → EAS attestation
  → Provider rates client (1-5 stars + comment) → EAS attestation
  → Both done? → MutualReviewComplete event
```

**Features:**
- Only job participants can review (enforced on-chain)
- One review per party per job
- Non-revocable EAS attestations — reviews are permanent facts
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

## EAS Deployment Addresses

| Chain | EAS Contract | Status |
|-------|-------------|--------|
| Base | `0x4200000000000000000000000000000000000021` | ✅ Official |
| Ethereum | `0xA1207F3BBa224E2c9c3c6D5aF63D0eb1582Ce587` | ✅ Official |
| Optimism | `0x4200000000000000000000000000000000000021` | ✅ Official |
| Arbitrum | See [EAS docs](https://docs.attest.org/docs/quick--start/contracts) | ✅ Official |
| **BSC / BSC Testnet** | **Not yet deployed** | ⚠️ Requires self-deploy |

> **BSC Note:** EAS does not have an official deployment on BNB Chain yet. The `AttestationHook` and `MutualAttestationHook` contracts are EAS-dependent — to use them on BSC, you would need to deploy the [EAS contracts](https://github.com/ethereum-attestation-service/eas-contracts) yourself. The `TokenSafetyHook` works independently of EAS and is ready to use on any EVM chain.

## Testing

```bash
cd contracts
forge install
forge test -vv
```

## Related

- [TrustEvaluator](../trust-evaluator/) — Trust-based fast-path evaluator
- [ERC-8183 spec](https://eips.ethereum.org/EIPS/eip-8183)
- [EAS (Ethereum Attestation Service)](https://docs.attest.org)

## License

MIT
