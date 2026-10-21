"""Voter watch loop — polls Disputed + VoteCast events, fetches IPFS manifest, prompts for vote.

For each disputed job:
  1. Reads deliverable_url from on-chain JobInitialised (optParams)
  2. Downloads DeliverableManifest from IPFS gateway
  3. Verifies manifest hash against on-chain deliverable
  4. Prints the response content for review
  5. Prompts: [r]eject / [s]kip

Under OptimisticPolicy silence past the voting window approves the job, so a
disputed job is always put to a vote before its window closes:
  - deliverable reviewable: prompted with its content;
  - deliverable unavailable (the provider published no usable deliverable_url,
    an unsupported URL, a malformed manifest, or a download failing
    MANIFEST_DOWNLOAD_ATTEMPTS ticks in a row): prompted with a warning;
  - deliverable not verified (the chain could not be read to look it up):
    retried, and prompted with a warning once the window is within the
    decision margin (VOTER_DECISION_MARGIN_SECONDS, default 30 minutes, at most
    half the window).
Text from the deliverable is printed escaped (see _printable).

For each VoteCast event:
  - If rejectVotes >= voteQuorum → settles automatically and prints JobSettled result

Usage:
    cd examples/voter
    python watch.py
"""

from __future__ import annotations

import math
import os
import time
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from bnbagent.erc8183 import ERC8183Client
from bnbagent.erc8183.schema import DeliverableManifest
from bnbagent.utils.public_http import fetch_public_json, public_gateway_url
from bnbagent.wallets import EVMWalletProvider

ROOT = Path(__file__).resolve().parent
POLL_INTERVAL = 12  # seconds
# Ticks a failing download is retried before the voter is asked to vote on an
# unavailable deliverable: the provider controls whether it can be fetched.
MANIFEST_DOWNLOAD_ATTEMPTS = 3
MANIFEST_TIMEOUT = 15  # seconds for a whole download
# A manifest carries up to MAX_RESPONSE_BYTES (5 MiB by default) of response.
MANIFEST_MAX_BYTES = 8 * 1024 * 1024
DEFAULT_DECISION_MARGIN = 30 * 60  # seconds
MAX_LOG_RANGE = 1_000  # blocks per Disputed / VoteCast query
_FETCHABLE_SCHEMES = ("ipfs://", "https://", "http://")


class ManifestUnavailable(Exception):
    """The deliverable could not be downloaded this time; retry on the next tick."""


def _printable(value: object) -> str:
    """``value`` as text a terminal shows as is.

    Control, format and surrogate characters are escaped: from the provider's
    deliverable they could rewrite lines already printed (such as the hash
    check) or make ``print`` raise and the job never be prompted.
    """
    text = value if isinstance(value, str) else str(value)
    return "".join(c if c.isprintable() or c in "\n\t" else ascii(c)[1:-1] for c in text)


def decision_margin(window: float) -> float:
    """Seconds before the voting window closes from which a disputed job is put to
    a vote whatever is still unresolved: ``VOTER_DECISION_MARGIN_SECONDS``
    (default 30 minutes), at most half the window."""
    raw = os.environ.get("VOTER_DECISION_MARGIN_SECONDS", "").strip()
    try:
        configured = float(raw) if raw else DEFAULT_DECISION_MARGIN
    except ValueError:
        configured = math.nan
    if not math.isfinite(configured) or configured < 0:
        raise ValueError(
            f"VOTER_DECISION_MARGIN_SECONDS must be a non-negative number of seconds, got {raw!r}"
        )
    return min(configured, window / 2)


def fetch_manifest(deliverable_url: str, gateway_url: str) -> DeliverableManifest | None:
    """Download and parse a DeliverableManifest from IPFS; ``None`` if it is malformed.

    The download goes through :func:`fetch_public_json`: public addresses only,
    no redirects, at most ``MANIFEST_MAX_BYTES`` within ``MANIFEST_TIMEOUT``.
    Raises :class:`ManifestUnavailable` when it cannot be downloaded, for any
    reason: the provider controls the URL and the server.
    """
    try:
        url = public_gateway_url(deliverable_url, gateway_url)
        data = fetch_public_json(url, max_bytes=MANIFEST_MAX_BYTES, timeout=MANIFEST_TIMEOUT)
    except Exception as e:
        raise ManifestUnavailable(f"could not fetch manifest: {e}") from e
    try:
        return DeliverableManifest.from_dict(data)
    except Exception as e:
        print(f"  [warn] malformed manifest: {_printable(e)}")
        return None


def handle_quorum_reached(
    erc8183: ERC8183Client, job_id: int, reject_votes: int, quorum: int
) -> None:
    """Called when VoteCast shows rejectVotes >= quorum — settle and print result."""
    print(f"\n{'=' * 60}")
    print(f"  QUORUM REACHED job_id={job_id}  ({reject_votes}/{quorum} reject votes)")
    print(f"{'=' * 60}")
    print(f"  settling job {job_id}...")
    try:
        erc8183.settle(job_id)
        print(f"  settle({job_id}) success ✓")
    except Exception as exc:
        print(f"  [error] settle failed: {exc}")


def handle_disputed_job(
    erc8183: ERC8183Client,
    job_id: int,
    voter: str,
    gateway_url: str,
    download_failures: int = 0,
) -> None:
    """Show job details and prompt voter to reject or skip.

    The deliverable is reviewable, unavailable or not verified (see the module
    docstring). Without prompting, a failed download raises
    :class:`ManifestUnavailable` until it has failed
    ``MANIFEST_DOWNLOAD_ATTEMPTS`` times (counted by the caller in
    ``download_failures``), and a failed deliverable lookup propagates; both
    stop being retried once the window is within :func:`decision_margin`.
    Other chain reads propagate and are retried.
    """
    if erc8183.policy.has_voted(job_id, voter):
        print(f"  job {job_id}: already voted — skipping.")
        return

    job = erc8183.get_job(job_id)
    window = erc8183.policy.dispute_window()
    # voteReject reverts from submittedAt + disputeWindow on
    window_left = job.submitted_at + window - time.time()
    if window_left <= 0:
        print(f"  job {job_id}: voting window closed — skipping.")
        return
    deciding = window_left <= decision_margin(window)

    manifest = None
    unavailable = None  # the provider made nothing reviewable
    unverified = None  # the chain could not be read to look the deliverable up
    deliverable_url = None
    try:
        # The SDK locates the submission block from job.submitted_at, however
        # long ago it was, and raises (never returns None) when it cannot read it.
        deliverable_url = erc8183.get_deliverable_url(job_id)
    except Exception as exc:
        if not deciding:
            raise
        unverified = f"could not look the deliverable up on-chain ({exc})"
    if unverified is None:
        if not isinstance(deliverable_url, str) or not deliverable_url:
            deliverable_url = None
            unavailable = "the provider published no usable deliverable_url on-chain"
        elif not deliverable_url.startswith(_FETCHABLE_SCHEMES):
            unavailable = f"unsupported deliverable URL {deliverable_url!r}"
        else:
            try:
                manifest = fetch_manifest(deliverable_url, gateway_url)
            except ManifestUnavailable as exc:
                if download_failures + 1 < MANIFEST_DOWNLOAD_ATTEMPTS and not deciding:
                    raise
                unavailable = str(exc)

    print(f"\n{'=' * 60}")
    print(f"  DISPUTED job_id={job_id}")
    print(f"{'=' * 60}")
    print(f"  client   : {job.client}")
    print(f"  provider : {job.provider}")
    print(f"  budget   : {job.budget}")
    print(f"  status   : {job.status.name}")
    print(f"  IPFS URL : {_printable(deliverable_url or '-')}")

    if manifest:
        hash_ok = manifest.verify(job.deliverable)
        print(f"  hash ok  : {'✓' if hash_ok else '✗ MISMATCH'}")
        print(f"\n--- Deliverable content (job_id={manifest.job_id}) ---")
        content = manifest.response.get("content", "")
        print(_printable(content))
        print("---")
    elif unverified:
        print(f"  [!] deliverable not verified: {_printable(unverified)}")
        print(
            f"      the voting window closes in {window_left / 60:.0f} min; check the"
            " deliverable another way (e.g. another RPC) before voting"
        )
    elif unavailable:
        print(f"  [!] deliverable unavailable: {_printable(unavailable)}")
        print("      there is nothing to review; the provider controls whether it can be fetched")
    else:
        print("  (the manifest is malformed: there is no valid deliverable to review)")

    # Prompt for vote
    try:
        choice = input("\n  [r]eject  [s]kip  > ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        print("\nstopped.")
        raise SystemExit(0) from None

    if choice == "r":
        print(f"  casting voteReject({job_id})...")
        erc8183.vote_reject(job_id)
        print(f"  voteReject({job_id}) submitted ✓")
        print("  (waiting for VoteCast event to check quorum...)")
    else:
        print(f"  skipped job {job_id}")


def _block_seconds_ago(erc8183: ERC8183Client, head: int, seconds: float) -> int:
    """A block about ``seconds`` before ``head``, from the recent block time."""
    sample = min(head, 1_000)
    if sample == 0:
        return 0
    elapsed = (
        erc8183.w3.eth.get_block(head)["timestamp"]
        - erc8183.w3.eth.get_block(head - sample)["timestamp"]
    )
    if elapsed <= 0:
        return 0
    return max(0, head - int(seconds * sample / elapsed))


def require_log_rpc(erc8183: ERC8183Client, rpc_url: str) -> None:
    """Exit unless the RPC serves ``eth_getLogs`` for recent blocks and for blocks as
    old as the voting window: a deliverable is looked up at its submission."""
    head = erc8183.w3.eth.block_number
    try:
        erc8183.policy.contract.events.Disputed().get_logs(
            from_block=max(0, head - 10), to_block=head
        )
    except Exception as exc:
        raise SystemExit(
            f"error: the RPC {rpc_url} does not serve eth_getLogs ({exc}); the voter reads "
            "Disputed, VoteCast and JobInitialised logs. Set RPC_URL to an endpoint that does."
        ) from exc
    window = erc8183.policy.dispute_window()
    old = _block_seconds_ago(erc8183, head, window)
    try:
        erc8183.policy.contract.events.Disputed().get_logs(from_block=old, to_block=old + 10)
    except Exception as exc:
        raise SystemExit(
            f"error: the RPC {rpc_url} does not serve eth_getLogs for blocks as old as the "
            f"voting window ({window / 3600:.1f} h, around block {old}): {exc}. A disputed "
            "job's deliverable is read from its submission, up to that long ago. Set RPC_URL "
            "to an archive endpoint."
        ) from exc


def _process_disputed_log(
    log, erc8183, voter, gateway_url, seen_disputed, download_failures=None
) -> bool:
    """Handle one disputed log, marking its job seen only after success.

    ``download_failures`` counts each job's failed manifest downloads across ticks.
    """
    if download_failures is None:
        download_failures = {}
    job_id = log["args"]["jobId"]
    ts = time.strftime("%H:%M:%S")
    print(f"[{ts}] Disputed event — jobId={job_id}")
    if job_id in seen_disputed:
        return True
    try:
        handle_disputed_job(erc8183, job_id, voter, gateway_url, download_failures.get(job_id, 0))
    except ManifestUnavailable as exc:
        download_failures[job_id] = download_failures.get(job_id, 0) + 1
        print(f"  [warn] disputed job {job_id}: {_printable(exc)}; retrying next tick")
        return False
    except Exception as exc:
        print(f"  [warn] failed to handle disputed job {job_id}: {_printable(exc)}")
        return False
    seen_disputed.add(job_id)
    download_failures.pop(job_id, None)
    return True


def _retry_pending_disputed(
    pending_disputed, erc8183, voter, gateway_url, seen_disputed, download_failures=None
):
    """Retry pending disputed logs and retain only those that still fail."""
    for job_id, log in list(pending_disputed.items()):
        if _process_disputed_log(
            log, erc8183, voter, gateway_url, seen_disputed, download_failures
        ):
            del pending_disputed[job_id]


class WatchState:
    """What the loop carries across ticks."""

    def __init__(self, last_block: int) -> None:
        self.last_block = last_block
        self.seen_disputed: set[int] = set()
        self.pending_disputed: dict[int, Any] = {}
        self.download_failures: dict[int, int] = {}
        self.settled: set[int] = set()


def poll_once(
    erc8183: ERC8183Client, voter: str, gateway: str, quorum: int, state: WatchState
) -> None:
    """One tick: retry pending disputes, then read up to ``MAX_LOG_RANGE`` new blocks.

    Raises when the chain cannot be read. ``state.last_block`` advances only once
    both the Disputed and the VoteCast logs of the range were read, so a failed
    read is retried from the same block instead of skipping its disputes.
    """
    _retry_pending_disputed(
        state.pending_disputed,
        erc8183,
        voter,
        gateway,
        state.seen_disputed,
        state.download_failures,
    )
    head = erc8183.w3.eth.block_number
    if head <= state.last_block:
        return
    from_block = state.last_block + 1
    to_block = min(head, state.last_block + MAX_LOG_RANGE)
    events = erc8183.policy.contract.events
    disputed_logs = events.Disputed().get_logs(from_block=from_block, to_block=to_block)
    vote_logs = events.VoteCast().get_logs(from_block=from_block, to_block=to_block)

    for log in disputed_logs:
        job_id = log["args"]["jobId"]
        if not _process_disputed_log(
            log, erc8183, voter, gateway, state.seen_disputed, state.download_failures
        ):
            state.pending_disputed[job_id] = log

    for log in vote_logs:
        job_id = log["args"]["jobId"]
        reject_votes = log["args"]["rejectVotes"]
        caster = log["args"]["voter"]
        ts = time.strftime("%H:%M:%S")
        print(
            f"[{ts}] VoteCast — jobId={job_id}  rejectVotes={reject_votes}/{quorum}  by={caster}"
        )
        if reject_votes >= quorum and job_id not in state.settled:
            state.settled.add(job_id)
            handle_quorum_reached(erc8183, job_id, reject_votes, quorum)

    state.last_block = to_block


def main() -> None:
    load_dotenv(ROOT / ".env")

    pk = os.environ.get("VOTER_PRIVATE_KEY")
    if not pk:
        raise SystemExit("VOTER_PRIVATE_KEY is required in .env")

    network = os.environ.get("NETWORK", "bsc-testnet")
    gateway = os.environ.get("STORAGE_GATEWAY_URL", "https://gateway.pinata.cloud/ipfs/")

    from bnbagent.config import resolve_network

    nc = resolve_network(network)

    wallet = EVMWalletProvider(password="example", private_key=pk, persist=False)
    erc8183 = ERC8183Client(wallet, network=nc)
    voter = erc8183.address
    try:
        margin = decision_margin(erc8183.policy.dispute_window())
    except ValueError as exc:
        raise SystemExit(f"error: {exc}") from exc
    require_log_rpc(erc8183, nc.rpc_url)

    quorum = erc8183.policy.vote_quorum()

    print("Voter watch loop")
    print(f"  network  : {nc.name}")
    print(f"  rpc      : {nc.rpc_url}")
    print(f"  policy   : {erc8183.policy.address}")
    print(f"  voter    : {voter}")
    print(f"  listed   : {erc8183.policy.is_voter(voter)}")
    print(f"  quorum   : {quorum}")
    print(f"  gateway  : {gateway}")
    print(f"  margin   : {margin:.0f} s before a voting window closes")
    print("\nWatching for Disputed / VoteCast events (Ctrl+C to stop)...\n")

    state = WatchState(last_block=erc8183.w3.eth.block_number)
    while True:
        try:
            poll_once(erc8183, voter, gateway, quorum, state)
        except Exception as exc:
            print(
                f"  [warn] poll failed ({_printable(exc)}); "
                f"retrying from block {state.last_block + 1}"
            )
        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nstopped.")
