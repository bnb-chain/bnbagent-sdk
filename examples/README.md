# Examples

End-to-end examples for ERC-8183 (AgenticCommerce + EvaluatorRouter + OptimisticPolicy).

## Directory layout

| Example | Role | Description |
|---------|------|-------------|
| [client/](client/) | Client | Stand-alone scripts that walk a job through each of the five canonical flows (happy path, dispute-reject, stalemate-expire, never-submit, cancel-open) |
| [voter/](voter/) | Voter | Whitelisted voter casting `voteReject` on disputed jobs |
| [agent-server/](agent-server/) | Provider | FastAPI agent with funded-job poll loop. Shows three storage backends (local / IPFS / SQLite) inline in `src/service.py` |

## Recommended path

```
1. client/      → learn createJob → registerJob → setBudget → fund → submit → settle
2. voter/       → understand dispute quorum
3. agent-server → run a full provider with the funded-job poll loop
```

## Prerequisites

- Python 3.10+
- Testnet BNB ([faucet](https://www.bnbchain.org/en/testnet-faucet))
- `uv sync` or `pip install bnbagent`
- Some of the deployed payment token (default: U, see address below)

## BSC Testnet addresses (SDK defaults)

| Contract | Address |
|----------|---------|
| AgenticCommerce (kernel) | `0xa206c0517b6371c6638cd9e4a42cc9f02a33b0de` |
| EvaluatorRouter | `0xd7d36d66d2f1b608a0f943f722d27e3744f66f25` |
| OptimisticPolicy | `0x4f4678d4439fec812ac7674bb3efb4c8f5fb78a6` |
| Identity Registry (ERC-8004) | `0x8004A818BFB912233c491871b3d84c89A494BD9e` |

Payment token address is fetched at runtime via `ERC8183Client.payment_token`.
