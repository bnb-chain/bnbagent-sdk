"""Flow E — client cancels before funding.

createJob → (no setBudget / no fund) → client ``reject`` → REJECTED.
No escrow ever moved, no provider action needed.
"""

from __future__ import annotations

from _helpers import banner, expiry_for, load_settings, make_client

from bnbagent.erc8183 import JobStatus


def main() -> None:
    s = load_settings()
    client = make_client(s.client_pk, s.network)

    banner("CANCEL OPEN — client cancels before funding")

    expired_at = expiry_for(client, slack_minutes=1)
    res = client.create_job(
        provider=s.provider_address,
        expired_at=expired_at,
        description="ERC-8183 demo: cancel-open",
    )
    job_id = res["jobId"]
    print(f"[client] createJob jobId={job_id}")

    client.register_job(job_id)
    print("[client] registerJob (optional, shown for completeness)")

    client.cancel_open(job_id)
    job = client.get_job(job_id)
    assert job.status == JobStatus.REJECTED, f"expected REJECTED, got {job.status.name}"
    print(f"[client] cancel OK -> {job.status.name} (no escrow moved)")


if __name__ == "__main__":
    main()
