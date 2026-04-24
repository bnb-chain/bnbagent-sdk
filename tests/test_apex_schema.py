"""Tests for bnbagent/apex/schema.py — JobDescription and DeliverableManifest."""

import json
import time

import pytest
from web3 import Web3

from bnbagent.apex.schema import SCHEMA_VERSION, DeliverableManifest, JobDescription

FAKE_COMMERCE = "0x" + "aa" * 20
FAKE_ROUTER = "0x" + "bb" * 20
FAKE_POLICY = "0x" + "cc" * 20


# ---------------------------------------------------------------------------
# DeliverableManifest
# ---------------------------------------------------------------------------


def _manifest_dict(content="hello world", metadata=None) -> dict:
    return {
        "version": SCHEMA_VERSION,
        "job_id": 42,
        "chain_id": 97,
        "contracts": {
            "commerce": FAKE_COMMERCE,
            "router": FAKE_ROUTER,
            "policy": FAKE_POLICY,
        },
        "response": {
            "content": content,
            "content_type": "text/plain",
        },
        "submitted_at": 1_700_000_000,
        "metadata": metadata or {},
    }


class TestDeliverableManifest:
    def test_from_dict_round_trip(self):
        d = _manifest_dict()
        m = DeliverableManifest.from_dict(d)
        assert m.to_dict() == d

    def test_fields_accessible(self):
        m = DeliverableManifest.from_dict(_manifest_dict(content="test content"))
        assert m.version == SCHEMA_VERSION
        assert m.job_id == 42
        assert m.chain_id == 97
        assert m.contracts["commerce"] == FAKE_COMMERCE
        assert m.response["content"] == "test content"
        assert m.response["content_type"] == "text/plain"
        assert m.submitted_at == 1_700_000_000

    def test_manifest_hash_is_keccak256_of_canonical_manifest_json_excluding_submitted_at(self):
        m = DeliverableManifest.from_dict(_manifest_dict())
        # hash excludes submitted_at so providers can compute it before the tx lands
        canonical = json.dumps(m._hash_dict(), sort_keys=True, separators=(",", ":"))
        expected = Web3.keccak(text=canonical)
        assert m.manifest_hash() == expected

    def test_manifest_hash_unaffected_by_submitted_at(self):
        """submitted_at is metadata-only; changing it must not change the hash."""
        m1 = DeliverableManifest.from_dict(_manifest_dict())
        m2 = DeliverableManifest.from_dict(_manifest_dict())
        m2.submitted_at = 9_999_999_999
        assert m1.manifest_hash() == m2.manifest_hash()

    def test_manifest_hash_returns_bytes32(self):
        m = DeliverableManifest.from_dict(_manifest_dict())
        h = m.manifest_hash()
        assert isinstance(h, bytes)
        assert len(h) == 32

    def test_manifest_hash_differs_for_different_content(self):
        m1 = DeliverableManifest.from_dict(_manifest_dict(content="result A"))
        m2 = DeliverableManifest.from_dict(_manifest_dict(content="result B"))
        assert m1.manifest_hash() != m2.manifest_hash()

    def test_manifest_hash_differs_for_different_job_id(self):
        d1 = _manifest_dict()
        d2 = _manifest_dict()
        d2["job_id"] = 999
        assert DeliverableManifest.from_dict(d1).manifest_hash() != DeliverableManifest.from_dict(d2).manifest_hash()

    def test_verify_returns_true_for_matching_hash(self):
        m = DeliverableManifest.from_dict(_manifest_dict())
        assert m.verify(m.manifest_hash()) is True

    def test_verify_returns_false_for_wrong_hash(self):
        m = DeliverableManifest.from_dict(_manifest_dict(content="real content"))
        other = DeliverableManifest.from_dict(_manifest_dict(content="other content"))
        assert m.verify(other.manifest_hash()) is False

    def test_metadata_defaults_to_empty_dict(self):
        d = _manifest_dict()
        d.pop("metadata")
        m = DeliverableManifest.from_dict(d)
        assert m.metadata == {}

    def test_to_dict_includes_metadata(self):
        meta = {"agent": "gpt-4", "duration_ms": 1234}
        m = DeliverableManifest.from_dict(_manifest_dict(metadata=meta))
        assert m.to_dict()["metadata"] == meta

    def test_from_dict_raises_on_unknown_version(self):
        d = _manifest_dict()
        d["version"] = 999
        with pytest.raises(ValueError, match="version"):
            DeliverableManifest.from_dict(d)

    def test_from_dict_raises_on_missing_job_id(self):
        d = _manifest_dict()
        d.pop("job_id")
        with pytest.raises(ValueError, match="job_id"):
            DeliverableManifest.from_dict(d)

    def test_from_dict_submitted_at_defaults_to_zero_when_absent(self):
        d = _manifest_dict()
        d.pop("submitted_at")
        m = DeliverableManifest.from_dict(d)
        assert m.submitted_at == 0

    def test_from_dict_raises_on_missing_response_content(self):
        d = _manifest_dict()
        d["response"] = {"content_type": "text/plain"}
        with pytest.raises((KeyError, ValueError)):
            DeliverableManifest.from_dict(d)

    def test_to_dict_is_json_serializable(self):
        m = DeliverableManifest.from_dict(_manifest_dict())
        serialized = json.dumps(m.to_dict())
        assert json.loads(serialized) == m.to_dict()

    def test_manifest_hash_stable_across_equal_instances(self):
        """Same manifest data → same hash, every time."""
        m1 = DeliverableManifest.from_dict(_manifest_dict())
        m2 = DeliverableManifest.from_dict(_manifest_dict())
        assert m1.manifest_hash() == m2.manifest_hash()


# ---------------------------------------------------------------------------
# JobDescription
# ---------------------------------------------------------------------------


def _description_dict(task="write a report") -> dict:
    return {
        "version": SCHEMA_VERSION,
        "negotiated_at": 1_700_000_000,
        "task": task,
        "terms": {
            "deliverables": "PDF report",
            "quality_standards": "accurate, concise",
        },
        "price": "20000000000000000000",
        "currency": FAKE_COMMERCE,
        "negotiation_hash": "0x" + "ab" * 32,
        "provider_sig": "0x" + "cd" * 65,
    }


def _description_json(task="write a report") -> str:
    return json.dumps(_description_dict(task=task), sort_keys=True, separators=(",", ":"))


class TestJobDescription:
    def test_from_dict_round_trip(self):
        d = _description_dict()
        jd = JobDescription.from_dict(d)
        assert jd.to_dict() == d

    def test_fields_accessible(self):
        jd = JobDescription.from_dict(_description_dict(task="summarise article"))
        assert jd.version == SCHEMA_VERSION
        assert jd.task == "summarise article"
        assert jd.price == "20000000000000000000"
        assert jd.currency == FAKE_COMMERCE
        assert jd.terms["deliverables"] == "PDF report"
        assert jd.negotiation_hash == "0x" + "ab" * 32
        assert jd.provider_sig == "0x" + "cd" * 65

    def test_from_str_parses_valid_json(self):
        s = _description_json()
        jd = JobDescription.from_str(s)
        assert jd is not None
        assert jd.task == "write a report"

    def test_from_str_returns_none_for_plain_text(self):
        assert JobDescription.from_str("just a plain text description") is None

    def test_from_str_returns_none_for_json_without_version(self):
        s = json.dumps({"task": "no version field"})
        assert JobDescription.from_str(s) is None

    def test_from_str_returns_none_for_empty_string(self):
        assert JobDescription.from_str("") is None

    def test_from_dict_raises_on_unknown_version(self):
        d = _description_dict()
        d["version"] = 999
        with pytest.raises(ValueError, match="version"):
            JobDescription.from_dict(d)

    def test_optional_fields_default_to_none(self):
        d = _description_dict()
        d.pop("negotiation_hash")
        d.pop("provider_sig")
        d.pop("quote_expires_at", None)
        jd = JobDescription.from_dict(d)
        assert jd.negotiation_hash is None
        assert jd.provider_sig is None
        assert jd.quote_expires_at is None

    def test_to_dict_omits_none_optional_fields(self):
        d = _description_dict()
        d.pop("negotiation_hash")
        d.pop("provider_sig")
        jd = JobDescription.from_dict(d)
        result = jd.to_dict()
        assert "negotiation_hash" not in result
        assert "provider_sig" not in result

    def test_to_dict_is_json_serializable(self):
        jd = JobDescription.from_dict(_description_dict())
        serialized = json.dumps(jd.to_dict())
        assert json.loads(serialized) == jd.to_dict()

    def test_from_str_handles_whitespace_prefix(self):
        s = "  \n" + _description_json()
        jd = JobDescription.from_str(s)
        assert jd is not None

    def test_quote_expires_at_parsed_when_present(self):
        d = _description_dict()
        d["quote_expires_at"] = 1_700_003_600
        jd = JobDescription.from_dict(d)
        assert jd.quote_expires_at == 1_700_003_600
