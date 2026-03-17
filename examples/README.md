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
| ERC-8183 (Agentic Commerce) | `0x3464e64dD53bC093c53050cE5114062765e9F1b6` |
| APEX Evaluator | `0x5f4976ACBCD2968D08273bA9f4a67FA43C4A3af3` |
| Identity Registry (ERC-8004) | `0x8004A818BFB912233c491871b3d84c89A494BD9e` |
| Payment Token (U) | `0xc70B8741B8B07A6d61E54fd4B20f22Fa648E5565` |
