"""Poll recent ``Disputed`` events on ``OptimisticPolicy`` and print them.

Useful on testnet when you don't have an indexer — gives a voter a cheap
view of which jobs are waiting for votes.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

from dotenv import load_dotenv

from bnbagent.apex import APEXClient
from bnbagent.wallets import EVMWalletProvider

ROOT = Path(__file__).resolve().parent
POLL_INTERVAL = 12  # BSC block time ~3s, 12s keeps it light


def main() -> None:
    load_dotenv(ROOT / ".env")
    pk = os.environ.get("VOTER_PRIVATE_KEY")
    if not pk:
        raise SystemExit("VOTER_PRIVATE_KEY is required")

    network = os.environ.get("NETWORK", "bsc-testnet")
    wallet = EVMWalletProvider(password="example", private_key=pk, persist=False)
    apex = APEXClient(wallet, network=network)
    voter = apex.address
    assert voter is not None

    print(
        f"Watching OptimisticPolicy={apex.policy.address} on {network}\n"
        f"Voter={voter} whitelisted={apex.policy.is_voter(voter)}\n"
    )

    last_block = apex.w3.eth.block_number
    while True:
        head = apex.w3.eth.block_number
        if head > last_block:
            logs = apex.policy.contract.events.Disputed().get_logs(
                from_block=last_block + 1,
                to_block=head,
            )
            for log in logs:
                job_id = log["args"]["jobId"]
                client = log["args"]["client"]
                voted = apex.policy.has_voted(job_id, voter)
                print(
                    f"[{time.strftime('%H:%M:%S')}] Disputed jobId={job_id} "
                    f"client={client} already_voted={voted}"
                )
            last_block = head
        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nstopped.")
