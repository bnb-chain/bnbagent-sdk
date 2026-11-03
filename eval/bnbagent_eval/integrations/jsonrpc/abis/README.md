# Vendored ABI fragments

Minimal fragments (only the view functions and events the read-only JSON-RPC adapter uses),
copied verbatim from the bnbagent SDK `abis/` directory (`bnbagent` 0.5.1,
https://github.com/bnb-chain/bnbagent-sdk). The SDK is MIT-licensed,
Copyright (c) 2026 BNBChain Contributors.

| File | Entries |
|---|---|
| `IdentityRegistry.json` (ERC-8004) | `tokenURI`, `getMetadata`, `ownerOf` |
| `AgenticCommerce.json` (ERC-8183) | `getJob` |
| `OptimisticPolicy.json` | `disputeWindow`, `disputed`, `isVoter`, `hasVoted`, event `JobInitialised` |
| `EvaluatorRouter.json` | `jobPolicy` |
| `ERC20.json` | `balanceOf` |

To refresh, extract the same entries from the SDK ABIs; keep the entry objects unchanged.
