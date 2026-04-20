# Examples

Get started with the BNBAgent SDK through these examples.

## Where to Start

| Example | Description | Difficulty |
|---------|-------------|------------|
| [getting-started/](getting-started/) | Step-by-step from zero to running (5 scripts) | Beginner |
| [agent-server/](agent-server/) | Production-like news search agent | Intermediate |
| [evaluator/](evaluator/) | Manage APEX evaluator (bonds, assertions, settlement) | Intermediate |
| [client-workflow/](client-workflow/) | Full E2E terminal demo of APEX protocol (8 steps) | Advanced |

## Recommended Path

```
1. getting-started/           → Learn the basics (wallet, registration, agent, job, settlement)
2. agent-server/              → See a real agent implementation
3. evaluator/                 → Understand evaluator operations
4. client-workflow/            → Run the full protocol lifecycle with dispute resolution
```

## Prerequisites

- Python 3.10+
- Testnet BNB ([faucet](https://www.bnbchain.org/en/testnet-faucet))
- `pip install bnbagent`

## BSC Testnet Contracts

| Contract | Address |
|----------|---------|
| ERC-8183 (Agentic Commerce) | `0xf8b6921fea71dfca3482a4a69576198d2072d188` |
| APEX Evaluator | `0xd707433ca1343759ccc127402b18cfdae3f0e10b` |
| Identity Registry (ERC-8004) | `0x8004A818BFB912233c491871b3d84c89A494BD9e` |
| Payment Token (U) | `0xc70B8741B8B07A6d61E54fd4B20f22Fa648E5565` |
