"""Flow B — dispute + quorum reject.

createJob → register → setBudget → fund → submit → client disputes →
whitelisted voter(s) voteReject (quorum met) → settle → REJECTED.

Client gets a refund; provider keeps nothing.
"""

from __future__ import annotations

import time

from _helpers import banner, load_settings, make_client, minutes_from_now

from bnbagent.apex import DeliverableManifest, JobStatus, SCHEMA_VERSION


def main() -> None:
    s = load_settings()
    client = make_client(s.client_pk, s.network)

    banner("DISPUTE REJECT — client disputes, voter rejects")

    decimals = client.token_decimals()
    budget = 1 * (10 ** decimals)
    expired_at = minutes_from_now(65)

    res = client.create_job(
        provider=s.provider_address,
        expired_at=expired_at,
        description="APEX demo: dispute-reject",
    )
    job_id = res["jobId"]
    print(f"[client] createJob jobId={job_id}")

    client.register_job(job_id)
    client.set_budget(job_id, budget)
    client.fund(job_id, budget)
    print("[client] registered + funded")

    if not s.provider_pk:
        print(f"\nProvider must submit jobId={job_id} before continuing.\n")
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
        response={"content": f"dispute test result for job {job_id}", "content_type": "text/plain"},
        submitted_at=int(time.time()),
    )
    # In production: upload manifest.to_dict() to IPFS/storage first, then pass the URL.
    # deliverable_url = storage.upload(manifest.to_dict(), f"job-{job_id}.json")
    deliverable_url = ""  # no storage in this example
    provider.submit(job_id, manifest.manifest_hash(), {"deliverable_url": deliverable_url})
    print("[provider] submit OK")

    client.dispute(job_id)
    print("[client] dispute raised")

    if not s.voter_pk:
        quorum = client.policy.vote_quorum()
        print(
            f"\nDispute raised on jobId={job_id}. Need {quorum} voteReject(s) from "
            f"whitelisted voters, then anyone can call settle. See examples/voter/.\n"
        )
        return

    voter = make_client(s.voter_pk, s.network)
    voter.vote_reject(job_id)
    print("[voter] voteReject cast")

    client.settle(job_id)
    job = client.get_job(job_id)
    assert job.status == JobStatus.REJECTED, f"expected REJECTED, got {job.status.name}"
    print(f"[client] settle OK -> {job.status.name}")


if __name__ == "__main__":
    main()
