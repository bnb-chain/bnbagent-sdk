# Client examples — five canonical APEX v1 flows

Stand-alone scripts that exercise each of the five ERC-8183 user flows from the
client side. Mirrors `apex-contracts/test/e2e/flows/*` one-to-one.

| Script | Flow | Outcome |
|--------|------|---------|
| `happy.py` | create → register → fund → provider submits → `settle` → **COMPLETED** | payment released, no dispute |
| `dispute_reject.py` | submit → client `dispute` → whitelisted voters `voteReject` → `settle` → **REJECTED** | refund to client |
| `stalemate_expire.py` | submit → client `dispute` → quorum not reached → job expires → `claimRefund` → **EXPIRED** | refund via expiry |
| `never_submit.py` | provider never submits → job expires → `claimRefund` → **EXPIRED** | refund via expiry |
| `cancel_open.py` | client cancels before funding (`reject`) → **REJECTED** | nothing escrowed |

All five scripts share the same `_helpers.py` for env loading and small
fixtures (job description, expiry, provider address).

## Setup

```bash
uv sync
cp .env.example .env
# Fill in PRIVATE_KEY (client) and PROVIDER_ADDRESS at minimum.
```

## Required env

```
WALLET_PASSWORD      keystore password (any string)
PRIVATE_KEY          client private key (0x...)
PROVIDER_ADDRESS     provider EOA (pick any; the happy path will sign from
                     a separate PROVIDER_PRIVATE_KEY if set, otherwise the
                     provider steps are left as printed instructions)
# Optional
NETWORK                    bsc-testnet (default)
RPC_URL                    override RPC
APEX_COMMERCE_ADDRESS      override commerce proxy
APEX_ROUTER_ADDRESS        override router proxy
APEX_POLICY_ADDRESS        override policy
```

## Notes

- Expiry is set to `now + 10 minutes` for flows that should complete quickly
  and `now + 65 minutes` for flows that rely on expiry (the on-chain minimum
  is `now + 5 minutes`).
- The dispute-reject and stalemate-expire flows rely on a whitelisted
  voter. Provide `VOTER_PRIVATE_KEY` in the env if you want the script to
  cast the reject vote itself; otherwise it prints the jobId and expects
  an out-of-band vote (see `examples/voter/`).
- Every script is idempotent-ish: it creates a new job each run, so reruns
  don't collide.
