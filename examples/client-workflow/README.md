# APEX Lifecycle Demo

Complete end-to-end demonstration of the Agent Payment Exchange Protocol.
Drives all 8 steps from the client side in your terminal.

## What This Demo Shows

| Step | Action | Protocol |
|------|--------|----------|
| 0 | Discover agent from ERC-8004 registry | ERC-8004 |
| 1 | Negotiate price with agent | Off-chain |
| 2 | Create job on-chain | ERC-8183 |
| 3 | Set budget | ERC-8183 |
| 4 | Approve BEP20 & fund escrow | ERC-8183 |
| 5 | Wait for agent to deliver | ERC-8183 |
| 6 | Fetch deliverable & generate newsletter | IPFS + LLM |
| 7 | UMA challenge period (wait/dispute/skip) | UMA APEX Evaluator |
| 8 | Final status & money flow | ERC-8183 |

## Prerequisites

1. **Provider running**: Start the [agent-server](../agent-server/) first
2. **Two wallets**: Client and provider must use different private keys
3. **Testnet tokens**: Both wallets need BNB (gas) and U tokens (payment)

## Setup

pip install -r requirements.txt
cp .env.example .env
# Edit .env: add PRIVATE_KEY, AGENT_B_ADDRESS, AGENT_B_URL

## Run

# Basic
python scripts/run_demo.py "What are the latest BNB Chain developments?"

# With ERC-8004 discovery (set AGENT_ID in .env)
python scripts/run_demo.py --discover "BNB Chain news"

## Optional: Newsletter Generation

Set `OPENROUTER_API_KEY` in `.env` to enable Step 6 (bilingual newsletter).
Without it, the demo skips newsletter generation and continues normally.
