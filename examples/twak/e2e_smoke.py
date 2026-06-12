"""bsctestnet end-to-end smoke for the TWAK intent dispatch.

Closes the design-doc backlog item "bsctestnet 真实冒烟（13 个 intent 全生命周期）"
(docs/twak-integration-design.md §8): drives the twak wallet through the real
ERC-8004/8183 contracts on BSC testnet and asserts each step on-chain, in the
assert-chain style of examples/security_e2e.py.

Requires twak >= v0.19.0 (`submit --opt-params`, `fund --expected-budget` —
REQ-1/S-1/S-2 shipped; an older CLI fails loudly with an upgrade hint).

⚠️  THIS SCRIPT SPENDS TESTNET FUNDS AND TAKES WALL-CLOCK TIME. ⚠️
    - testnet BNB for gas on ~15 transactions (on bsctestnet twak pays its
      own gas — sponsorship is mainnet-only so far, gaps REQ-2)
    - 2 × 0.01 test-U escrowed, both of which come back: job A pays out to
      the twak wallet itself (it plays client AND provider), job C is
      refunded at expiry
    - total runtime ≈ the policy's dispute window (24 h on this testnet
      deployment) + ~10 minutes of transactions (window printed
      up front; the OptimisticPolicy window on the target network decides)

Since v0.19.0 (REQ-1) the twak wallet plays BOTH roles on each job — client
and provider — as a self-deal: a host has exactly ONE twak wallet (per HOME),
so two *distinct* twak parties on one machine are impossible, but the same
wallet on both sides is fine for a smoke and was proven on-chain (job 150).
No EVM key is needed except the optional whitelisted voter.

Coverage strategy — correctness over coverage (the policy semantics decide
what can honestly be asserted; see the table printed at the end):

    job A  happy path WITHOUT dispute: create → set_provider (the twak
           wallet itself) → set_budget → register_job → fund → twak submit
           WITH {"deliverable_url": …} opt_params (positive, REQ-1 shipped
           v0.19.0; asserts SUBMITTED + on-chain deliverable == manifest
           hash) → wait out the dispute window → twak settle → COMPLETED.
           "Silence approves": there is no voteApprove on-chain, so an
           undisputed job settles APPROVE once submittedAt+disputeWindow
           passes — that is the only state a twak `settle` can be asserted
           against without voters.
    job B  cancel-open: create → twak reject while Open → REJECTED.
    job C  dispute stalemate: create(+provider=twak itself) → budget →
           register → fund → twak submit → twak dispute → [optional EVM
           voter vote_reject] → verdict stays PENDING (quorum, snapshotted
           at dispute time, cannot be reached by one vote, and a disputed
           job can NOT be settled while PENDING) → wait past expiredAt →
           twak claim_refund → EXPIRED → twak mark_expired (router
           reconcile).

    NOT exercised (with reasons, mirroring the gaps-doc verification-log
    style): twak complete — evaluator-only, and on router-registered jobs
    the evaluator is the Router contract, so no EOA may call it (routed
    completion happens inside settle); twak vote_reject — requires the twak
    wallet to be a whitelisted voter on OptimisticPolicy (an on-chain
    permission, not a wallet capability).

Environment (.env / .env.local next to this script, see .env.example):
    NETWORK               bsc-testnet (default; the only supported value —
                          this smoke deliberately refuses mainnet)
    VOTER_PRIVATE_KEY     optional. Whitelisted-voter EVM key; casts one
                          (non-flipping) vote_reject on job C. The only EVM
                          key left — since v0.19.0 the provider (seller)
                          role is played by the twak wallet itself, so the
                          old PROVIDER_PRIVATE_KEY is gone.
    AGENT_URI             optional. When set, step 1 registers an ERC-8004
                          agent via the SDK ContractInterface intent path
                          (mints a new agent NFT every run — hence opt-in).
    RPC_URL               optional RPC override (e.g. NodeReal).
    TWAK_BIN              optional twak binary override.
    TWAK_WALLET_PASSWORD  if not stored in the OS keychain.

Usage:
    python examples/twak/e2e_smoke.py
"""

from __future__ import annotations

import dataclasses
import os
import sys
import time
from pathlib import Path

from bnbagent import load_env
from bnbagent.config import resolve_network
from bnbagent.erc8004.contract import ContractInterface
from bnbagent.erc8183 import (
    SCHEMA_VERSION,
    ZERO_ADDRESS,
    DeliverableManifest,
    ERC8183Client,
    JobStatus,
    Verdict,
)
from bnbagent.wallets import EVMWalletProvider, TWAKProvider

HERE = Path(__file__).resolve().parent

# The documented twak capability set (bnbagent/wallets/README.md) — asserted
# verbatim in step 0 so a capability drift fails the smoke immediately.
EXPECTED_TWAK_CAPS = frozenset(
    {"sign.message", "broadcast.self", "intents.erc8004", "intents.erc8183", "x402.pay"}
)

# twak's chain key for BSC testnet ("bsc-testnet" is the SDK's network name;
# the CLI's key is "bsctestnet" — field-verified, the hyphenated form is
# rejected with CHAIN_UNSUPPORTED).
TWAK_CHAIN = "bsctestnet"

DELIVERABLE_URL = "https://example.invalid/manifest.json"  # on-chain flow only

# Minimum preflight balances. Gas: ~15 testnet txs with headroom. Escrow:
# two job budgets (A pays out to the twak wallet itself, C is refunded).
MIN_BNB_WEI = 5 * 10**15  # 0.005 tBNB


# ── plumbing ───────────────────────────────────────────────────────────────


coverage: dict[str, tuple[str, str]] = {}  # intent -> (status, note)


def mark(intent: str, status: str, note: str = "") -> None:
    coverage[intent] = (status, note)


def step(n: int | str, msg: str) -> None:
    print()
    print(f"── step {n}: {msg} " + "─" * max(0, 50 - len(msg)))


def ok(n: int | str, msg: str, tx: str | None = None) -> None:
    suffix = f" tx={tx}" if tx else ""
    print(f"   PASS [{n}] {msg}{suffix}")


def fail(n: int | str, msg: str) -> AssertionError:
    return AssertionError(f"step {n} FAILED: {msg}")


def wait_for(label: str, predicate, *, timeout: int = 180, interval: int = 5) -> None:
    """Poll an on-chain predicate until true (twak returns a tx hash but no
    receipt, so state changes are confirmed by reading the chain)."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            if predicate():
                return
        except Exception:  # noqa: BLE001 - transient RPC reads during polling
            pass
        time.sleep(interval)
    raise AssertionError(f"timed out after {timeout}s waiting for {label}")


def wait_until(ts: int, label: str) -> None:
    delta = ts - int(time.time())
    if delta > 0:
        print(f"   waiting {delta}s for {label}...")
        time.sleep(delta)


def make_manifest(client: ERC8183Client, job_id: int, text: str) -> DeliverableManifest:
    """Mirror examples/client/happy.py's manifest construction."""
    return DeliverableManifest(
        version=SCHEMA_VERSION,
        job_id=job_id,
        chain_id=client.network.chain_id,
        contracts={
            "commerce": client.commerce.address,
            "router": client.router.address,
            "policy": client.policy.address,
        },
        response={"content": text, "content_type": "text/plain"},
    )


def make_clients() -> tuple[TWAKProvider, ERC8183Client, ERC8183Client | None, str]:
    """Build the twak wallet+client and the optional EVM voter client."""
    network_name = os.environ.get("NETWORK", "bsc-testnet")
    if network_name != "bsc-testnet":
        raise SystemExit(
            f"NETWORK={network_name!r} refused: this smoke spends funds and is "
            "written for bsc-testnet only."
        )
    nc = resolve_network(network_name)
    rpc_url = os.environ.get("RPC_URL")
    if rpc_url:
        nc = dataclasses.replace(nc, rpc_url=rpc_url)

    twak_wallet = TWAKProvider(
        chain=TWAK_CHAIN, twak_bin=os.environ.get("TWAK_BIN", "twak")
    )
    twak_client = ERC8183Client(wallet_provider=twak_wallet, network=nc)

    # persist=False: ephemeral in-memory wrap of a raw testnet key — demo
    # pattern only (same as examples/client/_helpers.make_wallet). Only the
    # optional voter is EVM now; the seller role is twak itself (REQ-1
    # shipped in v0.19.0).
    voter_pk = os.environ.get("VOTER_PRIVATE_KEY")
    voter_client = (
        ERC8183Client(
            EVMWalletProvider(password="example", private_key=voter_pk, persist=False),
            network=nc,
        )
        if voter_pk
        else None
    )
    return twak_wallet, twak_client, voter_client, network_name


# ── the assert chain ───────────────────────────────────────────────────────


def main() -> int:  # noqa: PLR0915 - a linear assert chain reads best inline
    load_env(root=HERE)  # real env > .env.local > .env (never overrides)

    print("twak bsctestnet smoke — 13-intent lifecycle")
    print("⚠️  spends testnet BNB (gas); 0.02 test-U is escrowed but returns "
          "(job A self-deals, job C refunds).")

    twak_wallet, twak, voter, network_name = make_clients()
    twak_addr = twak.address  # the ERC8183Client's account == the twak wallet
    window = int(twak.policy.dispute_window())
    # The settle/claim_refund steps wait out the REAL on-chain dispute window —
    # on this testnet deployment that is 24 HOURS (86400s), so steps 11-13 are
    # an overnight tail, not a coffee break. Everything up to step 10 runs in
    # minutes; aborting mid-wait still prints the coverage table, and the tail
    # can be finished later (settle/claim-refund/mark-expired are permissionless
    # twak intents against the printed jobIds).
    print(
        f"⚠️  steps 11-13 wait out the on-chain dispute window: {window}s "
        f"({window / 3600:.1f} h). Ctrl-C mid-wait keeps all prior PASSes."
    )
    decimals = twak.token_decimals()
    symbol = twak.token_symbol()
    budget = 10**decimals // 100  # 0.01 test-U in atomic units

    print(f"network        : {network_name} (chainId={twak.network.chain_id})")
    print(f"twak wallet    : {twak_addr} (client/buyer AND provider/seller — "
          "self-deal; one twak wallet per host)")
    print(f"dispute window : {window}s ({window / 60:.1f} min)")
    print(f"job budget     : {budget} atomic ({budget / 10**decimals} {symbol})")

    # ── step 0: capabilities + balance preflight ──────────────────────────
    step(0, "capabilities + preflight")
    caps = twak_wallet.capabilities()
    if caps != EXPECTED_TWAK_CAPS:
        raise fail(0, f"capability drift: {sorted(caps)} != {sorted(EXPECTED_TWAK_CAPS)}")
    bnb = twak.w3.eth.get_balance(twak_addr)
    u_bal = twak.token_balance(twak_addr)
    print(f"   twak balances: {bnb / 10**18:.6f} tBNB, {u_bal / 10**decimals} {symbol}")
    needed_u = 2 * budget
    if bnb < MIN_BNB_WEI or u_bal < needed_u:
        raise fail(
            0,
            f"FUND ME: the twak wallet {twak_addr} needs >= "
            f"{MIN_BNB_WEI / 10**18} tBNB for gas (has {bnb / 10**18:.6f}) and >= "
            f"{needed_u / 10**decimals} {symbol} for escrow "
            f"(has {u_bal / 10**decimals}). Top it up on {network_name} and rerun.",
        )
    ok(0, f"capabilities match the documented set ({len(caps)}), balances sufficient")

    # ── step 1: erc8004.register (atomic --metadata), opt-in ──────────────
    step(1, "erc8004.register with metadata (twak)")
    agent_uri = os.environ.get("AGENT_URI")
    if agent_uri:
        registry = ContractInterface(
            web3=twak.w3,
            contract_address=resolve_network(network_name).registry_contract,
            wallet_provider=twak_wallet,
        )
        # Two explicit entries; the SDK appends built_with itself. On twak
        # all of them ride the register tx as repeatable --metadata flags
        # (v0.18.0: metadata is atomic with the mint).
        reg = registry.register_agent(
            agent_uri=agent_uri,
            metadata=[
                {"key": "suite", "value": "examples/twak/e2e_smoke"},
                {"key": "role", "value": "smoke-client"},
            ],
        )
        if reg.get("agentId") is None:
            raise fail(1, f"no agentId in register result: {reg}")
        mark("erc8004.register", "exercised", f"agentId={reg['agentId']}")
        ok(1, f"registered agentId={reg['agentId']}", reg.get("transactionHash"))
    else:
        mark("erc8004.register", "skipped", "AGENT_URI unset (mints an NFT per run)")
        print("   SKIP [1] AGENT_URI not set — registration mints a new agent each run")

    # ── job A: happy path (settle without dispute) ────────────────────────
    # expiredAt must leave the provider room to submit before
    # expiredAt - disputeWindow (SubmissionTooLate otherwise) — 20 min slack.
    step(2, "erc8183.create_job — job A, provider=ZERO initially (twak)")
    expired_a = int(time.time()) + window + 20 * 60
    res = twak.create_job(
        provider=ZERO_ADDRESS,
        expired_at=expired_a,
        description="twak e2e smoke: job A (happy path)",
    )
    if res.get("jobId") is None:
        raise fail(2, f"create_job returned no jobId: {res}")
    job_a = int(res["jobId"])
    mark("erc8183.create_job", "exercised", f"jobs A/B/C (A={job_a})")
    ok(2, f"job A created, jobId={job_a}", res.get("transactionHash"))

    step(3, "erc8183.set_provider — point job A at the twak wallet itself (twak)")
    # The twak wallet plays the seller too (self-deal): one twak wallet per
    # host, and since v0.19.0 (REQ-1) it can submit — proven on job 150.
    res = twak.set_provider(job_a, twak_addr)
    wait_for(
        f"job {job_a} provider == {twak_addr}",
        lambda: twak.get_job(job_a).provider.lower() == twak_addr.lower(),
    )
    mark("erc8183.set_provider", "exercised", f"job A -> {twak_addr[:10]}… (self)")
    ok(3, "provider set on-chain (the twak wallet itself)", res.get("transactionHash"))

    step(4, f"erc8183.set_budget — {budget} atomic (twak)")
    res = twak.set_budget(job_a, budget)
    wait_for(
        f"job {job_a} budget == {budget}",
        lambda: twak.get_job(job_a).budget == budget,
    )
    mark("erc8183.set_budget", "exercised", "jobs A and C")
    ok(4, f"budget = {budget} atomic on-chain", res.get("transactionHash"))

    step(5, "erc8183.register_job — bind OptimisticPolicy via Router (twak)")
    res = twak.register_job(job_a)
    mark("erc8183.register_job", "exercised", "jobs A and C")
    ok(5, "registered with the policy router", res.get("transactionHash"))

    step(6, "erc8183.fund — approve+deposit bundled, atomic --expected-budget (twak)")
    # fund_bundles_approval=True: the SDK skips its own allowance top-up;
    # twak approves + deposits itself. The amount is pinned on-chain via
    # `--expected-budget` (S-2, shipped v0.19.0): the contract reverts with
    # BudgetMismatch() on drift — the old client-side status pre-check is
    # gone, so reaching FUNDED proves the atomic guard was satisfied.
    res = twak.fund(job_a, budget)
    wait_for(
        f"job {job_a} FUNDED",
        lambda: twak.get_job_status(job_a) == JobStatus.FUNDED,
    )
    mark("erc8183.fund", "exercised", "atomic --expected-budget matched (S-2)")
    extra = f" approveHash={res['approveHash']}" if res.get("approveHash") else ""
    ok(6, f"escrow funded (--expected-budget matched){extra}", res.get("transactionHash"))

    step(7, "erc8183.submit — twak submits WITH deliverable_url (REQ-1, v0.19.0)")
    # POSITIVE since v0.19.0: `submit --opt-params` passes the
    # {"deliverable_url": …} JSON through raw, so the policy's JobInitialised
    # event carries it and the job is evaluable (on-chain proof: job 150).
    manifest = make_manifest(twak, job_a, f"smoke deliverable for job {job_a}")
    res = twak.submit(
        job_a, manifest.manifest_hash(), {"deliverable_url": DELIVERABLE_URL}
    )
    wait_for(
        f"job {job_a} SUBMITTED",
        lambda: twak.get_job_status(job_a) == JobStatus.SUBMITTED,
    )
    if twak.get_job(job_a).deliverable != manifest.manifest_hash():
        raise fail(7, "on-chain deliverable != the submitted manifest hash")
    mark("erc8183.submit", "exercised",
         "positive, REQ-1 shipped v0.19.0; deliverable hash matches")
    ok(7, "twak submitted; SUBMITTED + deliverable == manifest hash",
       res.get("transactionHash"))

    # ── job B: cancel while Open ──────────────────────────────────────────
    step(8, "erc8183.reject — cancel-open, job B (twak)")
    res = twak.create_job(
        provider=ZERO_ADDRESS,
        expired_at=int(time.time()) + window + 20 * 60,
        description="twak e2e smoke: job B (cancel-open)",
    )
    job_b = int(res["jobId"])
    print(f"   job B created, jobId={job_b} tx={res.get('transactionHash')}")
    res = twak.cancel_open(job_b)  # -> commerce.reject while Open, no escrow moved
    wait_for(
        f"job {job_b} REJECTED",
        lambda: twak.get_job_status(job_b) == JobStatus.REJECTED,
    )
    mark("erc8183.reject", "exercised", f"job B={job_b} cancel-open -> REJECTED")
    ok(8, "job B rejected while Open (no escrow moved)", res.get("transactionHash"))

    # ── job C: dispute → stalemate → refund at expiry ─────────────────────
    # The OptimisticPolicy is "silence approves" with a reject-only quorum:
    # once disputed, the verdict stays PENDING until quorum reject votes
    # arrive — and a PENDING job cannot be settled, even after the window.
    # The kernel's universal escape hatch is claimRefund after expiredAt, so
    # C's expiry is kept as tight as the submit deadline allows (the
    # provider must submit before expiredAt - disputeWindow).
    step(9, "erc8183.dispute — job C (twak)")
    slack_c = 8 * 60
    expired_c = int(time.time()) + window + slack_c
    res = twak.create_job(
        provider=twak_addr,  # set directly; set_provider already covered
        expired_at=expired_c,
        description="twak e2e smoke: job C (dispute stalemate)",
    )
    job_c = int(res["jobId"])
    print(f"   job C created, jobId={job_c} tx={res.get('transactionHash')}")
    twak.set_budget(job_c, budget)
    wait_for(f"job {job_c} budget", lambda: twak.get_job(job_c).budget == budget)
    twak.register_job(job_c)
    twak.fund(job_c, budget)
    wait_for(
        f"job {job_c} FUNDED",
        lambda: twak.get_job_status(job_c) == JobStatus.FUNDED,
    )
    manifest_c = make_manifest(twak, job_c, f"smoke deliverable for job {job_c}")
    twak.submit(job_c, manifest_c.manifest_hash(), {"deliverable_url": DELIVERABLE_URL})
    wait_for(
        f"job {job_c} SUBMITTED",
        lambda: twak.get_job_status(job_c) == JobStatus.SUBMITTED,
    )
    print(f"   job C funded + submitted by twak (expiredAt={expired_c})")
    res = twak.dispute(job_c)  # client-only, within the dispute window
    wait_for(f"job {job_c} disputed", lambda: twak.policy.disputed(job_c))
    verdict, _ = twak.get_verdict(job_c)
    if verdict != Verdict.PENDING:
        raise fail(9, f"expected PENDING right after dispute, got {verdict.name}")
    quorum = twak.dispute_quorum_snapshot(job_c)
    mark("erc8183.dispute", "exercised", f"job C={job_c}, quorum snapshot={quorum}")
    ok(9, f"disputed; verdict=PENDING, quorum snapshot={quorum}",
       res.get("transactionHash"))

    # ── step 10: optional single voter (EVM) — cannot flip the verdict ────
    step(10, "vote_reject — optional, via the EVM voter (not a twak intent)")
    c_settled_by_quorum = False
    if voter is not None:
        voter_addr = voter.address
        if not twak.policy.is_voter(voter_addr):
            mark("erc8183.vote_reject", "skipped",
                 f"VOTER {voter_addr[:10]}… not whitelisted on OptimisticPolicy")
            print(f"   SKIP [10] {voter_addr} is not a whitelisted voter")
        else:
            before = twak.policy.reject_votes(job_c)
            res = voter.vote_reject(job_c)
            wait_for(
                f"job {job_c} rejectVotes > {before}",
                lambda: twak.policy.reject_votes(job_c) > before,
            )
            votes = twak.policy.reject_votes(job_c)
            print(f"   rejectVotes={votes} / quorum={quorum}")
            if votes >= quorum:
                # Quorum reached (e.g. a 1-vote test policy): the verdict
                # flips to REJECT and settle applies it.
                res = twak.settle(job_c)
                wait_for(
                    f"job {job_c} REJECTED",
                    lambda: twak.get_job_status(job_c) == JobStatus.REJECTED,
                )
                c_settled_by_quorum = True
                mark("erc8183.vote_reject", "exercised (EVM voter)",
                     "quorum met -> settle -> REJECTED")
                ok(10, "quorum met; job C settled REJECTED",
                   res.get("transactionHash"))
            else:
                mark("erc8183.vote_reject", "exercised (EVM voter)",
                     f"{votes}/{quorum} votes — verdict stays PENDING by design")
                ok(10, f"vote counted ({votes}/{quorum}); verdict stays PENDING")
    else:
        mark("erc8183.vote_reject", "skipped",
             "no VOTER_PRIVATE_KEY; twak-side also impossible (twak wallet is "
             "not a whitelisted voter — on-chain permission, not a wallet gap)")
        print("   SKIP [10] VOTER_PRIVATE_KEY not set")

    # ── step 11: settle job A after the window (silence approves) ─────────
    step(11, "erc8183.settle — job A after the dispute window (twak)")
    submitted_at = twak.get_job(job_a).submitted_at
    wait_until(submitted_at + window + 5, "job A's dispute window to elapse")
    res = twak.settle(job_a)  # permissionless: applies the policy verdict
    wait_for(
        f"job {job_a} COMPLETED",
        lambda: twak.get_job_status(job_a) == JobStatus.COMPLETED,
    )
    mark("erc8183.settle", "exercised", f"job A={job_a} -> COMPLETED (no dispute)")
    ok(11, "job A settled -> COMPLETED (escrow back to the twak wallet — "
           "it is the provider)", res.get("transactionHash"))

    # ── steps 12/13: refund + reconcile job C ──────────────────────────────
    step(12, "erc8183.claim_refund — job C after expiry (twak)")
    if c_settled_by_quorum:
        mark("erc8183.claim_refund", "skipped", "job C settled by quorum instead")
        mark("erc8183.mark_expired", "skipped", "job C settled by quorum instead")
        print("   SKIP [12/13] job C already settled REJECTED via quorum")
    else:
        wait_until(expired_c + 5, "job C's expiredAt to pass")
        res = twak.claim_refund(job_c)  # permissionless escape hatch
        wait_for(
            f"job {job_c} EXPIRED",
            lambda: twak.get_job_status(job_c) == JobStatus.EXPIRED,
        )
        mark("erc8183.claim_refund", "exercised",
             f"job C={job_c} disputed-but-PENDING -> EXPIRED, escrow refunded")
        ok(12, "escrow refunded; job C -> EXPIRED", res.get("transactionHash"))

        step(13, "erc8183.mark_expired — reconcile the Router (twak)")
        # claimRefund bypasses the Router (kernel-only), leaving its in-flight
        # counter stale — mark_expired is the permissionless reconcile.
        before = twak.inflight_job_count()
        res = twak.mark_expired(job_c)
        after = twak.inflight_job_count()
        # Soft check: a shared testnet can move the counter concurrently.
        print(f"   router inflight count: {before} -> {after}")
        mark("erc8183.mark_expired", "exercised", f"inflight {before} -> {after}")
        ok(13, "router reconciled for the refunded job", res.get("transactionHash"))

    # ── intents that cannot be honestly exercised ──────────────────────────
    mark("erc8183.complete", "skipped",
         "evaluator-only; on router-registered jobs the evaluator IS the "
         "Router contract, so no EOA (twak or EVM) may call it — routed "
         "completion happens inside settle")
    mark("erc8004.set_metadata", "skipped", "post-mint metadata not in scope "
         "(v0.18.0 made register metadata atomic; see step 1)")
    mark("erc8004.set_agent_uri", "skipped", "identity mutation out of scope")

    print_coverage()
    print("\nALL STEPS PASSED ✓")
    return 0


ALL_INTENTS = [
    "erc8004.register", "erc8004.set_metadata", "erc8004.set_agent_uri",
    "erc8183.create_job", "erc8183.set_provider", "erc8183.set_budget",
    "erc8183.register_job", "erc8183.fund", "erc8183.submit",
    "erc8183.complete", "erc8183.reject", "erc8183.claim_refund",
    "erc8183.settle", "erc8183.mark_expired", "erc8183.dispute",
    "erc8183.vote_reject",
]


def print_coverage() -> None:
    print()
    print("=" * 76)
    print(" coverage summary (intent -> exercised / skipped + reason)")
    print("=" * 76)
    for intent in ALL_INTENTS:
        status, note = coverage.get(intent, ("NOT REACHED", ""))
        line = f" {intent:<24} {status:<22} {note}"
        print(line if len(line) <= 76 else line[:73] + "...")
    print("=" * 76)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (AssertionError, KeyboardInterrupt) as e:
        print(f"\nSMOKE ABORTED: {e}")
        print_coverage()  # show progress even on failure
        sys.exit(1)
