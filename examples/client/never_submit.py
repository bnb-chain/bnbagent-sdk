"""Flow D — provider never submits, client reclaims via expiry.

createJob → register → setBudget → fund → (provider silent) → wait past
``expiredAt`` → ``claimRefund`` → EXPIRED.

Wall-clock wait scales with the contract's ``disputeWindow`` because
``expiredAt`` must be at least ``disputeWindow`` in the future for
Commerce to accept the job.
"""

from __future__ import annotations

import time

from _helpers import banner, expiry_for, load_settings, make_client

from bnbagent.erc8183 import JobStatus


def main() -> None:
    s = load_settings()
    client = make_client(s.client_pk, s.network)

    banner("NEVER SUBMIT — provider silent, refund at expiry")

    decimals = client.token_decimals()
    budget = 1 * (10 ** decimals)
    expired_at = expiry_for(client, slack_minutes=1)

    res = client.create_job(
        provider=s.provider_address,
        expired_at=expired_at,
        description="ERC-8183 demo: never-submit",
    )
    job_id = res["jobId"]
    print(f"[client] createJob jobId={job_id} expiredAt={expired_at}")
    client.register_job(job_id)
    client.set_budget(job_id, budget)
    client.fund(job_id, budget)

    wait = expired_at - int(time.time()) + 3
    print(f"[client] waiting {wait}s for expiry (provider is silent)...")
    if wait > 0:
        time.sleep(wait)

    client.claim_refund(job_id)
    job = client.get_job(job_id)
    assert job.status == JobStatus.EXPIRED, f"expected EXPIRED, got {job.status.name}"
    print(f"[client] claimRefund OK -> {job.status.name}")


if __name__ == "__main__":
    main()
