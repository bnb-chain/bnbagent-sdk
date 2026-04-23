# Voter example

A whitelisted voter participates in APEX's optimistic policy by casting
`voteReject` on jobs the client has disputed. Voters **cannot approve** —
silence past the dispute window is implicit approval.

## Lifecycle from the voter's point of view

1. A client calls `policy.dispute(jobId)` within `disputeWindow` seconds of submit.
2. Off-chain, the voter decides if the deliverable is valid.
3. If invalid → `policy.voteReject(jobId)`.
4. Once `rejectVotes[jobId] >= voteQuorum`, anyone may call `router.settle(jobId, "")`
   and the kernel emits `JobRejected`; client gets the refund.

## What's in this directory

| File | Purpose |
|------|---------|
| `vote_reject.py` | Cast `voteReject` on a given jobId with safety checks |
| `watch.py`       | Poll the policy for disputed jobs and print them |

## Setup

```bash
cp .env.example .env
# Fill in VOTER_PRIVATE_KEY (an EOA whitelisted by the policy admin).
```

## Usage

```bash
# One-shot vote.
python vote_reject.py <jobId>

# Watcher (useful to spot pending disputes on testnet).
python watch.py
```

## Preconditions for a successful `voteReject`

- Caller is a whitelisted voter (`policy.isVoter(address) == true`).
- Job was already disputed (`policy.disputed(jobId) == true`).
- Caller hasn't voted yet (`policy.hasVoted(jobId, address) == false`).

The script verifies all three before sending a transaction.
