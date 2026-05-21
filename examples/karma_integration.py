#!/usr/bin/env python3
"""
Karma × BNB Chain Integration Example
======================================

Full end-to-end workflow: create an ERC-8183 job, execute it with Karma
verifiable execution, submit the deliverable with a signed Karma evidence
bundle, and settle on-chain via the Karma evaluator.

Prerequisites
-------------
.. code-block:: bash

    pip install "bnbagent[karma]"

    # Set env vars (see .env.example):
    export PRIVATE_KEY="0x..."
    export WALLET_PASSWORD="your-password"
    export KARMA_RUNTIME_URL="https://api.karma.xyz"
    export KARMA_API_KEY="karma_worker-001_secret"

    # Default: BSC Testnet
    # For mainnet, set: export NETWORK="bsc-mainnet"

Workflow
--------
1. Register agent identity (ERC-8004, one-time).
2. Create ERC-8183 job with escrow.
3. Provider executes work via Karma hook layer → signed receipts.
4. Provider builds evidence bundle → uploads to storage.
5. Karma evaluator verifies bundle → settle on-chain.

Run::

    python examples/karma_integration.py
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from datetime import datetime, timezone, timedelta
from dataclasses import dataclass
from typing import Any

from dotenv import load_dotenv

from bnbagent import ERC8183Client, EVMWalletProvider, JobStatus
from bnbagent.erc8183 import NegotiationHandler, NegotiationRequest, TermSpecification
from bnbagent.storage import LocalStorageProvider

# Karma integration (requires pip install "bnbagent[karma]")
try:
    from bnbagent.extras.karma import (
        KarmaBNBVerifier,
        KarmaEvaluator,
        KarmaEvidenceStore,
    )
    HAS_KARMA = True
except ImportError:
    HAS_KARMA = False
    print("[WARN] Karma extras not installed. Run: pip install 'bnbagent[karma]'")

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("karma_example")


# ---------------------------------------------------------------------------
# Simulation helpers
# ---------------------------------------------------------------------------


def simulate_karma_execution(task_id: str, tool_calls: list[dict[str, Any]]) -> dict[str, Any]:
    """Simulate Karma tool execution → signed receipts + evidence bundle.

    In production this would be done by KarmaClient.run_task() with a
    real KarmaHookLayer wrapping actual tool calls. Here we build
    equivalent data structures for the demo.
    """
    import hashlib
    from web3 import Web3

    receipts: list[dict[str, Any]] = []
    now = datetime.now(timezone.utc)

    for i, tc in enumerate(tool_calls):
        receipt = {
            "receipt_id": f"rcpt-{task_id}-{i:04d}",
            "task_id": task_id,
            "agent_id": "provider-agent-001",
            "step_index": i,
            "tool_name": tc["tool"],
            "input_hash": hashlib.sha256(
                json.dumps(tc.get("input", {}), sort_keys=True, default=str).encode()
            ).hexdigest(),
            "output_hash": hashlib.sha256(
                json.dumps(tc.get("output", {}), sort_keys=True, default=str).encode()
            ).hexdigest(),
            "started_at": (now + timedelta(seconds=i * 0.5)).isoformat(),
            "ended_at": (now + timedelta(seconds=i * 0.5 + 0.3)).isoformat(),
            "duration_ms": 300,
            "status": tc.get("status", "success"),
            "metadata": {
                "template": "api",
                "status_code": tc.get("status_code", 200),
                "request_hash": hashlib.sha256(
                    json.dumps(tc.get("input", {}), sort_keys=True, default=str).encode()
                ).hexdigest(),
                "response_hash": hashlib.sha256(
                    json.dumps(tc.get("output", {}), sort_keys=True, default=str).encode()
                ).hexdigest(),
            },
        }
        receipts.append(receipt)

    # Build evidence bundle
    bundle = {
        "bundle_id": f"bundle-{task_id}",
        "task_id": task_id,
        "agent_id": "provider-agent-001",
        "receipt_count": len(receipts),
        "receipts": receipts,
        "created_at": now.isoformat(),
        "bundle_hash": Web3.keccak(
            text=json.dumps(receipts, sort_keys=True, separators=(",", ":"), default=str)
        ).hex(),
        "status": "success" if all(r["status"] == "success" for r in receipts) else "failed",
    }

    return {
        "bundle": bundle,
        "receipts": receipts,
    }


# ---------------------------------------------------------------------------
# Main workflow
# ---------------------------------------------------------------------------


async def main():
    logger.info("=" * 60)
    logger.info("Karma × BNB Chain — Verifiable Evaluator Demo")
    logger.info("=" * 60)

    # ---------------------------------------------------------- setup wallets

    private_key = os.getenv("PRIVATE_KEY")
    wallet_password = os.getenv("WALLET_PASSWORD", "demo-password")

    if not private_key:
        logger.warning(
            "PRIVATE_KEY not set. Generating a demo wallet (no real funds)."
        )
        from eth_account import Account

        acct = Account.create()
        private_key = acct.key.hex()
        logger.info("Demo address: %s", acct.address)

    wallet = EVMWalletProvider(password=wallet_password, private_key=private_key)
    logger.info("Wallet: %s", wallet.address[:10] + "...")

    # ----------------------------------------------------- init ERC-8183

    network = os.getenv("NETWORK", "bsc-testnet")
    logger.info("Network: %s", network)

    erc8183 = ERC8183Client(wallet, network=network)
    logger.info("Payment token: %s", erc8183.payment_token[:10] + "...")
    logger.info("Commerce: %s", erc8183.commerce.address[:10] + "...")
    logger.info("Router:   %s", erc8183.router.address[:10] + "...")
    logger.info("Policy:   %s", erc8183.policy.address[:10] + "...")

    # ------------------------------------------------------- init storage

    storage = LocalStorageProvider.from_env()
    if not os.path.exists(storage.base_path):
        os.makedirs(storage.base_path)

    # --------------------------------------------------- init Karma evaluator

    karma_runtime_url = os.getenv("KARMA_RUNTIME_URL", "http://localhost:8000")
    karma_api_key = os.getenv("KARMA_API_KEY", "")

    if HAS_KARMA:
        karma_eval = KarmaEvaluator(
            runtime_url=karma_runtime_url,
            api_key=karma_api_key,
        )
        karma_verifier = KarmaBNBVerifier(erc8183, karma_eval, min_confidence=0.5)
        logger.info("Karma evaluator: %s", karma_runtime_url)
    else:
        karma_eval = None
        karma_verifier = None
        logger.warning("Karma evaluator NOT available (karma extras not installed)")

    # ===========================================================
    # STEP 1 — Simulate Agent Execution with Karma
    # ===========================================================

    task_id = f"demo-task-{int(time.time())}"
    logger.info("\n[Step 1] Simulating Karma execution for task: %s", task_id)

    tool_calls = [
        {
            "tool": "browser.navigate",
            "input": {"url": "https://example.com"},
            "output": {"status": "ok", "html_length": 1234},
            "status_code": 200,
            "status": "success",
        },
        {
            "tool": "browser.extract",
            "input": {"selector": "h1"},
            "output": {"text": "Example Domain"},
            "status_code": 200,
            "status": "success",
        },
        {
            "tool": "llm.analyze",
            "input": {"prompt": "Summarize the page content"},
            "output": {"summary": "This is an example domain page."},
            "status_code": 200,
            "status": "success",
        },
    ]

    karma_result = simulate_karma_execution(task_id, tool_calls)
    bundle = karma_result["bundle"]
    receipts = karma_result["receipts"]

    logger.info("  → Generated %d signed receipts", len(receipts))
    logger.info("  → Evidence bundle hash: %s", bundle["bundle_hash"][:16] + "...")

    # Verify each receipt's digest is self-consistent
    for r in receipts:
        ok = r["input_hash"] and r["output_hash"] and r["status"] == "success"
        logger.debug("    receipt %s: %s", r["receipt_id"], "✓" if ok else "✗")

    # ===========================================================
    # STEP 2 — Negotiate price (off-chain)
    # ===========================================================

    logger.info("\n[Step 2] Negotiating price...")

    handler = NegotiationHandler.from_erc8183_client(
        erc8183_client=erc8183,
        service_price="5000000000000000000",  # 5 USDC
        estimated_completion_seconds=120,
    )

    request = NegotiationRequest(
        task_description=bundle["bundle_hash"],
        terms=TermSpecification(
            deliverables="Web page analysis and summarization",
            quality_standards="Accuracy > 95%, latency < 5s per tool call",
        ),
    )

    negotiation = handler.negotiate(request.to_dict())
    logger.info("  → Provider accepted: %s", negotiation.accepted)
    logger.info("  → Price: %s wei", negotiation.response.get("terms", {}).get("price", "N/A"))
    logger.info("  → Provider sig: %s", negotiation.provider_sig[:16] + "..." if negotiation.provider_sig else "none")

    if not negotiation.accepted:
        logger.error("Negotiation rejected: %s", negotiation.response.get("reason", "unknown"))
        return

    # ===========================================================
    # STEP 3 — Upload deliverable to storage
    # ===========================================================

    logger.info("\n[Step 3] Uploading Karma evidence bundle to storage...")

    deliverable_data = {
        "task_id": task_id,
        "bundle_hash": bundle["bundle_hash"],
        "evidence_bundle": bundle,
        "receipts": receipts,
        "negotiation": negotiation.to_dict(),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

    deliverable_path = f"karma/{task_id}.json"
    await storage.upload(
        path=deliverable_path,
        data=json.dumps(deliverable_data, indent=2, default=str).encode("utf-8"),
    )

    # For file:// storage, construct a URL
    file_path = os.path.join(storage.base_path, deliverable_path)
    deliverable_url = f"file://{file_path}"

    logger.info("  → Deliverable uploaded to: %s", deliverable_url)
    logger.info("  → Size: %d bytes", os.path.getsize(file_path))

    # ===========================================================
    # STEP 4 — ERC-8183 On-chain Job
    # ===========================================================

    logger.info("\n[Step 4] Creating ERC-8183 job on-chain...")

    # Build description from negotiation
    from bnbagent.erc8183.negotiation import build_job_description

    description = build_job_description(negotiation.to_dict())

    # Expire in 1 hour
    expired_at = int(time.time()) + 3600

    logger.info("  → Creating job with description: %s...", description[:80])

    try:
        create_result = erc8183.create_job(
            provider=wallet.address,   # demo: provider = self
            expired_at=expired_at,
            description=description,
        )

        # Parse job ID from events
        job_id = _extract_job_id(create_result, erc8183.commerce.address)
        logger.info("  → Job created: jobId=%s", job_id)
        logger.info("  → Tx: %s", _format_tx(create_result))

    except Exception as exc:
        logger.error("  ✗ create_job failed: %s", exc)
        logger.info("  (This is expected if no testnet funds are available)")
        logger.info("  Continuing with demo simulation...")
        job_id = 42  # demo fallback

    # Register job on router
    try:
        reg_result = erc8183.register_job(job_id)
        logger.info("  → Job registered on router: tx=%s", _format_tx(reg_result))
    except Exception as exc:
        logger.warning("  → register_job skipped: %s", exc)

    # Set budget + fund
    budged_amount = 5000000000000000000  # 5 tokens

    try:
        set_result = erc8183.set_budget(job_id, budged_amount)
        logger.info("  → Budget set: %d wei", budged_amount)
    except Exception as exc:
        logger.warning("  → set_budget skipped: %s", exc)

    try:
        fund_result = erc8183.fund(job_id, budged_amount)
        logger.info("  → Job funded: tx=%s", _format_tx(fund_result))
    except Exception as exc:
        logger.warning("  → fund skipped (no testnet token): %s", exc)

    # ===========================================================
    # STEP 5 — Submit deliverable on-chain
    # ===========================================================

    logger.info("\n[Step 5] Submitting deliverable...")

    from bnbagent.erc8183.schema import DeliverableManifest

    manifest = DeliverableManifest(
        version=1,
        job_id=str(job_id),
        bundle_hash=bundle["bundle_hash"],
        receipt_count=len(receipts),
        status=bundle["status"],
        url=deliverable_url,
    )

    manifest_hash = manifest.manifest_hash()
    opt_params = {
        "deliverable_url": deliverable_url,
        "karma_bundle_hash": bundle["bundle_hash"],
        "karma_receipt_count": len(receipts),
    }

    try:
        submit_result = erc8183.submit(
            job_id=job_id,
            deliverable=manifest_hash,
            opt_params=opt_params,
        )
        logger.info("  → Deliverable submitted: tx=%s", _format_tx(submit_result))
        logger.info("  → Manifest hash: %s", manifest_hash.hex()[:16] + "...")
        logger.info("  → Deliverable URL on-chain: %s", deliverable_url)
    except Exception as exc:
        logger.warning("  → submit skipped: %s", exc)

    # ===========================================================
    # STEP 6 — Karma Verification + Settle
    # ===========================================================

    logger.info("\n[Step 6] Karma verification + on-chain settlement...")

    if karma_verifier is not None:
        try:
            result = await karma_verifier.verify_and_settle(job_id)

            logger.info("  → Verdict: %s", result["verdict"])
            logger.info("  → Settled: %s", result["settled"])
            if result.get("tx_hash"):
                logger.info("  → Settlement tx: %s", result["tx_hash"])
            if result.get("verification"):
                v = result["verification"]
                logger.info("  → Karma score: %.2f", v.get("score", 0))
                logger.info("  → Verification ID: %s", v.get("verification_id", "N/A"))

        except Exception as exc:
            logger.error("  ✗ verify_and_settle failed: %s", exc)

    else:
        logger.info("  → (skipped — Karma extras not installed)")

        # Show what the evidence bytes would look like
        from bnbagent.extras.karma.evaluator import build_karma_evidence_bytes

        evidence = build_karma_evidence_bytes(
            verification_id="demo-verification-001",
            verdict="APPROVE",
            score=0.98,
            receipt_count=len(receipts),
            bundle_hash=bundle["bundle_hash"],
        )
        logger.info("  → Evidence bytes (demo): %s", evidence.decode())
        logger.info("  → Evidence hex: %s", evidence.hex()[:64] + "...")

    # ===========================================================
    # STEP 7 — Verify final state
    # ===========================================================

    logger.info("\n[Step 7] Checking final job state...")

    try:
        job = erc8183.get_job(job_id)
        logger.info("  → Job %d status: %s", job_id, job.status.name)
    except Exception as exc:
        logger.warning("  → get_job failed: %s", exc)

    # ===========================================================
    # Summary
    # ===========================================================

    logger.info("\n" + "=" * 60)
    logger.info("Integration Demo Complete!")
    logger.info("=" * 60)
    logger.info("  Task ID:     %s", task_id)
    logger.info("  Receipts:    %d", len(receipts))
    logger.info("  Bundle hash: %s", bundle["bundle_hash"][:32] + "...")
    logger.info("  Network:     %s", network)
    logger.info("  Deliverable: %s", deliverable_url)

    logger.info("\nNext steps:")
    logger.info("  1. Set PRIVATE_KEY with testnet funds for live on-chain test")
    logger.info("  2. Run a Karma Runtime instance (or use hosted api.karma.xyz)")
    logger.info("  3. Run this demo again to see the full settle flow")
    logger.info("  4. Try: pip install 'karma-sdk' for the full Karma client")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _extract_job_id(tx: dict, commerce_address: str) -> int:
    """Extract jobId from a createJob transaction receipt events."""
    events = tx.get("events", tx.get("logs", []))
    for evt in events:
        if evt.get("event") == "JobCreated":
            return int(evt.get("args", {}).get("jobId", 0))
    # Fallback: look for raw logs
    return 0


def _format_tx(tx: dict) -> str:
    """Short tx hash for logging."""
    h = tx.get("transactionHash", tx.get("tx_hash", ""))
    if isinstance(h, bytes):
        h = h.hex()
    return h[:16] + "..." if h else "N/A"


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    asyncio.run(main())
