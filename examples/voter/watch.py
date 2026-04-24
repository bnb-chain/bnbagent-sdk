"""Voter watch loop — polls Disputed + VoteCast events, fetches IPFS manifest, prompts for vote.

For each disputed job:
  1. Reads deliverable_url from on-chain JobInitialised (optParams)
  2. Downloads DeliverableManifest from IPFS gateway
  3. Verifies manifest hash against on-chain deliverable
  4. Prints the response content for review
  5. Prompts: [r]eject / [s]kip

For each VoteCast event:
  - If rejectVotes >= quorum → auto-settle and print JobSettled result

Usage:
    cd examples/voter
    python watch.py
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import httpx
from dotenv import load_dotenv

from bnbagent.apex import APEXClient
from bnbagent.apex.schema import DeliverableManifest
from bnbagent.wallets import EVMWalletProvider

ROOT = Path(__file__).resolve().parent
POLL_INTERVAL = 12  # seconds


def fetch_manifest(deliverable_url: str, gateway_url: str) -> DeliverableManifest | None:
    """Download and parse a DeliverableManifest from IPFS."""
    try:
        if deliverable_url.startswith("ipfs://"):
            cid = deliverable_url[len("ipfs://"):]
            url = f"{gateway_url.rstrip('/')}/{cid}"
        else:
            url = deliverable_url
        resp = httpx.get(url, timeout=15)
        resp.raise_for_status()
        return DeliverableManifest.from_dict(resp.json())
    except Exception as e:
        print(f"  [warn] could not fetch manifest: {e}")
        return None


def handle_quorum_reached(apex: APEXClient, job_id: int, reject_votes: int, quorum: int) -> None:
    """Called when VoteCast shows rejectVotes >= quorum — settle and print result."""
    print(f"\n{'='*60}")
    print(f"  QUORUM REACHED job_id={job_id}  ({reject_votes}/{quorum} reject votes)")
    print(f"{'='*60}")
    print(f"  settling job {job_id}...")
    try:
        apex.settle(job_id)
        print(f"  settle({job_id}) success ✓")
    except Exception as exc:
        print(f"  [error] settle failed: {exc}")


def handle_disputed_job(
    apex: APEXClient,
    job_id: int,
    voter: str,
    gateway_url: str,
    hint_block: int | None = None,
) -> None:
    """Show job details and prompt voter to reject or skip."""
    print(f"\n{'='*60}")
    print(f"  DISPUTED job_id={job_id}")
    print(f"{'='*60}")

    already_voted = apex.policy.has_voted(job_id, voter)
    if already_voted:
        print("  Already voted on this job — skipping.")
        return

    # Read on-chain job details
    job = apex.get_job(job_id)
    print(f"  client   : {job.client}")
    print(f"  provider : {job.provider}")
    print(f"  budget   : {job.budget}")
    print(f"  status   : {job.status.name}")

    # Get deliverable_url from on-chain optParams
    deliverable_url = apex.get_deliverable_url(job_id, hint_block=hint_block)
    if deliverable_url:
        print(f"  IPFS URL : {deliverable_url}")
    else:
        print("  [warn] no deliverable_url found on-chain")

    # Fetch and display manifest
    manifest = None
    if deliverable_url:
        manifest = fetch_manifest(deliverable_url, gateway_url)

    if manifest:
        # Verify hash — use hint_block to stay within RPC block-range limits
        _fb = max(0, (hint_block or 0) - 5_000)
        _tb = (hint_block or 0) + 10 if hint_block else "latest"
        logs = apex.policy.contract.events.JobInitialised().get_logs(
            from_block=_fb, to_block=_tb, argument_filters={"jobId": job_id}
        )
        hash_ok = False
        if logs:
            on_chain_hash = logs[0]["args"]["deliverable"]
            hash_ok = manifest.verify(on_chain_hash)
        print(f"  hash ok  : {'✓' if hash_ok else '✗ MISMATCH'}")
        print(f"\n--- Deliverable content (job_id={manifest.job_id}) ---")
        content = manifest.response.get("content", "")
        if len(content) > 2000:
            print(content[:2000])
            print(f"\n  ... [{len(content) - 2000} more chars]")
        else:
            print(content)
        print("---")
    else:
        print("  (no manifest available for review)")

    # Prompt for vote
    try:
        choice = input("\n  [r]eject  [s]kip  > ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        print("\nstopped.")
        raise SystemExit(0)

    if choice == "r":
        print(f"  casting voteReject({job_id})...")
        apex.vote_reject(job_id)
        print(f"  voteReject({job_id}) submitted ✓")
        print(f"  (waiting for VoteCast event to check quorum...)")
    else:
        print(f"  skipped job {job_id}")


def main() -> None:
    load_dotenv(ROOT / ".env")

    pk = os.environ.get("VOTER_PRIVATE_KEY")
    if not pk:
        raise SystemExit("VOTER_PRIVATE_KEY is required in .env")

    network = os.environ.get("NETWORK", "bsc-testnet")
    gateway = os.environ.get("STORAGE_GATEWAY_URL", "https://gateway.pinata.cloud/ipfs/")

    from bnbagent.config import resolve_network
    nc = resolve_network(network)

    wallet = EVMWalletProvider(password="example", private_key=pk, persist=False)
    apex   = APEXClient(wallet, network=nc)
    voter  = apex.address

    quorum = apex.policy.vote_quorum()

    print(f"Voter watch loop")
    print(f"  network  : {nc.name}")
    print(f"  rpc      : {nc.rpc_url}")
    print(f"  policy   : {apex.policy.address}")
    print(f"  voter    : {voter}")
    print(f"  listed   : {apex.policy.is_voter(voter)}")
    print(f"  quorum   : {quorum}")
    print(f"  gateway  : {gateway}")
    print(f"\nWatching for Disputed / VoteCast events (Ctrl+C to stop)...\n")

    seen_disputed: set[int] = set()
    settled: set[int] = set()
    last_block = apex.w3.eth.block_number

    while True:
        head = apex.w3.eth.block_number
        if head > last_block:
            # --- Disputed events ------------------------------------------------
            disputed_logs = apex.policy.contract.events.Disputed().get_logs(
                from_block=last_block + 1,
                to_block=head,
            )
            for log in disputed_logs:
                job_id = log["args"]["jobId"]
                ts = time.strftime("%H:%M:%S")
                print(f"[{ts}] Disputed event — jobId={job_id}")
                if job_id not in seen_disputed:
                    seen_disputed.add(job_id)
                    handle_disputed_job(apex, job_id, voter, gateway, hint_block=log["blockNumber"])

            # --- VoteCast events ------------------------------------------------
            vote_logs = apex.policy.contract.events.VoteCast().get_logs(
                from_block=last_block + 1,
                to_block=head,
            )
            for log in vote_logs:
                job_id      = log["args"]["jobId"]
                reject_votes = log["args"]["rejectVotes"]
                caster      = log["args"]["voter"]
                ts = time.strftime("%H:%M:%S")
                print(f"[{ts}] VoteCast — jobId={job_id}  rejectVotes={reject_votes}/{quorum}  by={caster}")
                if reject_votes >= quorum and job_id not in settled:
                    settled.add(job_id)
                    handle_quorum_reached(apex, job_id, reject_votes, quorum)

            last_block = head
        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nstopped.")
