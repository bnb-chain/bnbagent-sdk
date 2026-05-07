"""Integration test — client <-> agent-server with IPFS storage.

Flow:
  1. Client creates + registers + funds a job (provider = agent-server wallet)
  2. Agent-server's funded-job poll loop picks it up, searches news, uploads
     DeliverableManifest to Pinata IPFS, and calls submit() on-chain with the
     IPFS URL as deliverable_url
  3. Client polls until job reaches SUBMITTED
  4. Client reads the deliverable_url from the on-chain optParams and prints
     the Pinata gateway URL so we can verify the manifest
  5. Client disputes → voter voteReject → settle (skips the 600s window)

Run:
    # Terminal 1 — start the agent-server
    cd examples/agent-server && uv run python src/service.py

    # Terminal 2 — run this script
    cd examples/client && python agent_ipfs_test.py
"""

from __future__ import annotations

import time

from _helpers import banner, load_settings, make_client, minutes_from_now

POLL_INTERVAL = 5    # seconds between status polls
POLL_TIMEOUT  = 240  # allow for one full poll cycle + on-chain submission


def main() -> None:
    s = load_settings()
    client = make_client(s.client_pk, s.network)

    banner("AGENT + IPFS — client funds job, agent submits to IPFS, dispute→settle")

    decimals = client.token_decimals()
    budget   = 1 * (10 ** decimals)

    # --- 1. Create + register + fund ----------------------------------------
    expired_at = minutes_from_now(65)
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
    from bnbagent.apex import JobStatus
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
    deliverable_url = client.commerce.get_deliverable_url(job_id)
    print(f"  deliverableUrl:  {deliverable_url}")
    if deliverable_url.startswith("ipfs://"):
        cid = deliverable_url[len("ipfs://"):]
        gateway_url = f"https://gateway.pinata.cloud/ipfs/{cid}"
        print(f"\n[client] fetching manifest from IPFS: {gateway_url}")
        from bnbagent.apex.schema import DeliverableManifest
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

    # --- 5. Dispute — voter reviews via watch.py ----------------------------
    print("\n[client] raising dispute...")
    client.dispute(job_id)
    print(f"[client] dispute({job_id}) OK")
    print(f"\n  job {job_id} is now DISPUTED")
    print(f"  → voter can review and vote in watch.py")
    print(f"  → after voting, settle with: client.settle({job_id})")


if __name__ == "__main__":
    main()
