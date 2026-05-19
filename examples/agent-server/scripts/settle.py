"""Operator-side settle for a SUBMITTED ERC-8183 job — v1 helper.

The typical reason an agent operator runs this is to claim payment after
the dispute window elapses without dispute (verdict = APPROVE). Settle
is permissionless on-chain: any wallet can call ``router.settle(jobId)``
and pay the gas; this script just packages the wallet load + pre-flight
checks for the common operator workflow.

NOTE: This is a v1 helper. A future ``bnbagent`` CLI will replace these
ad-hoc per-task scripts with a unified subcommand surface (e.g.
``bnbagent erc8183 settle <jobId>``).

Usage:
    uv run python scripts/settle.py <jobId>
    uv run python scripts/settle.py <jobId> --env .env.qa

Pre-flight (no transaction sent unless all pass):
1. Job is in SUBMITTED state (not OPEN / FUNDED / already-settled).
2. Policy verdict is APPROVE or REJECT (PENDING ⇒ wait, then retry).

If the loaded wallet is not ``job.provider`` the script still proceeds
(settle is permissionless) but warns — usually that means a typo'd jobId
or a misconfigured ``.env``.

Environment (loaded from ``examples/agent-server/.env`` by default):
    PRIVATE_KEY      — settler wallet (first run only; keystore is reused after)
    WALLET_PASSWORD  — keystore password
    NETWORK          — defaults to bsc-testnet
    RPC_URL          — optional RPC override
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

from bnbagent.erc8183 import ERC8183Client
from bnbagent.erc8183.types import JobStatus, Verdict
from bnbagent.wallets import EVMWalletProvider

ROOT = Path(__file__).resolve().parent.parent  # examples/agent-server/


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Settle a SUBMITTED ERC-8183 job (v1 operator helper).",
    )
    parser.add_argument("job_id", type=int, help="On-chain jobId to settle")
    parser.add_argument(
        "--env", default=".env", help="env file name relative to agent-server/"
    )
    args = parser.parse_args()

    load_dotenv(ROOT / args.env)

    wallet_password = os.getenv("WALLET_PASSWORD")
    private_key = os.getenv("PRIVATE_KEY")
    if not wallet_password:
        print("WALLET_PASSWORD is required (set it in .env)", file=sys.stderr)
        return 2

    wallet = EVMWalletProvider(password=wallet_password, private_key=private_key)
    erc8183 = ERC8183Client(wallet, network=os.getenv("NETWORK", "bsc-testnet"))
    me = erc8183.address

    job = erc8183.get_job(args.job_id)

    if job.status != JobStatus.SUBMITTED:
        print(
            f"jobId={args.job_id} is {job.status.name} — settle requires SUBMITTED",
            file=sys.stderr,
        )
        return 1

    verdict, _reason = erc8183.get_verdict(args.job_id)
    if verdict == Verdict.PENDING:
        print(
            f"jobId={args.job_id} verdict is PENDING — wait until the dispute "
            "window elapses (or vote quorum is reached) and retry.",
            file=sys.stderr,
        )
        return 1

    if job.provider.lower() != me.lower():
        # router.settle is permissionless; proceed but make the gas/economics
        # visible so a misconfigured .env doesn't silently spend on someone
        # else's job.
        print(
            f"[warn] jobId={args.job_id} provider is {job.provider}, "
            f"this wallet is {me} — you will pay gas to settle a job you do not own.",
            file=sys.stderr,
        )

    print(f"[settler={me}] settling jobId={args.job_id} (verdict={verdict.name}) ...")
    result = erc8183.settle(args.job_id)
    print(f"[settler] settle tx: {result['transactionHash']}")

    final = erc8183.get_job(args.job_id)
    print(f"[settler] jobId={args.job_id} status -> {final.status.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
