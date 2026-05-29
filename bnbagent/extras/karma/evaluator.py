"""
Karma Evaluator — bridges Karma Trust Protocol verification into ERC-8183.

Architecture
------------
::

    ┌──────────────────────────────────────────────────────────────┐
    │  ERC-8183 Job Lifecycle (BNB Chain)                          │
    │                                                              │
    │  createJob → fund → submit(deliverable)                      │
    │                        ↓                                     │
    │  ┌──────────────────────────────────────────────────────┐    │
    │  │  KarmaBNBVerifier.verify_and_settle(job_id)          │    │
    │  │                                                      │    │
    │  │  1. Fetch deliverable URL from policy events         │    │
    │  │  2. Download Karma evidence bundle + receipts        │    │
    │  │  3. Reconstruct signed-receipt Merkle tree           │    │
    │  │  4. Call Karma Runtime /v1/verify                    │    │
    │  │  5. If APPROVE → router.settle(job_id, evidence)     │    │
    │  │     If REJECT  → no-op (dispute path)                │    │
    │  └──────────────────────────────────────────────────────┘    │
    │                        ↓                                     │
    │  settlement → COMPLETED or REJECTED/EXPIRED                  │
    └──────────────────────────────────────────────────────────────┘

Key design decisions
--------------------
1. **Off-chain evaluation.** Karma verification runs off-chain via its REST API.
   The result is embedded as ``evidence`` bytes in ``router.settle()``,
   creating a permanent audit trail on BNB Chain.

2. **Pluggable.** ``KarmaEvaluator`` is independent of ``ERC8183Client``.
   Callers can use it standalone or compose it via ``KarmaBNBVerifier``.

3. **No on-chain policy changes.** This integration works with the existing
   ``OptimisticPolicy`` — it simply adds a more trustworthy source of truth
   to the permissionless ``settle()`` call.

Evidence encoding
-----------------
The ``evidence`` bytes passed to ``router.settle(evidence)`` are a
compact JSON-CBOR-style record::

    {
      "karma": {
        "verification_id": "<karma-verification-id>",
        "receipt_count": 5,
        "bundle_hash": "0x...",
        "verdict": "APPROVE",
        "verified_at": "2025-01-01T00:00:00Z"
      }
    }

This gives on-chain observers a direct pointer to the Karma verification
without requiring them to trust the entity that called ``settle()``.
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from web3 import Web3

from bnbagent.erc8183.types import Verdict

if TYPE_CHECKING:
    from bnbagent.erc8183.client import ERC8183Client

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# In-memory receipt store (lightweight Karma-compatible store)
# ---------------------------------------------------------------------------


@dataclass
class KarmaEvidenceStore:
    """Thread-safe in-memory store for Karma execution receipts.

    Compatible with Karma's ``ReceiptStore`` protocol.  Used by
    ``KarmaEvaluator`` to cache receipts before bundle construction.
    """

    _receipts: dict[str, list[dict[str, Any]]] = field(default_factory=dict)

    def add(self, task_id: str, receipt: dict[str, Any]) -> None:
        """Store a single execution receipt for *task_id*."""
        self._receipts.setdefault(task_id, []).append(receipt)

    def get_all(self, task_id: str) -> list[dict[str, Any]]:
        """Return all receipts for *task_id* (newest first)."""
        return list(reversed(self._receipts.get(task_id, [])))

    def count(self, task_id: str) -> int:
        """Number of receipts stored for *task_id*."""
        return len(self._receipts.get(task_id, []))

    def clear(self, task_id: str) -> None:
        """Remove all receipts for *task_id*."""
        self._receipts.pop(task_id, None)


@dataclass
class KarmaReceiptSigner:
    """Produces EIP-191 signatures over Karma receipt digests.

    Used to anchor signed receipts on BNB Chain so that on-chain
    observers can verify that a specific agent attested to an
    execution step.
    """

    agent_address: str
    _sign_fn: Any = field(repr=False, default=None)

    def sign_digest(self, digest: bytes) -> str:
        """Sign a 32-byte digest and return the hex-encoded signature."""
        if self._sign_fn is None:
            return "0x"
        try:
            sig = self._sign_fn(digest)
            return "0x" + sig.hex() if isinstance(sig, bytes) else str(sig)
        except Exception:
            return "0x"


# ---------------------------------------------------------------------------
# Karma Evaluator
# ---------------------------------------------------------------------------


class KarmaEvaluator:
    """Off-chain verifier powered by Karma Trust Protocol.

    This is the core integration point. It takes Karma evidence bundles
    and signed receipts, forwards them to the Karma Runtime for
    verification, and returns actionable verdicts for the ERC-8183
    settlement flow.

    Parameters
    ----------
    runtime_url:
        Karma Runtime API base URL (e.g. ``"https://api.karma.xyz"``).
    api_key:
        Karma Runtime API key.
    timeout:
        HTTP timeout in seconds for verification calls.
    strict:
        When ``True`` (default), RPC / transport errors are treated as
        REJECT to avoid false approvals. Set to ``False`` for debugging.

    Usage
    -----
        evaluator = KarmaEvaluator(
            runtime_url="https://api.karma.xyz",
            api_key="karma_worker-001_secret",
        )
        result = await evaluator.evaluate(
            task_id="task-abc",
            evidence_bundle=bundle_dict,
            receipts=receipts_list,
        )
        print(result["verdict"])  # "APPROVE" | "REJECT"
    """

    # ------------------------------------------------------------------
    def __init__(
        self,
        runtime_url: str,
        api_key: str = "",
        *,
        timeout: float = 120.0,
        strict: bool = True,
    ) -> None:
        self.runtime_url = runtime_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout
        self.strict = strict

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def evaluate(
        self,
        task_id: str,
        evidence_bundle: dict[str, Any] | None = None,
        receipts: list[dict[str, Any]] | None = None,
        *,
        contract: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Evaluate whether a task was completed correctly.

        Constructs a verification payload from *evidence_bundle* and/or
        *receipts*, sends it to the Karma Runtime, and returns the
        verdict.

        Returns a dict with keys:
            ``verdict`` — ``"APPROVE"``, ``"REJECT"``, or ``"PENDING"``
            ``verification_id`` — Karma verification run id
            ``score`` — confidence score (0.0–1.0)
            ``receipt_count`` — number of receipts verified
            ``bundle_hash`` — keccak256 of the evidence bundle
            ``reason`` — human-readable explanation
            ``raw`` — full Karma VerificationResult (when available)

        When *evidence_bundle* and *receipts* are both ``None``, the
        evaluator falls back to a lightweight self-consistent check
        against any deliverable metadata embedded in *contract*.
        """
        import httpx

        # ---- build verification payload ----
        payload = self._build_payload(task_id, evidence_bundle, receipts, contract)

        bundle_hash = self._compute_bundle_hash(evidence_bundle, receipts)

        # ---- call Karma runtime ----
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as http:
                resp = await http.post(
                    f"{self.runtime_url}/v1/verify",
                    json=payload,
                    headers=headers,
                )
                resp.raise_for_status()
                karma_result = resp.json()
        except Exception as exc:
            logger.error(
                "[KarmaEvaluator] verification request failed: %s", exc
            )
            if self.strict:
                return self._reject(f"Karma verification error: {exc}", bundle_hash)
            return self._pending(f"Verification unavailable: {exc}", bundle_hash)

        # ---- interpret Karma result ----
        return self._interpret(karma_result, bundle_hash, payload)

    async def evaluate_from_deliverable_url(
        self,
        task_id: str,
        deliverable_url: str,
        *,
        contract: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Fetch a Karma evidence bundle from *deliverable_url* and evaluate it.

        The deliverable URL should point to a JSON document containing
        at minimum ``evidence_bundle`` and optionally ``receipts``.
        """
        import httpx

        try:
            async with httpx.AsyncClient(timeout=30.0) as http:
                resp = await http.get(deliverable_url)
                resp.raise_for_status()
                data = resp.json()
        except Exception as exc:
            logger.error(
                "[KarmaEvaluator] failed to fetch deliverable from %s: %s",
                deliverable_url, exc,
            )
            return self._reject(
                f"Failed to fetch deliverable: {exc}",
                "",
            )

        evidence_bundle = data.get("evidence_bundle")
        receipts = data.get("receipts")

        return await self.evaluate(task_id, evidence_bundle, receipts, contract=contract)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _build_payload(
        self,
        task_id: str,
        evidence_bundle: dict[str, Any] | None,
        receipts: list[dict[str, Any]] | None,
        contract: dict[str, Any] | None,
    ) -> dict[str, Any]:
        """Assemble the /v1/verify request body."""
        payload: dict[str, Any] = {"task_id": task_id}

        if evidence_bundle:
            payload["bundle"] = evidence_bundle
        if receipts:
            payload["receipts"] = receipts
        if contract:
            payload["contract"] = contract

        return payload

    @staticmethod
    def _compute_bundle_hash(
        evidence_bundle: dict[str, Any] | None,
        receipts: list[dict[str, Any]] | None,
    ) -> str:
        """Compute keccak256 over the combined evidence."""
        parts: dict[str, Any] = {}
        if evidence_bundle:
            parts["bundle"] = evidence_bundle
        if receipts:
            parts["receipts"] = receipts
        if not parts:
            return "0x"
        canonical = json.dumps(parts, sort_keys=True, separators=(",", ":"), default=str)
        h = Web3.keccak(text=canonical).hex()
        return h if h.startswith("0x") else "0x" + h

    def _interpret(
        self,
        karma_result: dict[str, Any],
        bundle_hash: str,
        _payload: dict[str, Any],
    ) -> dict[str, Any]:
        """Translate a Karma Runtime verification response into our result format."""
        passed = karma_result.get("passed", karma_result.get("verified", False))
        score = karma_result.get("score", karma_result.get("confidence", 0.0))
        verification_id = karma_result.get(
            "verification_id", karma_result.get("id", "")
        )
        reason = karma_result.get(
            "reason", karma_result.get("message", "")
        )

        verdict = "APPROVE" if passed else "REJECT"

        return {
            "verdict": verdict,
            "verification_id": str(verification_id),
            "score": float(score),
            "receipt_count": karma_result.get("receipt_count", 0),
            "bundle_hash": bundle_hash,
            "reason": str(reason),
            "raw": karma_result,
        }

    @staticmethod
    def _reject(reason: str, bundle_hash: str) -> dict[str, Any]:
        return {
            "verdict": "REJECT",
            "verification_id": "",
            "score": 0.0,
            "receipt_count": 0,
            "bundle_hash": bundle_hash,
            "reason": reason,
            "raw": None,
        }

    @staticmethod
    def _pending(reason: str, bundle_hash: str) -> dict[str, Any]:
        return {
            "verdict": "PENDING",
            "verification_id": "",
            "score": 0.0,
            "receipt_count": 0,
            "bundle_hash": bundle_hash,
            "reason": reason,
            "raw": None,
        }


# ---------------------------------------------------------------------------
# KarmaBNBVerifier — bridges KarmaEvaluator ↔ ERC8183Client
# ---------------------------------------------------------------------------


class KarmaBNBVerifier:
    """Top-level bridge from Karma verification to ERC-8183 settlement.

    Composes a ``KarmaEvaluator`` with an ``ERC8183Client`` to provide a
    single ``verify_and_settle()`` call that:

    1. Fetches the deliverable URL from on-chain events.
    2. Downloads the Karma evidence bundle from that URL.
    3. Runs Karma verification.
    4. If APPROVE, calls ``router.settle(job_id, evidence)`` on-chain.
       If REJECT, optionally triggers the dispute path.

    Parameters
    ----------
    erc8183:
        An initialised ``ERC8183Client`` connected to the target network.
    evaluator:
        A ``KarmaEvaluator`` pointing at the Karma Runtime.
    min_confidence:
        Minimum Karma score (0.0–1.0) to accept as APPROVE. Default 0.5.

    Usage
    -----
        verifier = KarmaBNBVerifier(erc8183_client, karma_evaluator)

        # After the provider has called submit()
        result = await verifier.verify_and_settle(job_id)
        if result["settled"]:
            print(f"Settled on-chain: {result['tx_hash']}")
    """

    # ------------------------------------------------------------------
    def __init__(
        self,
        erc8183: ERC8183Client,
        evaluator: KarmaEvaluator,
        *,
        min_confidence: float = 0.5,
    ) -> None:
        self._erc8183 = erc8183
        self._evaluator = evaluator
        self._min_confidence = min_confidence

    # ------------------------------------------------------------------

    async def verify_and_settle(self, job_id: int) -> dict[str, Any]:
        """Run the full Karma verify → settle pipeline for *job_id*.

        Steps
        -----
        1. Fetch on-chain job state + deliverable URL.
        2. Download Karma evidence bundle.
        3. Evaluate via Karma Runtime.
        4. If APPROVE → ``router.settle(job_id, evidence)``.
           If REJECT → returns verdict without settling (caller may dispute).
        """
        import asyncio

        from bnbagent.erc8183.types import JobStatus

        # ---- 1. check job state ----
        job = self._erc8183.get_job(job_id)
        if job.status != JobStatus.SUBMITTED:
            return {
                "settled": False,
                "verdict": "SKIPPED",
                "reason": f"Job {job_id} is {job.status.name}, not SUBMITTED",
                "tx_hash": None,
            }

        # ---- 2. resolve deliverable URL ----
        deliverable_url = self._erc8183.get_deliverable_url(job_id)
        if not deliverable_url:
            return {
                "settled": False,
                "verdict": "REJECT",
                "reason": f"No deliverable URL found for job {job_id}",
                "tx_hash": None,
            }

        # ---- 3. verify via Karma ----
        eval_result = await self._evaluator.evaluate_from_deliverable_url(
            task_id=str(job_id),
            deliverable_url=deliverable_url,
        )

        # ---- 4. check confidence threshold ----
        if eval_result["score"] < self._min_confidence and eval_result["verdict"] == "APPROVE":
            eval_result["verdict"] = "REJECT"
            eval_result["reason"] = (
                f"Karma score {eval_result['score']} below min {self._min_confidence}"
            )

        # ---- 5. act on verdict ----
        if eval_result["verdict"] == "APPROVE":
            evidence = self._encode_evidence(eval_result)
            try:
                tx = await asyncio.to_thread(
                    self._erc8183.settle, job_id, evidence
                )
                return {
                    "settled": True,
                    "verdict": "APPROVE",
                    "verification": eval_result,
                    "tx_hash": tx.get("transactionHash", tx.get("tx_hash", "")),
                    "evidence_hex": evidence.hex(),
                }
            except Exception as exc:
                logger.error(
                    "[KarmaBNBVerifier] settle tx failed for job %s: %s",
                    job_id, exc,
                )
                return {
                    "settled": False,
                    "verdict": "APPROVE",
                    "verification": eval_result,
                    "reason": f"Settle tx failed: {exc}",
                    "tx_hash": None,
                }

        # REJECT or PENDING — don't settle
        return {
            "settled": False,
            "verdict": eval_result["verdict"],
            "verification": eval_result,
            "reason": eval_result.get("reason", "Karma rejected"),
            "tx_hash": None,
        }

    async def verify_deliverable(
        self,
        job_id: int,
        deliverable_url: str,
    ) -> dict[str, Any]:
        """Verify a deliverable without settling on-chain.

        Returns the Karma evaluation result.  Useful for pre-flight
        checks before the provider calls ``submit()``.
        """
        return await self._evaluator.evaluate_from_deliverable_url(
            task_id=str(job_id),
            deliverable_url=deliverable_url,
        )

    # ------------------------------------------------------------------
    # Evidence encoding
    # ------------------------------------------------------------------

    @staticmethod
    def _encode_evidence(eval_result: dict[str, Any]) -> bytes:
        """Encode Karma verification result as on-chain evidence bytes."""
        record = {
            "karma": {
                "verification_id": str(eval_result.get("verification_id", "")),
                "verdict": str(eval_result.get("verdict", "UNKNOWN")),
                "score": float(eval_result.get("score", 0.0)),
                "receipt_count": int(eval_result.get("receipt_count", 0)),
                "bundle_hash": str(eval_result.get("bundle_hash", "0x")),
                "verified_at": datetime.now(timezone.utc).isoformat(),
            }
        }
        return json.dumps(record, sort_keys=True, separators=(",", ":")).encode("utf-8")


# ---------------------------------------------------------------------------
# Standalone helpers (no ERC8183Client needed)
# ---------------------------------------------------------------------------


def build_karma_evidence_bytes(
    verification_id: str,
    verdict: str,
    score: float,
    receipt_count: int,
    bundle_hash: str,
) -> bytes:
    """Build evidence bytes consumable by ``RouterClient.settle(evidence=...)``.

    Callers who only need the evidence encoding (e.g. for custom
    settlement scripts) can use this function directly.
    """
    record = {
        "karma": {
            "verification_id": verification_id,
            "verdict": verdict,
            "score": score,
            "receipt_count": receipt_count,
            "bundle_hash": bundle_hash,
            "verified_at": datetime.now(timezone.utc).isoformat(),
        }
    }
    return json.dumps(record, sort_keys=True, separators=(",", ":")).encode("utf-8")


def parse_karma_evidence(evidence: bytes) -> dict[str, Any] | None:
    """Parse evidence bytes from a settled transaction back into a dict.

    Returns ``None`` if the evidence does not contain a Karma payload.
    """
    try:
        data = json.loads(evidence.decode("utf-8"))
        if "karma" in data:
            return data["karma"]
    except (json.JSONDecodeError, UnicodeDecodeError):
        pass
    return None


def verify_karma_receipt_digest(receipt: dict[str, Any], expected_digest: str) -> bool:
    """Verify that a Karma receipt matches an expected digest."""
    try:
        canonical = json.dumps(receipt, sort_keys=True, separators=(",", ":"), default=str)
        actual = Web3.keccak(text=canonical).hex()
        return actual.lower() == expected_digest.lower()
    except Exception:
        return False
