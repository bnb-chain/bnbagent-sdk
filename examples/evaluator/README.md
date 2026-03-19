# Evaluator Scripts

Manage the APEX Evaluator: deposit bonds, check assertions, settle jobs, handle disputes.

## Setup

```bash
cd scripts
npm install
cp .env.example .env
# Edit .env: add PRIVATE_KEY (and SETTLE_PRIVATE_KEY / DISPUTE_PRIVATE_KEY if needed)
```

## Commands

**Job Queries:**

| Command | Description |
|---------|-------------|
| `npm run get-job -- <job_id>` | Get APEX job details + assertion info |
| `npm run get-job-claim -- <job_id>` | Get APEX job claim & verify |
| `npm run get-settlement -- <job_id>` | Get settlement details |

**Bond Management:**

| Command | Description |
|---------|-------------|
| `AMOUNT=<n> npm run deposit-bond` | Deposit bond tokens to evaluator |
| `AMOUNT=<n> npm run withdraw-bond` | Withdraw bond from evaluator (owner only) |
| `npm run check-bond` | Check current bond balance |

**Settlement & Disputes:**

| Command | Description |
|---------|-------------|
| `npm run settle-job -- <job_id>` | Settle single job after liveness |
| `npm run settle-jobs` | Batch settle ALL ready jobs |
| `npm run dispute-job -- <job_id>` | Dispute an assertion (requires bond) |
| `npm run resolve-dispute -- <job_id> <true\|false>` | Resolve dispute via MockOracle (testnet) |
| `npm run initiate-assertion -- <job_id>` | Manually initiate assertion |

Run `npm run help` to see all available commands.
