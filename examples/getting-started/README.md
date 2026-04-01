# Quickstart: From Zero to Running in 5 Steps

## Prerequisites
- Python 3.10+
- [uv](https://docs.astral.sh/uv/)
- Testnet BNB ([faucet](https://www.bnbchain.org/en/testnet-faucet))

## Setup

```bash
uv sync
cp .env.example .env
# Edit .env: add your PRIVATE_KEY
```

## Steps

| Step | Script | What it does |
|------|--------|-------------|
| 1 | `uv run python step1_setup_wallet.py` | Setup wallet, mint test tokens |
| 2 | `uv run python step2_run_agent.py` | Start agent server (Terminal 1) |
| 3 | `uv run python step3_register_agent.py` | Register agent on ERC-8004 (Terminal 2) |
| 4 | `uv run python step4_create_job.py` | Discover agent, create & fund a job (Terminal 2) |
| 5 | `uv run python step5_settle_job.py <JOB_ID>` | Settle job after liveness (use job ID from step 4) |

> **Note:** Step 2 runs a server. Open a second terminal for steps 3-5.

## E2E Test

Run all 5 steps automatically in a single command:

```bash
# Full flow (includes ~30min liveness wait for settlement)
uv run python examples/getting-started/test_quickstart_e2e.py

# Quick validation (skip settlement wait)
uv run python examples/getting-started/test_quickstart_e2e.py --skip-settle
```
