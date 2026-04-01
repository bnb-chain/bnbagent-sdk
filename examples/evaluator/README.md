# Evaluator Scripts

Manage the APEX Evaluator: initiate assertions, check assertion status, settle jobs, handle disputes.

## Setup

```bash
cd scripts
npm install
cp .env.example .env
# Edit .env: add PRIVATE_KEY (and SETTLE_PRIVATE_KEY / DISPUTE_PRIVATE_KEY if needed)
```

## Assertion Flow (v5)

In v5, the **provider pays the bond directly** instead of the evaluator holding a pre-funded pool:

1. Provider calls `submit()` on the APEX contract
2. Provider approves bond token to the evaluator contract (`approve(evaluator, bondAmount)`)
3. Provider calls `initiateAssertion(jobId)` — bond is pulled from provider via `transferFrom`
4. After the liveness period, anyone calls `settleJob(jobId)` — bond returned to provider on clean resolution

## Commands

**Job Queries:**

| Command | Description |
|---------|-------------|
| `npm run get-job -- <job_id>` | Get APEX job details + assertion info |
| `npm run get-job-claim -- <job_id>` | Get APEX job claim & verify |
| `npm run get-settlement -- <job_id>` | Get settlement details |

**Assertion Management:**

| Command | Description |
|---------|-------------|
| `npm run initiate-assertion -- <job_id>` | Approve bond + initiate assertion (provider only) |
| `npm run check-bond` | Check total locked bond across active assertions |

**Settlement & Disputes:**

| Command | Description |
|---------|-------------|
| `npm run settle-job -- <job_id>` | Settle single job after liveness |
| `npm run settle-jobs` | Batch settle ALL ready jobs |
| `npm run dispute-job -- <job_id>` | Dispute an assertion (requires bond) |
| `npm run resolve-dispute -- <job_id> <true\|false>` | Resolve dispute via MockOracle (testnet) |

> **Note:** `deposit-bond` and `withdraw-bond` commands are removed in v5. The evaluator no longer holds a bond pool — each assertion's bond is paid and returned directly to the provider.

Run `npm run help` to see all available commands.
