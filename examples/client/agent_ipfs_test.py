"""Integration test — client <-> agent-server with IPFS storage.

Flow:
  1. Client creates + registers + funds a job (provider = agent-server wallet)
  2. Agent-server picks it up, searches news, uploads DeliverableManifest to
     Pinata IPFS, and calls submit() on-chain with the IPFS URL as deliverable_url
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

POLL_INTERVAL = 5   # seconds between status polls
POLL_TIMEOUT  = 180 # give agent up to 3 min to submit


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

    # --- 2. Trigger the agent via its HTTP endpoint -------------------------
    import httpx
    agent_url = "http://localhost:8003/apex/job/execute"
    print(f"\n[client] triggering agent via POST {agent_url} ...")
    resp = httpx.post(agent_url, json={"job_id": job_id, "timeout": POLL_TIMEOUT}, timeout=POLL_TIMEOUT + 5)
    agent_result = resp.json()
    print(f"  status:          {resp.status_code}")
    print(f"  success:         {agent_result.get('success')}")
    print(f"  txHash:          {agent_result.get('txHash')}")
    print(f"  deliverableUrl:  {agent_result.get('deliverableUrl')}")
    print(f"  deliverable:     {agent_result.get('deliverable')}")

    # --- 3. Confirm job reached SUBMITTED -----------------------------------
    from bnbagent.apex import JobStatus
    job = client.get_job(job_id)
    if job.status != JobStatus.SUBMITTED:
        deadline = time.time() + 30
        while time.time() < deadline:
            job = client.get_job(job_id)
            if job.status == JobStatus.SUBMITTED:
                break
            time.sleep(POLL_INTERVAL)
    if job.status != JobStatus.SUBMITTED:
        print(f"\n[client] job {job_id} is {job.status.name} — expected SUBMITTED, aborting")
        return
    print(f"\n[client] job {job_id} is SUBMITTED ✓")

    # --- 4. Verify manifest hash via IPFS -----------------------------------
    deliverable_url = agent_result.get("deliverableUrl", "")
    if deliverable_url.startswith("ipfs://"):
        cid = deliverable_url[len("ipfs://"):]
        gateway_url = f"https://gateway.pinata.cloud/ipfs/{cid}"
        print(f"\n[client] fetching manifest from IPFS: {gateway_url}")
        from bnbagent.apex.schema import DeliverableManifest
        try:
            fetch = httpx.get(gateway_url, timeout=15)
            fetch.raise_for_status()
            manifest = DeliverableManifest.from_dict(fetch.json())
            on_chain_hex = agent_result.get("deliverable", "")
            on_chain_hash = bytes.fromhex(on_chain_hex[2:] if on_chain_hex.startswith("0x") else on_chain_hex)
            match = manifest.verify(on_chain_hash)
            print(f"  manifest.job_id    : {manifest.job_id}")
            print(f"  manifest.chain_id  : {manifest.chain_id}")
            print(f"  response length    : {len(manifest.response.get('content', ''))} chars")
            print(f"  hash matches chain : {'✓ YES' if match else '✗ MISMATCH'}")
        except Exception as e:
            print(f"  could not verify manifest: {e}")
    else:
        print("\n[client] no IPFS URL in agent response — skipping manifest verification")

    # --- 5. Dispute — voter reviews via watch.py ----------------------------
    print("\n[client] raising dispute...")
    client.dispute(job_id)
    print(f"[client] dispute({job_id}) OK")
    print(f"\n  job {job_id} is now DISPUTED")
    print(f"  → voter can review and vote in watch.py")
    print(f"  → after voting, settle with: client.settle({job_id})")


if __name__ == "__main__":
    main()
