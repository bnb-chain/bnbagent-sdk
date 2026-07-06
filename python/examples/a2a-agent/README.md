# a2a-agent — A2A-fronted ERC-8183 provider

The SDK's recommended serving direction made concrete: **the agent's outward
surface is A2A** (agent card + JSON-RPC `message/send`); everything below it is
plain SDK protocol capability. The SDK ships no serving runtime — the ~200-line
`src/server.py` here IS the serving layer, and it's yours to copy and own.

```
buyer.py                                server.py
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

- Discovery is **ERC-8004**: `scripts/register.py` registers the card URL
  on-chain via `AgentEndpoint.a2a(base_url)` (the SDK's A2A registration
  constructor); `scripts/buyer.py` resolves it back from an `AGENT_ID`.
- The A2A wire format (card shape, JSON-RPC 2.0 `message/send`, data parts)
  follows the A2A spec but is hand-rolled on FastAPI to stay minimal. For a
  production agent consider the official `a2a-sdk` package — clients speaking
  spec A2A interoperate with either.
- The signed quote round-trips into `createJob` via the SDK's
  `build_job_description`, so `ecrecover(negotiation_hash, provider_sig)`
  stays verifiable on-chain — the same anti-tamper chain as the HTTP example.

## Run it

```bash
cp .env.example .env        # fill PRIVATE_KEY (provider wallet)
uv sync

# 1. serve the agent
uv run uvicorn server:app --app-dir src --port 8010

# 2. (optional, one-time) register the card URL on ERC-8004
uv run python scripts/register.py     # prints AGENT_ID → put it in .env

# 3. buyer: discover → quote (chain-free unless BUYER_PRIVATE_KEY is set)
uv run python scripts/buyer.py
```

Without `BUYER_PRIVATE_KEY` the buyer stops after printing the signed quote —
a fully chain-free first run. With it, the buyer funds a real job on
`bsc-testnet`; pair it with a funded-job watcher (see `examples/agent-server`
or the headless 15-liner in the repo README) to complete the sell side.

## Skills

| Skill id | Input data part | Result data part |
|---|---|---|
| `negotiate-erc8183-job` | `{"skill": ..., "task_description": "...", "terms": {"deliverables", "quality_standards"}}` | Signed negotiation envelope (`response.terms.price`, `negotiation_hash`, `provider_sig`, `provider_address`) |
| `erc8183-job-status` | `{"skill": ..., "job_id": 42}` | On-chain job snapshot (status, budget, deadlines) |
