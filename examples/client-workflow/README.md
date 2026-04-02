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

- Python 3.10+
- [uv](https://docs.astral.sh/uv/)
- **Agent server running**: Start [examples/agent-server](../agent-server/) first (listens on port `8003` by default)
- **Two wallets**: Client and agent-server must use different private keys
- **Testnet tokens**: Both wallets need BNB (gas) and U tokens (payment)

## Setup

```bash
uv sync
cp .env.example .env
# Edit .env:
#   PRIVATE_KEY           — this client's wallet private key
#   AGENT_SERVER_ADDRESS  — the agent-server's on-chain address (printed by agent-server/scripts/register.py)
#   AGENT_SERVER_URL      — agent-server URL (default: http://localhost:8003)
```

## Run

```bash
# Basic
uv run python scripts/run_demo.py "What are the latest BNB Chain developments?"

# With ERC-8004 discovery (set AGENT_ID in .env)
uv run python scripts/run_demo.py --discover "BNB Chain news"
```

## Optional: Newsletter Generation

Set `OPENROUTER_API_KEY` in `.env` to enable Step 6 (bilingual newsletter).
Without it, the demo skips newsletter generation and continues normally.
