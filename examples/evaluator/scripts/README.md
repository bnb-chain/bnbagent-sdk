# APEX Evaluator Scripts

Utility scripts for managing the APEX Evaluator — assertions, settlement, disputes, and bond management.

## Setup

```bash
npm install
cp .env.example .env
# Edit .env with your configuration
```

## Configuration

Edit `.env`:

```bash
# RPC and contract addresses
RPC_URL=https://data-seed-prebsc-2-s2.binance.org:8545
ERC8183_ADDRESS=0xf8b6921fea71dfca3482a4a69576198d2072d188
OOV3_ADDRESS=0xFc5bb3e475cc9264760Cf33b1e9ea7B87942C709
APEX_EVALUATOR_ADDRESS=0xd707433ca1343759ccc127402b18cfdae3f0e10b

# Wallet private keys
PRIVATE_KEY=0x...           # General operations
SETTLE_PRIVATE_KEY=0x...    # For settle-job (needs BNB for gas)
DISPUTE_PRIVATE_KEY=0x...   # For dispute-job (needs BNB + bond tokens)
```

| Key                   | Script       | Requirements                 |
| --------------------- | ------------ | ---------------------------- |
| `SETTLE_PRIVATE_KEY`  | settle-job   | BNB for gas                  |
| `DISPUTE_PRIVATE_KEY` | dispute-job  | BNB for gas + bond tokens    |
| `PRIVATE_KEY`         | fallback     | Used if specific key not set |

## Scripts

### get-job

Query APEX job details and assertion status.

```bash
npm run get-job -- <jobId>

# Example
npm run get-job -- 5
```

Output includes:
- Job info (client, provider, evaluator, budget, status)
- OOv3 assertion details (liveness end, disputed, settleable)
- Bond balance and minimum bond

### settle-job

Settle a job's UMA assertion after liveness period expires.

```bash
npm run settle-job -- <jobId>

# Example
npm run settle-job -- 5
```

Prerequisites:
- Liveness period must have expired
- Assertion must not be settled yet
- `SETTLE_PRIVATE_KEY` wallet needs BNB for gas

### settle-jobs

Batch settle ALL jobs that are ready for settlement.

```bash
npm run settle-jobs
```

### dispute-job

Dispute a job's UMA assertion during the liveness period.

```bash
npm run dispute-job -- <jobId>

# Example
npm run dispute-job -- 5
```

Prerequisites:
- Job must be in "Submitted" status
- Liveness period must not have expired
- `DISPUTE_PRIVATE_KEY` wallet needs:
  - BNB for gas
  - Bond tokens (same amount as assertion bond)

### resolve-dispute

Resolve a disputed job by pushing price to MockOracle (BSC Testnet only).

```bash
npm run resolve-dispute -- <jobId> <true|false>

# Agent wins (assertion was correct)
npm run resolve-dispute -- 6 true

# Client wins (assertion was incorrect)
npm run resolve-dispute -- 6 false
```

### get-job-claim

Verify job deliverable by checking IPFS data and hashes.

```bash
npm run get-job-claim -- <jobId>
```

### get-settlement

Get settlement details for a completed/rejected job.

```bash
npm run get-settlement -- <jobId>
```

### Bond Management

```bash
# Check evaluator bond balance
npm run check-bond

# Deposit bond to evaluator
AMOUNT=10 npm run deposit-bond

# Withdraw bond from evaluator (owner only)
AMOUNT=10 npm run withdraw-bond
```

### initiate-assertion

Manually initiate assertion for a submitted job (if hook didn't trigger).

```bash
npm run initiate-assertion -- <jobId>
```

## Job Lifecycle

```
1. OPEN       → Job created, budget set
2. FUNDED     → Client funded escrow
3. SUBMITTED  → Agent submitted work
   └─ Liveness Period (30 min default)
      ├─ No dispute → settleJob() → COMPLETED (agent paid)
      └─ Dispute    → DVM voting  → COMPLETED or REJECTED
4. COMPLETED  → Agent paid
   or REJECTED → Client refunded
   or EXPIRED  → Client can claim refund
```

## Environment Variables

All scripts support environment variables:

```bash
JOB_ID=5 npm run get-job
JOB_ID=5 npm run settle-job
JOB_ID=5 npm run dispute-job
```
