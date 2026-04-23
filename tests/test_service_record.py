"""Tests for ServiceRecord data structures."""

import json

from bnbagent.apex.service_record import (
    NegotiationData,
    NegotiationTerms,
    OnChainReferences,
    RequestData,
    ResponseData,
    ServiceData,
    ServiceRecord,
    TimestampData,
)


class TestRequestData:
    def test_to_dict(self):
        r = RequestData(content="hello", content_type="text/plain", hash="0xabc")
        d = r.to_dict()
        assert d["content"] == "hello"
        assert d["content_type"] == "text/plain"
        assert d["hash"] == "0xabc"

    def test_from_dict_roundtrip(self):
        r = RequestData(content="test", hash="0x123")
        d = r.to_dict()
        r2 = RequestData.from_dict(d)
        assert r2.content == r.content
        assert r2.hash == r.hash


class TestResponseData:
    def test_to_dict_minimal(self):
        r = ResponseData(content="response text")
        d = r.to_dict()
        assert d["content"] == "response text"
        assert "hash" not in d  # Empty hash excluded
        assert "content_url" not in d
        assert "metrics" not in d

    def test_to_dict_with_optionals(self):
        r = ResponseData(
            content="output",
            hash="0xdef",
            content_url="ipfs://abc",
            metrics={"latency": 100},
        )
        d = r.to_dict()
        assert d["hash"] == "0xdef"
        assert d["content_url"] == "ipfs://abc"
        assert d["metrics"]["latency"] == 100

    def test_from_dict(self):
        d = {"content": "x", "content_url": "http://example.com"}
        r = ResponseData.from_dict(d)
        assert r.content == "x"
        assert r.content_url == "http://example.com"


class TestNegotiationTerms:
    def test_to_dict(self):
        t = NegotiationTerms(
            deliverables="summary",
            quality_standards="high",
            agreed_price="100",
            currency="0xTok",
        )
        d = t.to_dict()
        assert d["deliverables"] == "summary"
        assert d["agreed_price"] == "100"

    def test_from_dict(self):
        d = {"deliverables": "output", "agreed_price": "50"}
        t = NegotiationTerms.from_dict(d)
        assert t.deliverables == "output"
        assert t.agreed_price == "50"


class TestTimestampData:
    def test_to_dict(self):
        ts = TimestampData(negotiated_at=100, requested_at=200)
        d = ts.to_dict()
        assert d["negotiated_at"] == 100
        assert d["requested_at"] == 200
        assert d["responded_at"] == 0
        assert d["submitted_at"] == 0

    def test_from_dict_defaults(self):
        ts = TimestampData.from_dict({})
        assert ts.negotiated_at == 0
        assert ts.submitted_at == 0


class TestNegotiationData:
    def test_to_dict(self):
        nd = NegotiationData(
            request={"task": "test"},
            request_hash="0x111",
            response={"accepted": True},
            response_hash="0x222",
        )
        d = nd.to_dict()
        assert d["request"] == {"task": "test"}
        assert d["request_hash"] == "0x111"

    def test_from_dict(self):
        d = {"request": {"a": 1}, "request_hash": "0xabc"}
        nd = NegotiationData.from_dict(d)
        assert nd.request == {"a": 1}
        assert nd.request_hash == "0xabc"
        assert nd.response is None


class TestServiceData:
    def test_to_dict(self):
        sd = ServiceData(response_content="result", response_hash="0xhash")
        d = sd.to_dict()
        assert d["response_content"] == "result"
        assert d["response_hash"] == "0xhash"

    def test_from_dict(self):
        sd = ServiceData.from_dict({"response_content": "x"})
        assert sd.response_content == "x"
        assert sd.response_hash == ""


class TestOnChainReferences:
    def test_to_dict(self):
        oc = OnChainReferences(
            approve_tx_hash="0xa",
            create_job_tx_hash="0xb",
        )
        d = oc.to_dict()
        assert d["approve_tx_hash"] == "0xa"
        assert d["create_job_tx_hash"] == "0xb"
        assert d["assertion_id"] == ""

    def test_from_dict(self):
        d = {"approve_tx_hash": "0xtest", "assertion_id": "0xaid"}
        oc = OnChainReferences.from_dict(d)
        assert oc.approve_tx_hash == "0xtest"
        assert oc.assertion_id == "0xaid"


class TestServiceRecord:
    def test_to_dict_minimal(self):
        sr = ServiceRecord(job_id=1, chain_id=97)
        d = sr.to_dict()
        assert d["job_id"] == 1
        assert d["chain_id"] == 97
        assert d["version"] == "1.0"
        assert "negotiation" not in d
        assert "service" not in d

    def test_to_dict_full(self):
        sr = ServiceRecord(
            job_id=1,
            chain_id=97,
            contract_address="0xABC",
            negotiation=NegotiationData(request={"task": "test"}),
            service=ServiceData(response_content="done"),
            timestamps=TimestampData(negotiated_at=100),
            on_chain=OnChainReferences(approve_tx_hash="0xtx"),
        )
        d = sr.to_dict()
        assert "negotiation" in d
        assert "service" in d
        assert "timestamps" in d
        assert "on_chain" in d

    def test_from_dict_roundtrip(self):
        sr = ServiceRecord(
            job_id=5,
            chain_id=56,
            contract_address="0xDEF",
            negotiation=NegotiationData(
                request={"task": "roundtrip"},
                request_hash="0xhash",
            ),
            service=ServiceData(response_content="output"),
        )
        d = sr.to_dict()
        sr2 = ServiceRecord.from_dict(d)
        assert sr2.job_id == 5
        assert sr2.negotiation.request == {"task": "roundtrip"}
        assert sr2.service.response_content == "output"

    def test_canonical_json_deterministic(self):
        sr = ServiceRecord(
            job_id=1,
            chain_id=97,
            on_chain=OnChainReferences(
                approve_tx_hash="0xa",
                submit_result_tx_hash="0xsub",
                assertion_id="0xaid",
            ),
        )
        cj = sr.canonical_json()
        parsed = json.loads(cj)
        # submit_result_tx_hash and assertion_id should be excluded
        assert "submit_result_tx_hash" not in parsed.get("on_chain", {})
        assert "assertion_id" not in parsed.get("on_chain", {})

    def test_canonical_json_excludes_post_submit(self):
        sr1 = ServiceRecord(
            job_id=1,
            on_chain=OnChainReferences(approve_tx_hash="0xa"),
        )
        sr2 = ServiceRecord(
            job_id=1,
            on_chain=OnChainReferences(
                approve_tx_hash="0xa",
                submit_result_tx_hash="0xdifferent",
                assertion_id="0xaid",
            ),
        )
        assert sr1.canonical_json() == sr2.canonical_json()

    def test_compute_hashes(self):
        sr = ServiceRecord(
            job_id=1,
            negotiation=NegotiationData(
                request={"task": "test"},
                response={"accepted": True},
            ),
            service=ServiceData(response_content="my result"),
        )
        hashes = sr.compute_hashes()
        assert hashes["negotiation_request_hash"].startswith("0x")
        assert hashes["negotiation_response_hash"].startswith("0x")
        assert hashes["service_response_hash"].startswith("0x")
        assert hashes["service_record_hash"].startswith("0x")
        # Verify hashes are stored back
        assert sr.negotiation.request_hash == hashes["negotiation_request_hash"]
        assert sr.service.response_hash == hashes["service_response_hash"]
