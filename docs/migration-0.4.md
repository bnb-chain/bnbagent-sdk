# Migrating to bnbagent 0.4.0

0.4.0 narrows the SDK to its protocol meta-capability layer. Two public
surfaces were removed — the bundled FastAPI server and the `BNBAgent`
plugin facade — and several capabilities were added. No deprecation shims
were kept: every removal below has a direct replacement.

## 1. `bnbagent.erc8183.server` is gone

The package split three ways:

| 0.3.x | 0.4.0 |
|---|---|
| `from bnbagent.erc8183.server import ERC8183JobOps` | `from bnbagent.erc8183 import ERC8183JobOps` |
| `from bnbagent.erc8183.server import funded_job_watcher` (or `.job_ops`) | `from bnbagent.erc8183 import funded_job_watcher` |
| `from bnbagent.erc8183.server.rate_limit import SlidingWindowLimiter` | `from bnbagent.utils import SlidingWindowLimiter` |
| `from bnbagent.erc8183.server import create_erc8183_app` | **Removed from the SDK.** The FastAPI factory now lives in [`examples/agent-server/src/erc8183_server.py`](../examples/agent-server/src/erc8183_server.py) as copy-and-own example code |

Rationale: an agent's serving surface (A2A / MCP / HTTP) is an application
choice, not protocol capability. The SDK keeps the headless primitives every
serving form shares; the HTTP shell becomes a reference implementation you
own. If you used `create_erc8183_app`, copy `examples/agent-server/` and run
it as your own code — behavior is identical (same routes, poll loop, env
knobs).

**Behavior change in `SlidingWindowLimiter`:** it no longer raises
`fastapi.HTTPException(429)` — it raises the transport-agnostic
`bnbagent.utils.RateLimitExceeded`. HTTP servers catch it and convert:

```python
try:
    limiter.check(client_ip)
except RateLimitExceeded:
    raise HTTPException(status_code=429, detail="Too many requests")
```

The `[server]` pip extra is gone (`fastapi`/`uvicorn` are no longer SDK
dependencies); depend on `bnbagent` plus your own serving stack.

**`error_code` is no longer an HTTP status code.** `ERC8183JobOps` failure
dicts now carry transport-neutral semantic strings, and the retry signal is
an explicit field instead of the 4xx/5xx class:

| 0.3.x numeric | 0.4.0 `error_code` | retryable |
|---|---|---|
| 402 | `budget_too_low` | no |
| 403 | `not_assigned` | no |
| 404 | `not_found` | no |
| 408 | `job_expired` | no |
| 409 | `wrong_status` | no |
| 410 | `quote_expired` / `description_invalid` / `submit_deadline_passed` | no |
| 413 | `payload_too_large` | no |
| 500 | `internal_error` | yes |
| 503 | `chain_unavailable` | yes |

Transient failures (and only those) carry `"retryable": True` — branch on
that instead of `400 <= code < 500`. HTTP servers own the reverse mapping;
see the `_HTTP_STATUS` table in
[`examples/agent-server/src/erc8183_server.py`](../examples/agent-server/src/erc8183_server.py)
(it preserves the exact 0.3.x wire behavior). `rpc_error_code` (the node's
raw JSON-RPC code) is unchanged.

## 2. `BNBAgent` facade and the module/plugin system are gone

Removed: `BNBAgent`, `BNBAgentConfig`, `bnbagent.core.module`
(`BNBAgentModule`, `ModuleInfo`), `bnbagent.core.registry` (`ModuleRegistry`),
the `bnbagent.modules` entry-point group, and the per-package `module.py`
adapters.

Every protocol client has always been directly constructible — compose
explicitly instead:

```python
# 0.3.x
sdk = BNBAgent.from_env()
erc8183 = sdk.module("erc8183")

# 0.4.0
from bnbagent import ERC8183Client, ERC8004Agent, EVMWalletProvider
wallet = EVMWalletProvider(password=..., private_key=...)
client = ERC8183Client(wallet_provider=wallet, network="bsc-testnet")
agent  = ERC8004Agent(wallet_provider=wallet, network="bsc-testnet")
```

`AgentConfig` (the network + wallet plumbing base, including the
`private_key` auto-wrap-and-zero behavior) and `NetworkConfig` /
`resolve_network` are unchanged. `ERC8183Config` is unchanged.

## 3. New in 0.4.0

- **Per-network RPC override** — `RPC_URL_BSC_TESTNET` / `RPC_URL_BSC_MAINNET`
  take precedence over the global `RPC_URL` in `resolve_network()`. One
  process can pin both chains to distinct nodes.
- **`bnbagent.utils.to_raw` / `from_raw`** — Decimal-exact human ↔ raw token
  unit conversion.
- **`AgentEndpoint.a2a(base_url)` / `AgentEndpoint.mcp(url, version=...)`** —
  ERC-8004 registration constructors encoding exactly what the EIP-8004
  registration-file format specifies (A2A: `/.well-known/agent-card.json`
  auto-appended; MCP: bare server URL + protocol version — stdio servers
  have no registrable URL). Registration side only — the SDK ships no
  A2A/MCP runtime.
- **`examples/a2a-agent/`** — reference A2A serving implementation
  (agent card + JSON-RPC `message/send` + buyer counterpart).

## 4. Serving-direction guidance

A2A first, MCP second, HTTP as a reference example. The SDK's role is the
registration constructors plus the headless primitives
(`ERC8183JobOps`, `funded_job_watcher`, `NegotiationHandler`,
`SlidingWindowLimiter`); see `ARCHITECTURE.md` ("Layering: where the SDK
ends") for the boundary criterion.
