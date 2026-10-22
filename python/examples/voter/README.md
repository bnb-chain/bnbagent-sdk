# Voter example

A whitelisted voter participates in ERC-8183's optimistic policy by casting `voteReject` on jobs the client has disputed. Voters **cannot approve** - silence past the dispute window is implicit approval.

## Lifecycle from the voter's point of view

1. A client calls `policy.dispute(jobId)` within `disputeWindow` seconds of submit.
2. `watch.py` detects the `Disputed` event, downloads the `DeliverableManifest` from IPFS, verifies the on-chain hash, and prints the deliverable content.
3. Voter reviews and presses `r` to reject.
4. `voteReject(jobId)` is sent on-chain, emitting a `VoteCast` event.
5. `watch.py` detects `VoteCast`. When `rejectVotes >= voteQuorum`, it automatically calls `router.settle(jobId)` - no manual settle step needed.

## What's in this directory

| File | Purpose |
| --- | --- |
| `watch.py` | Event-driven loop: watches `Disputed` + `VoteCast`, reviews IPFS manifests, prompts to vote, settles automatically once `rejectVotes >= voteQuorum` |
| `vote_reject.py` | One-shot `voteReject` on a specific jobId (manual fallback) |

## Setup

```bash
cp .env.example .env
# Fill in VOTER_PRIVATE_KEY (an EOA whitelisted by the policy admin).
# Set RPC_URL to a reliable endpoint (e.g. NodeReal) to avoid rate limits.
```

`.env` fields:

| Variable | Required | Description |
| --- | --- | --- |
| `VOTER_PRIVATE_KEY` | Yes | Whitelisted voter private key (`0x…`) |
| `NETWORK` | No | `bsc-testnet` (default) |
| `RPC_URL` | No | Override RPC. Must serve `eth_getLogs` for blocks as old as the voting window (7 days on mainnet): the default `bsc-mainnet` RPC does not, and `watch.py` exits at startup when the RPC cannot |
| `STORAGE_GATEWAY_URL` | No | IPFS gateway for downloading manifests (default: Pinata) |
| `VOTER_DECISION_MARGIN_SECONDS` | No | Seconds before a voting window closes from which a disputed job whose deliverable could not be looked up on-chain is put to a vote anyway (default `1800`, at most half the window) |

## Usage

```bash
# Interactive watch loop (recommended)
python watch.py

# One-shot vote (manual fallback)
python vote_reject.py <jobId>
```

### `watch.py` behaviour

On each poll tick (`POLL_INTERVAL = 12s`):

1. **`Disputed` events** - for each new job: fetches the `DeliverableManifest` from IPFS (via `optParams.deliverable_url`, found from the job's `submittedAt` however long ago it was submitted), verifies the manifest hash against the on-chain `deliverable` bytes32, and prints the response content (up to 2 000 chars). Prompts `[r]eject / [s]kip`. Under `OptimisticPolicy` silence past the voting window (`submittedAt + disputeWindow`) approves the job, so every disputed job is put to a vote before its window closes:
   - a deliverable that cannot be reviewed is prompted with a "deliverable unavailable" warning: no usable `deliverable_url` (missing, empty or not a string), an unsupported URL scheme, a malformed manifest, or a download that failed for any reason (an invalid URL, a private or loopback address, a redirect, a body over 8 MiB, or one still arriving after 15 s included; downloads go through [`fetch_public_json`](../../../docs/public-downloads.md)) `MANIFEST_DOWNLOAD_ATTEMPTS` (3) ticks in a row (once, within the decision margin below);
   - a deliverable lookup that fails on the RPC (the SDK raises instead of returning "no URL") is retried on every tick, and prompted with a "deliverable not verified" warning once the window is within `VOTER_DECISION_MARGIN_SECONDS`; check the deliverable another way before voting.

   Other RPC errors are retried on every tick; one failing job or tick does not stop the loop, and a range whose logs could not be read is read again. Text from the deliverable (its URL, content and download errors) is printed with control, format and surrogate characters escaped, so it cannot rewrite the hash check or keep the prompt from showing.
2. **`VoteCast` events** - prints `rejectVotes/quorum` for every vote. When `rejectVotes >= quorum`, calls `router.settle(jobId)` automatically and prints the result.

## Preconditions for a successful `voteReject`

- Caller is a whitelisted voter (`policy.isVoter(address) == true`).
- Job was already disputed (`policy.disputed(jobId) == true`).
- Caller hasn't voted yet (`policy.hasVoted(jobId, address) == false`).
