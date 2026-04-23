"""Cast ``voteReject`` on a disputed APEX job.

Usage:
    python vote_reject.py <jobId>

Performs three pre-flight checks before sending any transaction:
1. Caller is a whitelisted voter.
2. The job has actually been disputed.
3. The caller hasn't already voted.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from dotenv import load_dotenv

from bnbagent.apex import APEXClient
from bnbagent.wallets import EVMWalletProvider

ROOT = Path(__file__).resolve().parent


def main() -> int:
    if len(sys.argv) != 2:
        print("Usage: python vote_reject.py <jobId>", file=sys.stderr)
        return 2

    try:
        job_id = int(sys.argv[1])
    except ValueError:
        print(f"jobId must be an integer, got {sys.argv[1]!r}", file=sys.stderr)
        return 2

    load_dotenv(ROOT / ".env")
    pk = os.environ.get("VOTER_PRIVATE_KEY")
    if not pk:
        print("VOTER_PRIVATE_KEY is required", file=sys.stderr)
        return 2

    network = os.environ.get("NETWORK", "bsc-testnet")
    wallet = EVMWalletProvider(password="example", private_key=pk, persist=False)
    apex = APEXClient(wallet, network=network)
    voter = apex.address
    assert voter is not None

    if not apex.policy.is_voter(voter):
        print(f"{voter} is NOT a whitelisted voter on {apex.policy.address}", file=sys.stderr)
        return 1
    if not apex.policy.disputed(job_id):
        print(f"jobId={job_id} has not been disputed yet; voteReject would revert", file=sys.stderr)
        return 1
    if apex.policy.has_voted(job_id, voter):
        print(f"{voter} already voted on jobId={job_id}", file=sys.stderr)
        return 0

    quorum = apex.policy.vote_quorum()
    current = apex.policy.reject_votes(job_id)
    print(f"[voter] casting voteReject on jobId={job_id} ({current}/{quorum} votes)")

    res = apex.vote_reject(job_id)
    print(f"[voter] tx: {res.get('tx_hash')}")

    new_total = apex.policy.reject_votes(job_id)
    if new_total >= quorum:
        print(f"[voter] quorum reached ({new_total}/{quorum}); any settler can now call router.settle({job_id})")
    else:
        print(f"[voter] current reject votes: {new_total}/{quorum} — still below quorum")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
