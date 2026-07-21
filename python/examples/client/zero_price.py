"""Flow F — zero price.

A job agreed at price 0 (``budget == 0``). The escrow state machine is
unchanged (Open → Funded → Submitted → Completed), but no tokens ever
move: ``fund(0)`` deposits nothing (and the SDK skips the ERC-20 approve
entirely), and ``settle`` pays nobody.

createJob → registerJob → set_budget(0) → fund(0) → submit → wait
past dispute window → settle → COMPLETED, with the provider's token balance
unchanged.

``set_budget(0)`` may be sent by either the client or the provider — same
rule as any other amount. This demo drives the buyer flow (the CLIENT sets
the budget) and still REQUIRES ``PROVIDER_PRIVATE_KEY`` for the
provider-only ``submit``.
"""

from __future__ import annotations

import time

from _helpers import banner, expiry_for, load_settings, make_client, make_primary_client

from bnbagent.erc8183 import DeliverableManifest, JobStatus, SCHEMA_VERSION


def main() -> None:
    s = load_settings()
    if not s.provider_pk:
        raise RuntimeError(
            "PROVIDER_PRIVATE_KEY is required for the zero-price flow: "
            "submit is provider-only."
        )
    client = make_primary_client(s)  # EVM or twak, per WALLET_KIND
    provider = make_client(s.provider_pk, s.network)

    banner("ZERO-PRICE — seller offers a free job, nobody is paid")

    expired_at = expiry_for(client)  # disputeWindow + 10 min slack
    res = client.create_job(
        provider=s.provider_address,
        expired_at=expired_at,
        description="ERC-8183 demo: zero-price",
    )
    job_id = res["jobId"]
    print(f"[client] createJob jobId={job_id}")

    client.register_job(job_id)
    print("[client] registerJob -> OptimisticPolicy")

    # The client sets the zero budget — set_budget(0) is symmetric, the
    # provider could equally send it.
    client.set_budget(job_id, 0)
    print("[client] setBudget 0 (zero price)")

    provider_balance_before = client.token_balance(s.provider_address)

    # fund(0) moves no tokens and needs no allowance — the SDK detects
    # amount == 0 and skips the ERC-20 approve entirely.
    client.fund(job_id, 0)
    print("[client] fund 0 OK (Open -> Funded, no transfer, no approve)")

    manifest = DeliverableManifest(
        version=SCHEMA_VERSION,
        job_id=job_id,
        chain_id=provider.network.chain_id,
        contracts={
            "commerce": provider.commerce.address,
            "router": provider.router.address,
            "policy": provider.policy.address,
        },
        response={"content": f"zero-price result for job {job_id}", "content_type": "text/plain"},
    )
    deliverable_url = "https://example.invalid/manifest.json"  # placeholder — these scripts test on-chain flow only
    provider.submit(job_id, manifest.manifest_hash(), {"deliverable_url": deliverable_url})
    print("[provider] submit OK (Funded -> Submitted)")

    window = client.policy.dispute_window()
    print(f"[client] waiting {window}s for dispute window to pass...")
    time.sleep(window + 2)

    client.settle(job_id)
    job = client.get_job(job_id)
    assert job.status == JobStatus.COMPLETED, f"expected COMPLETED, got {job.status.name}"
    print(f"[client] settle OK -> {job.status.name}")

    provider_balance_after = client.token_balance(s.provider_address)
    assert provider_balance_after == provider_balance_before, (
        "expected provider balance unchanged for a free job, "
        f"before={provider_balance_before} after={provider_balance_after}"
    )
    print(f"[client] provider balance unchanged ({provider_balance_after}) — nobody was paid")


if __name__ == "__main__":
    main()
