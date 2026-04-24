"""Flow A — happy path.

createJob → registerJob → setBudget → fund → submit → wait past dispute
window → settle → COMPLETED.
"""

from __future__ import annotations

import time

from _helpers import banner, load_settings, make_client, minutes_from_now

from bnbagent.apex import DeliverableManifest, JobStatus, SCHEMA_VERSION


def main() -> None:
    s = load_settings()
    client = make_client(s.client_pk, s.network)

    banner("HAPPY — create + fund + submit + settle")

    decimals = client.token_decimals()
    budget = 1 * (10 ** decimals)  # 1 token

    expired_at = minutes_from_now(65)  # > dispute window + slack
    res = client.create_job(
        provider=s.provider_address,
        expired_at=expired_at,
        description="APEX demo: happy",
    )
    job_id = res["jobId"]
    print(f"[client] createJob jobId={job_id}")

    client.register_job(job_id)
    print("[client] registerJob -> OptimisticPolicy")

    client.set_budget(job_id, budget)
    print(f"[client] setBudget {budget / 10**decimals} {client.token_symbol()}")

    client.fund(job_id, budget)
    print("[client] fund OK (Open -> Funded)")

    if not s.provider_pk:
        print(
            "\nNo PROVIDER_PRIVATE_KEY set. Ask the provider to submit jobId="
            f"{job_id}, then rerun with --resume.\n"
        )
        return

    provider = make_client(s.provider_pk, s.network)
    manifest = DeliverableManifest(
        version=SCHEMA_VERSION,
        job_id=job_id,
        chain_id=provider.network.chain_id,
        contracts={
            "commerce": provider.commerce.address,
            "router": provider.router.address,
            "policy": provider.policy.address,
        },
        response={"content": f"happy path result for job {job_id}", "content_type": "text/plain"},
        submitted_at=0,  # back-filled with block.timestamp after submit
    )
    # In production: upload manifest.to_dict() to IPFS/storage first, then pass the URL.
    # deliverable_url = storage.upload(manifest.to_dict(), f"job-{job_id}.json")
    deliverable_url = ""  # no storage in this example
    receipt = provider.submit(job_id, manifest.manifest_hash(), {"deliverable_url": deliverable_url})
    manifest.submitted_at = provider.w3.eth.get_block(receipt["blockNumber"])["timestamp"]
    # In production: re-upload manifest.to_dict() now that submitted_at is set.
    print("[provider] submit OK (Funded -> Submitted)")

    window = client.policy.dispute_window()
    print(f"[client] waiting {window}s for dispute window to pass...")
    time.sleep(window + 2)

    client.settle(job_id)
    job = client.get_job(job_id)
    assert job.status == JobStatus.COMPLETED, f"expected COMPLETED, got {job.status.name}"
    print(f"[client] settle OK -> {job.status.name}")


if __name__ == "__main__":
    main()
