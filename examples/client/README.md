# Client examples — ERC-8183 flows

Stand-alone scripts that exercise the canonical ERC-8183 flows from the
client side. Mirrors `erc8183-contracts/test/e2e/flows/*` one-to-one, plus an
end-to-end IPFS integration test.

All scripts share `_helpers.py` for env loading, job description, expiry, and
provider address.

| Script | Flow | Outcome |
|--------|------|---------|
| `happy.py` | create → register → fund → provider submits → `settle` → **COMPLETED** | payment released, no dispute |
| `dispute_reject.py` | submit → client `dispute` → whitelisted voters `voteReject` → `settle` → **REJECTED** | refund to client |
| `stalemate_expire.py` | submit → client `dispute` → quorum not reached → job expires → `claimRefund` → **EXPIRED** | refund via expiry |
| `never_submit.py` | provider never submits → job expires → `claimRefund` → **EXPIRED** | refund via expiry |
| `cancel_open.py` | client cancels before funding (`reject`) → **REJECTED** | nothing escrowed |
| `create_and_verify.py` | client funds → agent's funded-poll loop submits → IPFS verify (add `--dispute` to also exercise the dispute branch) | integration test against agent-server |

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
PROVIDER_ADDRESS     provider EOA

# Optional
NETWORK                    bsc-testnet (default)
RPC_URL                    override RPC
ERC8183_COMMERCE_ADDRESS      override commerce proxy
ERC8183_ROUTER_ADDRESS        override router proxy
ERC8183_POLICY_ADDRESS        override policy
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

## `create_and_verify.py`

End-to-end test against a running `agent-server`. Requires the agent-server
to be started first with IPFS storage configured:

```bash
# Terminal 1
cd examples/agent-server && uv run python scripts/run_agent.py

# Terminal 2
cd examples/client && python create_and_verify.py
```

The script:
1. Creates, registers, budgets, and funds a job for the agent-server provider.
2. Waits for the agent-server's funded-job poll loop to pick up the job.
3. Polls until the job reaches `SUBMITTED`.
4. Fetches the `DeliverableManifest` from the IPFS gateway and verifies its
   keccak256 hash against the on-chain `deliverable` bytes32.
5. (only with `--dispute`) Raises a dispute — leaving the voter to review via
   `examples/voter/watch.py`. Without the flag, the script ends here and the
   job can be settled to `COMPLETED` after the dispute window elapses.
