"""On-chain job timeline report generator (read-only).

Usage:
  uv run python gen_job_report.py <jobId>

Output: job-{id}-report.md in the current directory.
"""

from __future__ import annotations

import dataclasses
import datetime
import json
import os
import sys
import urllib.request
from pathlib import Path

from dotenv import load_dotenv, dotenv_values

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env")

from bnbagent.erc8183 import ERC8183Client, JobStatus, Verdict
from bnbagent.erc8183.types import REASON_APPROVED, REASON_REJECTED
from bnbagent.wallets import EVMWalletProvider
from bnbagent.config import resolve_network

SCAN_TX = "https://testnet.bscscan.com/tx/0x"

EMOJI: dict[str, str] = {
    "JobCreated":      "🆕",
    "BudgetSet":       "💰",
    "ProviderSet":     "👤",
    "JobRegistered":   "📋",
    "JobFunded":       "💵",
    "JobSubmitted":    "📤",
    "JobInitialised":  "🗂",
    "Disputed":        "⚖️",
    "VoteCast":        "🗳",
    "QuorumReached":   "✅",
    "JobSettled":      "🏁",
    "JobFinalised":    "🏁",
    "JobCompleted":    "✔️",
    "JobRejected":     "❌",
    "JobExpired":      "⏰",
    "Refunded":        "↩️",
    "PaymentReleased": "💸",
}

EVENT_MAP_KEYS = {
    "commerce": [
        "JobCreated", "BudgetSet", "ProviderSet", "JobFunded",
        "JobSubmitted", "JobCompleted", "JobRejected", "JobExpired",
        "Refunded", "PaymentReleased",
    ],
    "router": ["JobRegistered", "JobSettled", "JobFinalised"],
    "policy": ["Disputed", "JobInitialised", "VoteCast", "QuorumReached"],
}


def _make_client() -> ERC8183Client:
    voter_env = dotenv_values(ROOT.parent / "voter" / ".env")
    rpc_url   = voter_env.get("RPC_URL")
    network   = os.environ.get("NETWORK", "bsc-testnet")
    wallet    = EVMWalletProvider(
        password="demo",
        private_key=os.environ["PRIVATE_KEY"],
        persist=False,
    )
    if rpc_url:
        nc = dataclasses.replace(resolve_network(network), rpc_url=rpc_url)
        return ERC8183Client(wallet, network=nc)
    return ERC8183Client(wallet, network=network)


def _collect_events(client: ERC8183Client, job_id: int) -> list[dict]:
    w3     = client.commerce.w3
    latest = w3.eth.block_number

    submit_block = client._resolve_submit_block(job_id)
    if submit_block is not None:
        from_block = max(0, submit_block - 5_000)
        to_block   = min(latest, submit_block + 5_000)
    else:
        from_block = max(0, latest - 1_000)
        to_block   = latest

    contract_map = {
        "Commerce": client.commerce.contract,
        "Router":   client.router.contract,
        "Policy":   client.policy.contract,
    }

    events: list[dict] = []
    for (contract_key, contract_label), contract in zip(
        zip(EVENT_MAP_KEYS.keys(), contract_map.keys()), contract_map.values()
    ):
        for ev_name in EVENT_MAP_KEYS[contract_key]:
            try:
                logs = getattr(contract.events, ev_name)().get_logs(
                    from_block=from_block,
                    to_block=to_block,
                    argument_filters={"jobId": job_id},
                )
                for log in logs:
                    events.append({
                        "block":    log["blockNumber"],
                        "tx":       log["transactionHash"].hex(),
                        "contract": contract_label,
                        "name":     ev_name,
                        "args":     dict(log["args"]),
                    })
            except Exception as exc:
                print(f"  [warn] {contract_label}.{ev_name}: {exc}", file=sys.stderr)

    events.sort(key=lambda e: (e["block"], e["tx"], e["name"]))
    return events


_block_ts_cache: dict[int, int] = {}

def _block_ts(w3, blk: int) -> int:
    if blk not in _block_ts_cache:
        _block_ts_cache[blk] = w3.eth.get_block(blk)["timestamp"]
    return _block_ts_cache[blk]

def _utc(ts: int) -> str:
    return datetime.datetime.fromtimestamp(ts, tz=datetime.timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


_receipt_cache: dict[str, str] = {}

def _caller(w3, tx_hash: str) -> str:
    if tx_hash not in _receipt_cache:
        try:
            r = w3.eth.get_transaction_receipt("0x" + tx_hash)
            _receipt_cache[tx_hash] = r.get("from", "unknown")
        except Exception:
            _receipt_cache[tx_hash] = "unknown"
    return _receipt_cache[tx_hash]


def _resolve_deliverable(client: ERC8183Client, job_id: int) -> tuple[str | None, str | None]:
    url = client.get_deliverable_url(job_id)
    if not url:
        return None, None

    gateway = os.environ.get("STORAGE_GATEWAY_URL", "https://gateway.pinata.cloud/ipfs/")
    if url.startswith("ipfs://"):
        fetch_url = gateway.rstrip("/") + "/" + url[len("ipfs://"):]
    elif url.startswith(("http://", "https://")):
        fetch_url = url
    else:
        return url, None

    try:
        raw  = urllib.request.urlopen(fetch_url, timeout=15).read()
        text = raw.decode("utf-8", errors="replace")
        if len(text) > 2048:
            text = text[:2048] + "\n... (truncated — see URL above for full content)"
        return url, text
    except Exception as exc:
        return url, f"<fetch failed: {exc}>"


def _compute_fund_flow(events: list[dict], commerce_addr: str) -> list[dict]:
    flows = []
    for e in events:
        a = e["args"]
        if e["name"] == "JobFunded":
            flows.append({"dir": "Escrowed",  "from": a["client"],   "to": commerce_addr, "amount": a["amount"], "event": "JobFunded"})
        elif e["name"] == "Refunded":
            flows.append({"dir": "Refunded",  "from": commerce_addr, "to": a["client"],   "amount": a["amount"], "event": "Refunded"})
        elif e["name"] == "PaymentReleased":
            flows.append({"dir": "Released",  "from": commerce_addr, "to": a["provider"], "amount": a["amount"], "event": "PaymentReleased"})
    return flows


def _fmt_val(k: str, v, decimals: int, symbol: str) -> str:
    if isinstance(v, bytes):
        hex_val = f"0x{v.hex()}"
        if v == REASON_APPROVED:
            return f"`{hex_val}` (OPTIMISTIC_APPROVED)"
        if v == REASON_REJECTED:
            return f"`{hex_val}` (OPTIMISTIC_REJECTED)"
        if k == "optParams" and v:
            try:
                parsed = json.loads(v.decode("utf-8"))
                return f"`{json.dumps(parsed)}`"
            except Exception:
                pass
        return f"`{hex_val}`"
    if k in ("amount", "budget"):
        return f"`{v}` raw ({v / 10**decimals:.4f} {symbol})"
    if k == "verdict":
        try:
            return f"`{v}` ({Verdict(v).name})"
        except Exception:
            return f"`{v}`"
    if k == "status":
        try:
            return f"`{v}` ({JobStatus(v).name})"
        except Exception:
            return f"`{v}`"
    return f"`{v}`"


def _render(
    client: ERC8183Client,
    job_id: int,
    job,
    events: list[dict],
    deliverable_url: str | None,
    response_body: str | None,
    fund_flows: list[dict],
) -> str:
    w3       = client.commerce.w3
    decimals = client.token_decimals()
    symbol   = client.token_symbol()
    chain_id = client.network.chain_id
    now      = datetime.datetime.now(tz=datetime.timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    rv     = client.policy.reject_votes(job_id)
    quorum = client.policy.vote_quorum()
    disp   = client.policy.disputed(job_id)
    try:
        verdict, _ = client.policy.check(job_id)
        verdict_str = verdict.name
    except Exception:
        verdict_str = "N/A"

    exp_utc = _utc(job.expired_at)
    now_ts  = int(datetime.datetime.now(tz=datetime.timezone.utc).timestamp())
    if now_ts < job.expired_at:
        exp_rel = f"expires in {(job.expired_at - now_ts) // 60} min"
    else:
        exp_rel = f"expired {(now_ts - job.expired_at) // 60} min ago"

    L: list[str] = []
    def l(s: str = "") -> None:
        L.append(s)

    l(f"# Job {job_id} — On-chain Timeline Report")
    l()
    l(f"> Generated: {now}  ")
    l(f"> Network: BSC Testnet (chain_id={chain_id})  ")
    l(f"> Explorer: https://testnet.bscscan.com")
    l()
    l("---")
    l()

    l("## Addresses")
    l()
    l("| Role | Address |")
    l("|------|---------|")
    l(f"| Client | `{job.client}` |")
    l(f"| Provider | `{job.provider}` |")
    l(f"| Commerce contract | `{client.commerce.address}` |")
    l(f"| Router contract | `{client.router.address}` |")
    l(f"| Policy contract | `{client.policy.address}` |")
    l(f"| Payment token ({symbol}, 18 decimals) | `{client.payment_token}` |")
    l()
    l("---")
    l()

    l("## Job Status")
    l()
    l("| Field | Value |")
    l("|-------|-------|")
    l(f"| jobId | {job_id} |")
    l(f"| status | `{job.status.name}` |")
    l(f"| description | `{job.description}` |")
    l(f"| budget | `{job.budget}` raw = {job.budget / 10**decimals:.4f} {symbol} |")
    l(f"| expiredAt | `{job.expired_at}` = {exp_utc} ({exp_rel}) |")
    l(f"| rejectVotes | `{rv} / {quorum}` |")
    l(f"| verdict | `{verdict_str}` |")
    l(f"| disputed | `{disp}` |")
    if job.deliverable and job.deliverable != b'\x00' * 32:
        l(f"| deliverable hash (on-chain) | `0x{job.deliverable.hex()}` |")
    l()
    l("---")
    l()

    l("## Timeline")
    l()
    for ev in events:
        blk      = ev["block"]
        tx       = ev["tx"]
        utc      = _utc(_block_ts(w3, blk))
        emoji    = EMOJI.get(ev["name"], "📌")
        c_from   = _caller(w3, tx)

        l(f"### {emoji} `{ev['name']}` — {ev['contract']} — block {blk} — {utc}")
        l()
        l(f"- **Tx**: [`0x{tx}`]({SCAN_TX}{tx})")
        l(f"- **From**: `{c_from}`")
        l()

        args = {k: v for k, v in ev["args"].items() if k != "jobId"}
        if args:
            l("| Param | Value |")
            l("|-------|-------|")
            for k, v in args.items():
                l(f"| `{k}` | {_fmt_val(k, v, decimals, symbol)} |")
        l()

    l("---")
    l()

    l("## Deliverable")
    l()
    if deliverable_url:
        l(f"- **deliverable_url**: `{deliverable_url}`")
        if deliverable_url.startswith("ipfs://"):
            cid     = deliverable_url[len("ipfs://"):]
            gateway = os.environ.get("STORAGE_GATEWAY_URL", "https://gateway.pinata.cloud/ipfs/")
            l(f"- **CID**: `{cid}`")
            l(f"- **Gateway URL**: {gateway.rstrip('/')}/{cid}")
        if job.deliverable and job.deliverable != b'\x00' * 32:
            l(f"- **Manifest hash (on-chain)**: `0x{job.deliverable.hex()}`")
        l()
        if response_body:
            l("**Response** (truncated to 2 KB):")
            l()
            trimmed = response_body.strip()
            lang = "json" if (trimmed.startswith("{") or trimmed.startswith("[")) else "text"
            l(f"```{lang}")
            l(response_body)
            l("```")
        else:
            l("> Response body could not be fetched.")
    else:
        l("> deliverable_url not found (job may not be submitted yet, or event is outside the scan window).")
    l()
    l("---")
    l()

    l("## Fund Flow")
    l()
    if fund_flows:
        l("| Direction | From | To | Amount | Event |")
        l("|-----------|------|----|--------|-------|")
        for f in fund_flows:
            amt = f"{f['amount'] / 10**decimals:.4f} {symbol}"
            l(f"| {f['dir']} | `{f['from']}` | `{f['to']}` | {amt} | `{f['event']}` |")
        l()

        provider_received = sum(f["amount"] for f in fund_flows if f["event"] == "PaymentReleased")
        client_refunded   = sum(f["amount"] for f in fund_flows if f["event"] == "Refunded")
        client_spent      = sum(f["amount"] for f in fund_flows if f["event"] == "JobFunded") - client_refunded

        l(f"> **Provider received**: {provider_received / 10**decimals:.4f} {symbol}  ")
        l(f"> **Client net cost**: {client_spent / 10**decimals:.4f} {symbol}")
    else:
        l("> No fund flow events found in the scan window.")
    l()
    l("---")
    l()

    l("## Transaction Index")
    l()
    tx_index: dict[tuple[int, str], list[str]] = {}
    for ev in events:
        tx_index.setdefault((ev["block"], ev["tx"]), []).append(ev["name"])

    l("| Block | Time (UTC) | Tx Hash | Events |")
    l("|-------|------------|---------|--------|")
    for (blk, tx), ev_names in sorted(tx_index.items()):
        utc = _utc(_block_ts(w3, blk))
        l(f"| {blk} | {utc} | [`0x{tx}`]({SCAN_TX}{tx}) | {', '.join(ev_names)} |")
    l()

    return "\n".join(L)


def main(job_id: int) -> None:
    print(f"Generating job-{job_id}-report.md ...")
    client = _make_client()
    job    = client.get_job(job_id)
    print(f"  job {job_id}: status={job.status.name}  provider={job.provider}")

    print("  Collecting on-chain events ...")
    events = _collect_events(client, job_id)
    print(f"  Found {len(events)} events")

    print("  Resolving deliverable_url ...")
    deliverable_url, response_body = _resolve_deliverable(client, job_id)
    print(f"  deliverable_url = {deliverable_url}")

    fund_flows = _compute_fund_flow(events, client.commerce.address)

    md  = _render(client, job_id, job, events, deliverable_url, response_body, fund_flows)
    out = ROOT / f"job-{job_id}-report.md"
    out.write_text(md, encoding="utf-8")
    print(f"Done: {out}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: uv run python gen_job_report.py <jobId>", file=sys.stderr)
        sys.exit(1)
    main(int(sys.argv[1]))
