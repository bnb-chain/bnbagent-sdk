"""Flow E — client sends a job to a live agent-server and verifies the result.

Default flow:
  1. Client creates + registers + funds a job (provider = agent-server wallet)
  2. Agent-server's funded-job poll loop picks it up, processes the request,
     uploads DeliverableManifest to storage, and calls submit() on-chain
  3. Client polls until job reaches SUBMITTED
  4. Client reads deliverable_url from the chain and verifies the manifest hash

With ``--dispute``:
  5. Client raises dispute(jobId) — voter then reviews via examples/voter/watch.py

Run:
    # Terminal 1 — start the agent-server
    cd examples/agent-server && uv run python scripts/run_agent.py

    # Terminal 2 — run this script (default: verify manifest after submit)
    cd examples/client && uv run python create_and_verify.py
    # Or raise a dispute after submit:
    uv run python create_and_verify.py --dispute
"""

from __future__ import annotations

import argparse
import time

from _helpers import banner, expiry_for, load_settings, make_client

POLL_INTERVAL = 5    # seconds between status polls
POLL_TIMEOUT  = 240  # allow for one full poll cycle + on-chain submission


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--dispute",
        action="store_true",
        help="After SUBMITTED, raise a dispute to exercise the voter / settle branch.",
    )
    args = parser.parse_args()

    s = load_settings()
    client = make_client(s.client_pk, s.network)

    banner(
        "AGENT + IPFS — fund job, agent submits to IPFS, "
        + ("dispute → voter" if args.dispute else "verify manifest")
    )

    decimals = client.token_decimals()
    budget   = 1 * (10 ** decimals)

    # --- 1. Create + register + fund ----------------------------------------
    expired_at = expiry_for(client)
    res = client.create_job(
        provider=s.provider_address,
        expired_at=expired_at,
        description="Latest BNB Chain ecosystem news",
    )
    job_id = res["jobId"]
    print(f"[client] createJob jobId={job_id}")

    client.register_job(job_id)
    print("[client] registerJob -> OptimisticPolicy")

    client.set_budget(job_id, budget)
    print(f"[client] setBudget {budget / 10**decimals} {client.token_symbol()}")

    client.fund(job_id, budget)
    print("[client] fund OK (Open -> Funded)")

    # --- 2. Wait for the agent's funded-poll loop to pick up the job --------
    from bnbagent.erc8183 import JobStatus
    print(f"\n[client] waiting for agent to submit (up to {POLL_TIMEOUT}s)...")
    deadline = time.time() + POLL_TIMEOUT
    job = client.get_job(job_id)
    while time.time() < deadline and job.status != JobStatus.SUBMITTED:
        time.sleep(POLL_INTERVAL)
        job = client.get_job(job_id)

    # --- 3. Confirm job reached SUBMITTED -----------------------------------
    if job.status != JobStatus.SUBMITTED:
        print(f"\n[client] job {job_id} is {job.status.name} — expected SUBMITTED, aborting")
        return
    print(f"[client] job {job_id} is SUBMITTED ✓")

    # --- 4. Verify manifest hash via IPFS -----------------------------------
    import httpx
    deliverable_url = client.get_deliverable_url(job_id)
    print(f"  deliverableUrl:  {deliverable_url}")
    if deliverable_url and deliverable_url.startswith("ipfs://"):
        cid = deliverable_url[len("ipfs://"):]
        gateway_url = f"https://gateway.pinata.cloud/ipfs/{cid}"
        print(f"\n[client] fetching manifest from IPFS: {gateway_url}")
        from bnbagent.erc8183.schema import DeliverableManifest
        try:
            fetch = httpx.get(gateway_url, timeout=15)
            fetch.raise_for_status()
            manifest = DeliverableManifest.from_dict(fetch.json())
            match = manifest.verify(job.deliverable)
            print(f"  manifest.job_id    : {manifest.job_id}")
            print(f"  manifest.chain_id  : {manifest.chain_id}")
            print(f"  response length    : {len(manifest.response.get('content', ''))} chars")
            print(f"  hash matches chain : {'✓ YES' if match else '✗ MISMATCH'}")
        except Exception as e:
            print(f"  could not verify manifest: {e}")
    else:
        print("\n[client] no IPFS URL on-chain — skipping manifest verification")

    # --- 5. Dispute (only when --dispute) -----------------------------------
    if args.dispute:
        print("\n[client] raising dispute...")
        client.dispute(job_id)
        print(f"[client] dispute({job_id}) OK")
        print(f"\n  job {job_id} is now DISPUTED")
        print(f"  → voter can review and vote in examples/voter/watch.py")
        print(f"  → after quorum, anyone can call settle({job_id})")
    else:
        print(
            f"\n  job {job_id} stays SUBMITTED. After disputeWindow elapses without"
            " dispute, anyone can settle to APPROVE → COMPLETED."
        )


if __name__ == "__main__":
    main()
