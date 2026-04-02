# Trust-Based ERC-8183 Evaluator

A fast-path evaluator for ERC-8183 Agentic Commerce that uses on-chain trust scores to evaluate jobs in seconds — as an alternative to the UMA optimistic oracle's 30-minute liveness period.

## How it Works

```
Provider submits job → Evaluator reads trust score from oracle
  → Score >= threshold? → complete(jobId)
  → Score < threshold?  → reject(jobId)
  → Provider flagged?   → reject(jobId)
```

Instead of waiting 30 minutes for UMA assertion + liveness, the TrustEvaluator makes an instant decision based on the provider's behavioral reputation:

| Approach | Evaluation Time | Dispute Resolution | Best For |
|----------|----------------|-------------------|----------|
| UMA (APEXEvaluator) | 30 min liveness + 48-72h disputes | DVM token-holder vote | High-value jobs, unknown agents |
| **TrustEvaluator** | **Instant (~1 block)** | Owner-managed threats | Trusted agents, frequent small jobs |

## Architecture

```
┌──────────────────┐     ┌──────────────────┐     ┌──────────────────┐
│  TrustEvaluator  │────▶│  TrustOracle     │     │  ERC-8183        │
│                  │     │  (reputation DB) │     │  (job contract)  │
│  • threshold     │     │                  │     │                  │
│  • threat system │     │  • getUserData() │     │  • complete()    │
│  • access control│     │                  │     │  • reject()      │
└──────────────────┘     └──────────────────┘     └──────────────────┘
```

## Features

- **Instant evaluation** — No waiting for liveness period
- **Trust oracle integration** — Reads provider reputation from on-chain oracle
- **Threat reporting** — Owner can flag malicious providers for auto-reject
- **Access control** — Optional caller restriction + job contract whitelist
- **CEI pattern** — Events emitted before external calls (reentrancy safe)
- **Double-evaluation prevention** — Each (contract, jobId) can only be evaluated once

## Security Considerations

### Caller Restriction

By default, `evaluate()` is permissionless. Enable caller restriction to prevent:
- Front-running of evaluation transactions
- Grief attacks (triggering evaluation during unfavorable oracle states)

```solidity
evaluator.setCallerRestriction(true);
evaluator.setAllowedCaller(automationAddress, true);
```

### Job Contract Whitelist

Prevent attackers from deploying malicious ERC-8183 contracts that return forged `getJob()` data:

```solidity
evaluator.setJobContractRestriction(true);
evaluator.setAllowedJobContract(officialERC8183, true);
```

### Threat Threshold

`threatThreshold` cannot be set to 0 — this would silently disable the entire threat system.

## Contract

- [`contracts/src/TrustEvaluator.sol`](contracts/src/TrustEvaluator.sol) — Main evaluator contract
- [`contracts/test/TrustEvaluator.t.sol`](contracts/test/TrustEvaluator.t.sol) — Foundry test suite

## Usage

### 1. Deploy TrustOracle

Deploy or connect to an oracle that implements `ITrustOracle`:

```solidity
interface ITrustOracle {
    struct UserReputation {
        uint256 reputationScore;
        uint256 totalReviews;
        uint256 scarabPoints;
        uint256 feeBps;
        bool initialized;
        uint256 lastUpdated;
    }
    function getUserData(address user) external view returns (UserReputation memory);
}
```

### 2. Deploy TrustEvaluator

```solidity
TrustEvaluator evaluator = new TrustEvaluator(
    oracleAddress,    // ITrustOracle
    30,               // minimum score threshold (0-100)
    3,                // threat reports before auto-reject
    ownerAddress      // contract owner
);
```

### 3. Set as Job Evaluator

When creating an ERC-8183 job, set the TrustEvaluator as the evaluator address. Agents with sufficient trust scores will be evaluated instantly.

### 4. Pre-check (Optional)

Before submitting a job, check if the provider would pass:

```solidity
(uint256 score, bool wouldPass) = evaluator.preCheck(providerAddress);
```

## Testing

```bash
cd contracts
forge test -vv
```

## When to Use

✅ **Use TrustEvaluator when:**
- Provider has established on-chain reputation
- Jobs are frequent and low-to-medium value
- Speed matters more than dispute resolution
- You trust the oracle data source

❌ **Use UMA APEXEvaluator when:**
- Provider is unknown / new
- Jobs are high value
- You need permissionless dispute resolution
- You need cryptoeconomic guarantees (bonds)

## License

MIT
