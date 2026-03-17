# Quickstart: From Zero to Running in 5 Steps

## Prerequisites
- Python 3.10+
- Testnet BNB ([faucet](https://www.bnbchain.org/en/testnet-faucet))
- `pip install bnbagent python-dotenv`

## Setup

```bash
cp .env.example .env
# Edit .env: add your PRIVATE_KEY
```

## Steps

| Step | Script | What it does |
|------|--------|-------------|
| 1 | `python step1_setup_wallet.py` | Setup wallet, mint test tokens |
| 2 | `python step2_register_agent.py` | Register agent on ERC-8004 |
| 3 | `python step3_run_agent.py` | Start agent server (Terminal 1) |
| 4 | `python step4_create_job.py` | Create & fund a job (Terminal 2) |
| 5 | `python step5_settle_job.py <JOB_ID>` | Settle job after liveness (use job ID from step 4) |

> **Note:** Step 3 runs a server. Open a second terminal for steps 4-5.

## E2E Test

Run all 5 steps automatically in a single command:

```bash
# Full flow (includes ~30min liveness wait for settlement)
uv run python examples/quickstart/test_quickstart_e2e.py

# Quick validation (skip settlement wait)
uv run python examples/quickstart/test_quickstart_e2e.py --skip-settle
```
