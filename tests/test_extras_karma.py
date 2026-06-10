"""
Tests for the Karma evaluator integration.

Covers:
- KarmaEvaluator payload construction and verdict interpretation
- KarmaBNBVerifier evidence encoding
- KarmaEvidenceStore CRUD
- KarmaReceiptSigner
- Evidence encoding / decoding helpers
"""

from __future__ import annotations

import json
import pytest

from bnbagent.extras.karma.evaluator import (
    KarmaBNBVerifier,
    KarmaEvaluator,
    KarmaEvidenceStore,
    KarmaReceiptSigner,
    build_karma_evidence_bytes,
    parse_karma_evidence,
    verify_karma_receipt_digest,
)
from bnbagent.erc8183.types import Verdict


# ---------------------------------------------------------------------------
# KarmaEvidenceStore
# ---------------------------------------------------------------------------


class TestKarmaEvidenceStore:
    """In-memory receipt store CRUD tests."""

    def test_add_and_get(self):
        store = KarmaEvidenceStore()
        store.add("task-1", {"step": 1, "status": "ok"})
        store.add("task-1", {"step": 2, "status": "ok"})

        receipts = store.get_all("task-1")
        assert len(receipts) == 2
        # newest first
        assert receipts[0]["step"] == 2
        assert receipts[1]["step"] == 1

    def test_count(self):
        store = KarmaEvidenceStore()
        assert store.count("task-x") == 0
        store.add("task-x", {"a": 1})
        assert store.count("task-x") == 1

    def test_clear(self):
        store = KarmaEvidenceStore()
        store.add("task-1", {"step": 1})
        store.clear("task-1")
        assert store.count("task-1") == 0
        assert store.get_all("task-1") == []

    def test_independent_tasks(self):
        store = KarmaEvidenceStore()
        store.add("task-a", {"n": 1})
        store.add("task-b", {"n": 2})
        assert store.count("task-a") == 1
        assert store.count("task-b") == 1


# ---------------------------------------------------------------------------
# KarmaReceiptSigner
# ---------------------------------------------------------------------------


class TestKarmaReceiptSigner:
    def test_no_signer_returns_empty(self):
        signer = KarmaReceiptSigner(agent_address="0x1234")
        assert signer.sign_digest(b"\x00" * 32) == "0x"

    def test_signer_with_function(self):
        def fake_sign(digest: bytes) -> bytes:
            return b"\x01" * 65

        signer = KarmaReceiptSigner(agent_address="0xabcd", _sign_fn=fake_sign)
        sig = signer.sign_digest(b"\xab" * 32)
        assert sig.startswith("0x")
        assert len(sig) == 132  # 2 + 130 hex chars for 65 bytes

    def test_signer_exception_fallback(self):
        def bad_sign(_):
            raise RuntimeError("HSM offline")

        signer = KarmaReceiptSigner(agent_address="0xdead", _sign_fn=bad_sign)
        assert signer.sign_digest(b"\x00" * 32) == "0x"


# ---------------------------------------------------------------------------
# KarmaEvaluator
# ---------------------------------------------------------------------------


class TestKarmaEvaluator:
    """Off-chain evaluator tests (no live network dependency)."""

    def test_init_defaults(self):
        ev = KarmaEvaluator(runtime_url="https://karma.example.com")
        assert ev.runtime_url == "https://karma.example.com"
        assert ev.api_key == ""
        assert ev.strict is True
        assert ev.timeout == 120.0

    def test_init_with_api_key(self):
        ev = KarmaEvaluator(
            runtime_url="https://karma.example.com",
            api_key="karma_secret_123",
            strict=False,
            timeout=60.0,
        )
        assert ev.api_key == "karma_secret_123"
        assert ev.strict is False
        assert ev.timeout == 60.0

    def test_compute_bundle_hash(self):
        h = KarmaEvaluator._compute_bundle_hash(
            evidence_bundle={"task_id": "t1", "hash": "abc"},
            receipts=[{"step": 1}, {"step": 2}],
        )
        assert h.startswith("0x")
        assert len(h) == 66  # 0x + 64 hex chars

    def test_compute_bundle_hash_empty(self):
        assert KarmaEvaluator._compute_bundle_hash(None, None) == "0x"
        assert KarmaEvaluator._compute_bundle_hash({}, []) == "0x"

    def test_reject_method(self):
        result = KarmaEvaluator._reject("bad stuff", "0xdeadbeef")
        assert result["verdict"] == "REJECT"
        assert result["score"] == 0.0
        assert result["reason"] == "bad stuff"
        assert result["bundle_hash"] == "0xdeadbeef"

    def test_pending_method(self):
        result = KarmaEvaluator._pending("waiting...", "0xbeef")
        assert result["verdict"] == "PENDING"
        assert result["score"] == 0.0
        assert result["reason"] == "waiting..."

    def test_interpret_approve(self):
        ev = KarmaEvaluator(runtime_url="https://k.test")
        karma_result = {
            "passed": True,
            "score": 0.98,
            "verification_id": "vfy-123",
            "receipt_count": 5,
            "reason": "All receipts verified",
        }
        result = ev._interpret(karma_result, "0xhash", {})
        assert result["verdict"] == "APPROVE"
        assert result["score"] == 0.98
        assert result["verification_id"] == "vfy-123"
        assert result["receipt_count"] == 5

    def test_interpret_reject(self):
        ev = KarmaEvaluator(runtime_url="https://k.test")
        karma_result = {
            "verified": False,
            "confidence": 0.12,
            "id": "vfy-456",
            "message": "Input hash mismatch at step 3",
        }
        result = ev._interpret(karma_result, "0xcafe", {})
        assert result["verdict"] == "REJECT"
        assert result["score"] == 0.12

    def test_build_payload_minimal(self):
        ev = KarmaEvaluator(runtime_url="https://k.test")
        payload = ev._build_payload("task-abc", None, None, None)
        assert payload == {"task_id": "task-abc"}

    def test_build_payload_full(self):
        ev = KarmaEvaluator(runtime_url="https://k.test")
        payload = ev._build_payload(
            "task-xyz",
            evidence_bundle={"bundle_id": "b1"},
            receipts=[{"step": 0}],
            contract={"client": "alice", "budget": 100},
        )
        assert payload["task_id"] == "task-xyz"
        assert payload["bundle"] == {"bundle_id": "b1"}
        assert payload["receipts"] == [{"step": 0}]
        assert payload["contract"] == {"client": "alice", "budget": 100}


# ---------------------------------------------------------------------------
# Evidence encoding
# ---------------------------------------------------------------------------


class TestEvidenceEncoding:
    def test_build_karma_evidence_bytes(self):
        evidence = build_karma_evidence_bytes(
            verification_id="v-001",
            verdict="APPROVE",
            score=0.99,
            receipt_count=3,
            bundle_hash="0xabcdef",
        )
        assert isinstance(evidence, bytes)
        data = json.loads(evidence)
        assert data["karma"]["verification_id"] == "v-001"
        assert data["karma"]["verdict"] == "APPROVE"
        assert data["karma"]["score"] == 0.99
        assert data["karma"]["receipt_count"] == 3
        assert data["karma"]["bundle_hash"] == "0xabcdef"
        assert "verified_at" in data["karma"]

    def test_parse_karma_evidence_valid(self):
        evidence = build_karma_evidence_bytes("v-002", "REJECT", 0.1, 0, "0xdead")
        parsed = parse_karma_evidence(evidence)
        assert parsed is not None
        assert parsed["verdict"] == "REJECT"

    def test_parse_karma_evidence_no_karma_field(self):
        record = json.dumps({"other": "data"}).encode()
        assert parse_karma_evidence(record) is None

    def test_parse_karma_evidence_invalid_json(self):
        assert parse_karma_evidence(b"not json at all") is None
        assert parse_karma_evidence(b"") is None


class TestVerifyReceiptDigest:
    def test_match(self):
        receipt = {"step": 1, "output": "hello"}
        from web3 import Web3

        canonical = json.dumps(receipt, sort_keys=True, separators=(",", ":"))
        expected = Web3.keccak(text=canonical).hex()
        assert verify_karma_receipt_digest(receipt, expected) is True

    def test_mismatch(self):
        assert verify_karma_receipt_digest({"x": 1}, "0x" + "00" * 32) is False


# ---------------------------------------------------------------------------
# KarmaBNBVerifier evidence encoding (no live chain)
# ---------------------------------------------------------------------------


class TestKarmaBNBVerifierEvidence:
    def test_encode_evidence(self):
        eval_result = {
            "verdict": "APPROVE",
            "verification_id": "vfy-999",
            "score": 0.95,
            "receipt_count": 7,
            "bundle_hash": "0xhash123",
        }
        evidence = KarmaBNBVerifier._encode_evidence(eval_result)
        data = json.loads(evidence)
        assert data["karma"]["verdict"] == "APPROVE"
        assert data["karma"]["receipt_count"] == 7
        assert "verified_at" in data["karma"]
