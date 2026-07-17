# a2a-agent — A2A-fronted ERC-8183 provider

TypeScript port of `python/examples/a2a-agent`. The SDK's recommended serving
direction made concrete: **the agent's outward surface is A2A** (agent card +
JSON-RPC `message/send`); everything below it is plain SDK protocol capability.
The SDK ships no serving runtime — `src/server.ts` here IS the serving layer,
and it's yours to copy and own.

```
buyer.ts                                server.ts
   │  GET /.well-known/agent-card.json     │
   │ ─────────────────────────────────────►│   A2A discovery (card lists skills)
   │  POST /a2a  message/send              │
   │    {skill: negotiate-erc8183-job}     │
   │ ─────────────────────────────────────►│ ─► NegotiationHandler (SDK)
   │ ◄───────────────────────────────────  │    wallet-signed quote
   │                                       │
   │  ERC8183Client (SDK, on-chain)        │
   │  createJob → registerJob →            │
   │  setBudget → fund                     │
```

- Discovery is **ERC-8004**: `scripts/register.ts` registers the card URL
  on-chain via `AgentEndpoint.a2a(baseUrl)`; `scripts/buyer.ts` resolves it
  back from an `AGENT_ID`.
- The A2A wire format (card shape, JSON-RPC 2.0 `message/send`, data parts)
  follows the A2A spec but is hand-rolled on `node:http` to stay minimal. For a
  production agent consider the official `@a2a-js/sdk` — clients speaking spec
  A2A interoperate with either.
- The signed quote round-trips into `createJob` via `buildJobDescription`, so
  the provider signature stays verifiable by EOA recovery or ERC-1271.

> **EVM only.** Unlike the Python reference there is no `WALLET_KIND` switch —
> the TypeScript SDK ships an EVM wallet provider only.

## Run it

```bash
cd typescript
pnpm install
cp examples/a2a-agent/.env.example examples/a2a-agent/.env   # fill PRIVATE_KEY

# 1. serve the agent
pnpm example:a2a-server

# 2. (optional, one-time) register the card URL on ERC-8004
pnpm exec tsx examples/a2a-agent/scripts/register.ts   # prints AGENT_ID → put it in .env

# 3. buyer: discover → quote (chain-free unless BUYER_PRIVATE_KEY is set)
pnpm exec tsx examples/a2a-agent/scripts/buyer.ts
```

Without `BUYER_PRIVATE_KEY` the buyer stops after printing the signed quote — a
fully chain-free first run. With it, the buyer funds a real job on
`bsc-testnet`; pair it with a funded-job watcher (see `../agent-server`) to
complete the sell side.

## Skills

| Skill id | Input data part | Result data part |
|---|---|---|
| `negotiate-erc8183-job` | `{"skill": …, "task_description": "…", "terms": {"deliverables", "quality_standards"}}` | Signed negotiation envelope (`response.terms.price`, `negotiation_hash`, `provider_sig`, `provider_address`) |
| `erc8183-job-status` | `{"skill": …, "job_id": 42}` | On-chain job snapshot (status, budget, deadlines) |

## Files

```
src/
  server.ts          # agent card + JSON-RPC /a2a (2 skills), on node:http
scripts/
  register.ts        # one-time ERC-8004 registration (A2A endpoint)
  buyer.ts           # discover → quote → optional on-chain fund
```
