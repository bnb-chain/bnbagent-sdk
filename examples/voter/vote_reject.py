"""Cast ``voteReject`` on a disputed ERC-8183 job.

Usage:
    python vote_reject.py <jobId>

Performs three pre-flight checks before sending any transaction:
1. Caller is a whitelisted voter.
2. The job has actually been disputed.
3. The caller hasn't already voted.
"""

from __future__ import annotations

import dataclasses
import os
import sys
import time
from pathlib import Path

from dotenv import load_dotenv, dotenv_values

from bnbagent.erc8183 import ERC8183Client
from bnbagent.wallets import EVMWalletProvider
from bnbagent.config import resolve_network

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
    rpc_url = dotenv_values(ROOT / ".env").get("RPC_URL")
    wallet  = EVMWalletProvider(password="example", private_key=pk, persist=False)
    if rpc_url:
        nc = dataclasses.replace(resolve_network(network), rpc_url=rpc_url)
        erc8183 = ERC8183Client(wallet, network=nc)
    else:
        erc8183 = ERC8183Client(wallet, network=network)
    voter = erc8183.address
    assert voter is not None

    if not erc8183.policy.is_voter(voter):
        print(f"{voter} is NOT a whitelisted voter on {erc8183.policy.address}", file=sys.stderr)
        return 1
    if not erc8183.policy.disputed(job_id):
        print(f"jobId={job_id} has not been disputed yet; voteReject would revert", file=sys.stderr)
        return 1
    if erc8183.policy.has_voted(job_id, voter):
        print(f"{voter} already voted on jobId={job_id}", file=sys.stderr)
        return 0

    quorum = erc8183.policy.vote_quorum()
    current = erc8183.policy.reject_votes(job_id)
    print(f"[voter] casting voteReject on jobId={job_id} ({current}/{quorum} votes)")

    res = erc8183.vote_reject(job_id)
    print(f"[voter] tx: {res.get('transactionHash')}")

    # RPC 节点可能短暂返回旧块，重试直到读到最新值
    new_total = current
    for _ in range(8):
        new_total = erc8183.policy.reject_votes(job_id)
        if new_total > current:
            break
        time.sleep(2)

    if new_total >= quorum:
        print(f"[voter] quorum reached ({new_total}/{quorum}); any settler can now call router.settle({job_id})")
    else:
        print(f"[voter] current reject votes: {new_total}/{quorum} — still below quorum")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
