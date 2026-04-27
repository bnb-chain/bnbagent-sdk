# Voter example

A whitelisted voter participates in APEX's optimistic policy by casting
`voteReject` on jobs the client has disputed. Voters **cannot approve** —
silence past the dispute window is implicit approval.

## Lifecycle from the voter's point of view

1. A client calls `policy.dispute(jobId)` within `disputeWindow` seconds of submit.
2. `watch.py` detects the `Disputed` event, downloads the `DeliverableManifest`
   from IPFS, verifies the on-chain hash, and prints the deliverable content.
3. Voter reviews and presses `r` to reject.
4. `voteReject(jobId)` is sent on-chain, emitting a `VoteCast` event.
5. `watch.py` detects `VoteCast`. When `rejectVotes >= voteQuorum`, it
   automatically calls `router.settle(jobId)` — no manual settle step needed.

## What's in this directory

| File | Purpose |
|------|---------|
| `watch.py` | Event-driven loop: watches `Disputed` + `VoteCast`, reviews IPFS manifests, prompts to vote, auto-settles on quorum |
| `vote_reject.py` | One-shot `voteReject` on a specific jobId (manual fallback) |

## Setup

```bash
cp .env.example .env
# Fill in VOTER_PRIVATE_KEY (an EOA whitelisted by the policy admin).
# Set RPC_URL to a reliable endpoint (e.g. NodeReal) to avoid rate limits.
```

`.env` fields:

| Variable | Required | Description |
|----------|----------|-------------|
| `VOTER_PRIVATE_KEY` | Yes | Whitelisted voter private key (`0x…`) |
| `NETWORK` | No | `bsc-testnet` (default) |
| `RPC_URL` | No | Override RPC (recommended — avoids default rate limits) |
| `STORAGE_GATEWAY_URL` | No | IPFS gateway for downloading manifests (default: Pinata) |

## Usage

```bash
# Interactive watch loop (recommended)
python watch.py

# One-shot vote (manual fallback)
python vote_reject.py <jobId>
```

### `watch.py` behaviour

On each poll tick (`POLL_INTERVAL = 12s`):

1. **`Disputed` events** — for each new job: fetches the `DeliverableManifest`
   from IPFS (via `optParams.deliverable_url`), verifies the manifest hash
   against the on-chain `deliverable` bytes32, and prints the response content
   (up to 2 000 chars). Prompts `[r]eject / [s]kip`.
2. **`VoteCast` events** — prints `rejectVotes/quorum` for every vote. When
   `rejectVotes >= quorum`, calls `router.settle(jobId)` automatically and
   prints the result.

## Preconditions for a successful `voteReject`

- Caller is a whitelisted voter (`policy.isVoter(address) == true`).
- Job was already disputed (`policy.disputed(jobId) == true`).
- Caller hasn't voted yet (`policy.hasVoted(jobId, address) == false`).
