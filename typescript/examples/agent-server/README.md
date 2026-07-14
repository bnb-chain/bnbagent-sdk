# agent-server — Blockchain News Agent (ERC-8183, HTTP serving reference)

TypeScript port of `python/examples/agent-server`. A production-like ERC-8183
provider agent that searches DuckDuckGo for blockchain news and stores
deliverables locally (or on IPFS via Pinata).

This is the SDK's **HTTP serving reference**: the factory
(`createErc8183Server`, the funded-job poll loop, `/negotiate` + job routes)
lives in `src/erc8183Server.ts` — it is example code, not SDK API. Copy the
directory and own it; the SDK underneath ships only the headless primitives
(`ERC8183JobOps`, `fundedJobWatcher`, `NegotiationHandler`,
`SlidingWindowLimiter`). For the recommended agent-facing surface (A2A), see
`../a2a-agent`.

> **EVM only.** The TypeScript SDK ships an EVM wallet provider (no TWAK), so
> unlike the Python reference there is no `WALLET_KIND` switch.
>
> **Zero HTTP framework.** The serving layer is a ~100-line `node:http` router
> (`src/http.ts`), matching the `examples/x402` precedent. Swap it for
> Hono/Fastify/Express in a real service — the handlers only touch
> `IncomingMessage`/`ServerResponse`.

## Lifecycle

```
client createJob → registerJob → setBudget → fund
      └── agent's funded-job poll loop picks up FUNDED jobs (fundedJobWatcher)
          └── onJob(job) returns a news report
              └── SDK builds a DeliverableManifest, uploads it, and calls
                  commerce.submit with the keccak256 manifest hash
      └── after the dispute window any party calls router.settle(jobId)
```

ERC-8183 uses the **OptimisticPolicy**: silence approves after the dispute
window; a client-raised dispute must reach a whitelisted-voter quorum to flip
the verdict to REJECT. There is no auto-settle — run `scripts/settle.ts`.

## Setup

```bash
cd typescript
pnpm install
cp examples/agent-server/.env.example examples/agent-server/.env
# edit .env — WALLET_PASSWORD + PRIVATE_KEY (first run) + ERC8183_AGENT_URL
```

## Run

```bash
# from typescript/
pnpm example:agent-server
# or: pnpm exec tsx examples/agent-server/src/service.ts
```

Startup banner shows wallet address, contract addresses, price, and storage
backend. The server listens on `PORT` (default 8003) and starts the funded-job
poll loop.

### One-time ERC-8004 registration

```bash
pnpm exec tsx examples/agent-server/scripts/register.ts
```

### Settle a SUBMITTED job (operator)

```bash
pnpm exec tsx examples/agent-server/scripts/settle.ts 42
```

## Files

```
src/
  http.ts            # tiny node:http router (the whole HTTP layer)
  erc8183Server.ts   # createErc8183Server() — routes + funded-job poll loop
  service.ts         # main(): news search onJob + /search testing endpoint
scripts/
  register.ts        # one-time ERC-8004 registration (generic "web" endpoint)
  settle.ts          # operator settle for a SUBMITTED job (post-verdict)
```

## ERC-8183 endpoints

| Method | Path | Description |
|--------|------|-------------|
| POST | `/erc8183/negotiate` | Price negotiation (rate-limited) |
| GET  | `/erc8183/job/:id` | Job details |
| GET  | `/erc8183/job/:id/response` | Stored deliverable response |
| GET  | `/erc8183/job/:id/verify` | Job verification |
| GET  | `/erc8183/status` | Agent status (wallet, contracts, price) |
| GET  | `/erc8183/health` | Health check |
| POST | `/search` | Direct news search (testing, no ERC-8183) |

## Storage backends

`src/service.ts` exposes two options as a "pick ONE" block. Default is (a).

| Option | Provider | On-chain `deliverable_url` | Required env |
|--------|----------|----------------------------|--------------|
| **(a)** default | `LocalStorageProvider` | `{ERC8183_AGENT_URL}/job/{id}/response` | `ERC8183_AGENT_URL` |
| **(b)** | `IPFSStorageProvider` | `ipfs://CID` | `STORAGE_API_KEY` (Pinata JWT) |
