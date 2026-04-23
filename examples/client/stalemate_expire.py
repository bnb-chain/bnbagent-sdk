"""Flow C — stalemate → expiry refund.

Client disputes but quorum is never reached. Settlement is impossible
because the policy stays PENDING. Once the job passes its ``expiredAt``
deadline, the client (or anyone) calls ``claimRefund`` — the universal
escape hatch on the kernel.

NOTE: ``expiredAt`` must be > jobExpiry; the demo uses a very small
``jobExpirySeconds`` so it completes during a run. For live networks set
``expired_at`` to something reasonable (hours / days).
"""

from __future__ import annotations

import time

from web3 import Web3

from _helpers import banner, load_settings, make_client, minutes_from_now

from bnbagent.apex import JobStatus


def main() -> None:
    s = load_settings()
    client = make_client(s.client_pk, s.network)

    banner("STALEMATE — dispute without quorum, refund at expiry")

    decimals = client.token_decimals()
    budget = 1 * (10 ** decimals)

    # Short expiry so the demo finishes in-session. Still >= the 5-minute
    # on-chain minimum; bump higher for mainnet.
    expired_at = minutes_from_now(6)

    res = client.create_job(
        provider=s.provider_address,
        expired_at=expired_at,
        description="APEX demo: stalemate",
    )
    job_id = res["jobId"]
    print(f"[client] createJob jobId={job_id} expiredAt={expired_at}")
    client.register_job(job_id)
    client.set_budget(job_id, budget)
    client.fund(job_id, budget)

    if not s.provider_pk:
        print(f"\nProvider must submit jobId={job_id} before continuing.\n")
        return
    provider = make_client(s.provider_pk, s.network)
    content_hash = Web3.keccak(text=f"stalemate-{job_id}")
    provider.submit(job_id, content_hash, deliverable_url="https://example.com/deliverable")
    print("[provider] submit OK")

    client.dispute(job_id)
    print("[client] dispute raised (no voter will act)")

    wait = expired_at - int(time.time()) + 3
    if wait > 0:
        print(f"[client] waiting {wait}s for expiry...")
        time.sleep(wait)

    client.claim_refund(job_id)
    job = client.get_job(job_id)
    assert job.status == JobStatus.EXPIRED, f"expected EXPIRED, got {job.status.name}"
    print(f"[client] claimRefund OK -> {job.status.name}")


if __name__ == "__main__":
    main()
