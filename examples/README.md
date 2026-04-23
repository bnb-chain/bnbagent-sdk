# Examples

End-to-end examples for APEX v1 (AgenticCommerce + EvaluatorRouter + OptimisticPolicy).

## Directory layout

| Example | Role | Description |
|---------|------|-------------|
| [client/](client/) | Client | Stand-alone scripts that walk a job through each of the five canonical flows (happy path, dispute-reject, stalemate-expire, never-submit, cancel-open) |
| [voter/](voter/) | Voter | Whitelisted voter casting `voteReject` on disputed jobs |
| [agent-server/](agent-server/) | Provider | FastAPI agent with auto-settle background loop |

## Recommended path

```
1. client/      → learn createJob → registerJob → setBudget → fund → submit → settle
2. voter/       → understand dispute quorum
3. agent-server → run a full provider with auto-settle
```

## Prerequisites

- Python 3.10+
- Testnet BNB ([faucet](https://www.bnbchain.org/en/testnet-faucet))
- `uv sync` or `pip install bnbagent`
- Some of the deployed payment token (default: U, see address below)

## BSC Testnet addresses (SDK defaults)

| Contract | Address |
|----------|---------|
| AgenticCommerce (kernel) | `0x1e677fc06ff772e81051484c8c3845fbef13986d` |
| EvaluatorRouter | `0x0c729baa3cdac6cc3fdef6a816f6bcb85ae92ed7` |
| OptimisticPolicy | `0x459c3b7a46aa9dde45fbfc3b3d37bd062dbe6fb8` |
| Identity Registry (ERC-8004) | `0x8004A818BFB912233c491871b3d84c89A494BD9e` |

Payment token address is fetched at runtime via `APEXClient.payment_token`.
