"""Tests for negotiation data structures and handler."""

from unittest.mock import MagicMock

import pytest

from bnbagent.apex.negotiation import (
    TermSpecification,
    NegotiationRequest,
    NegotiationResponse,
    NegotiationResult,
    NegotiationHandler,
    PriceTooLowError,
    ReasonCode,
)


def _make_terms(**overrides):
    defaults = {
        "service_type": "blockchain-news",
        "deliverables": "news summary",
        "quality_standards": "accurate, sourced",
    }
    defaults.update(overrides)
    return TermSpecification(**defaults)


def _make_request(**overrides):
    defaults = {
        "task_description": "Get latest news",
        "terms": _make_terms(),
    }
    defaults.update(overrides)
    return NegotiationRequest(**defaults)


class TestTermSpecification:
    def test_to_dict_required(self):
        t = _make_terms()
        d = t.to_dict()
        assert d["service_type"] == "blockchain-news"
        assert d["deliverables"] == "news summary"
        assert d["quality_standards"] == "accurate, sourced"
        assert "deadline_seconds" in d  # Always included (None)

    def test_to_dict_optional(self):
        t = _make_terms(
            success_criteria=["c1"],
            price="100",
            currency="0xToken",
        )
        d = t.to_dict()
        assert d["success_criteria"] == ["c1"]
        assert d["price"] == "100"
        assert d["currency"] == "0xToken"

    def test_from_dict_roundtrip(self):
        t = _make_terms(price="50", currency="0xABC")
        d = t.to_dict()
        t2 = TermSpecification.from_dict(d)
        assert t2.service_type == t.service_type
        assert t2.price == t.price

    def test_defaults(self):
        t = _make_terms()
        assert t.evaluation_required is True
        assert t.evaluator_type == "uma_oov3"


class TestNegotiationRequest:
    def test_to_dict(self):
        req = _make_request()
        d = req.to_dict()
        assert "task_description" in d
        assert "terms" in d

    def test_to_dict_with_optionals(self):
        req = _make_request(context_urls=["http://example.com"], request_id="r1")
        d = req.to_dict()
        assert d["context_urls"] == ["http://example.com"]
        assert d["request_id"] == "r1"

    def test_compute_hash_deterministic(self):
        req = _make_request()
        h1 = req.compute_hash()
        h2 = req.compute_hash()
        assert h1 == h2
        assert h1.startswith("0x")

    def test_to_from_envelope(self):
        req = _make_request()
        env = req.to_envelope()
        assert "request" in env
        assert "request_hash" in env
        req2, hash2 = NegotiationRequest.from_envelope(env)
        assert req2.task_description == req.task_description
        assert hash2 == env["request_hash"]

    def test_from_dict(self):
        d = {
            "task_description": "Do something",
            "terms": {
                "service_type": "test",
                "deliverables": "output",
                "quality_standards": "high",
            },
        }
        req = NegotiationRequest.from_dict(d)
        assert req.task_description == "Do something"
        assert req.terms.service_type == "test"

    def test_compute_hash_0x_prefix(self):
        req = _make_request()
        h = req.compute_hash()
        assert h.startswith("0x")
        assert len(h) == 66  # 0x + 64 hex chars


class TestNegotiationResponse:
    def test_to_dict_accepted(self):
        resp = NegotiationResponse(
            accepted=True,
            terms=_make_terms(price="100", currency="0xTok"),
            estimated_completion_seconds=60,
        )
        d = resp.to_dict()
        assert d["accepted"] is True
        assert "terms" in d
        assert d["estimated_completion_seconds"] == 60

    def test_to_dict_rejected(self):
        resp = NegotiationResponse(
            accepted=False,
            reason_code=ReasonCode.PRICE_TOO_LOW,
            reason="Too cheap",
        )
        d = resp.to_dict()
        assert d["accepted"] is False
        assert d["reason_code"] == "0x01"
        assert d["reason"] == "Too cheap"

    def test_compute_hash(self):
        resp = NegotiationResponse(accepted=True, terms=_make_terms(price="100", currency="0xTok"))
        h = resp.compute_hash()
        assert h.startswith("0x")
        assert len(h) == 66

    def test_to_from_envelope(self):
        resp = NegotiationResponse(accepted=True, terms=_make_terms(price="100", currency="0xTok"))
        env = resp.to_envelope()
        assert "response" in env
        assert "response_hash" in env
        resp2, hash2 = NegotiationResponse.from_envelope(env)
        assert resp2.accepted is True
        assert hash2 == env["response_hash"]

    def test_from_dict(self):
        d = {"accepted": False, "reason_code": "0x03", "reason": "Cannot do it"}
        resp = NegotiationResponse.from_dict(d)
        assert resp.accepted is False
        assert resp.reason_code == "0x03"

    def test_compute_hash_deterministic(self):
        resp = NegotiationResponse(accepted=False, reason_code="0x01")
        h1 = resp.compute_hash()
        h2 = resp.compute_hash()
        assert h1 == h2


class TestNegotiationResult:
    def test_accepted_property(self):
        result = NegotiationResult(
            request={}, request_hash="0x123",
            response={"accepted": True}, response_hash="0x456",
        )
        assert result.accepted is True

    def test_to_dict(self):
        result = NegotiationResult(
            request={"task": "x"}, request_hash="0xabc",
            response={"accepted": False}, response_hash="0xdef",
        )
        d = result.to_dict()
        assert d["request"] == {"task": "x"}
        assert d["request_hash"] == "0xabc"
        assert d["response"] == {"accepted": False}


class TestPriceTooLowError:
    def test_is_value_error(self):
        err = PriceTooLowError(1, 10)
        assert isinstance(err, ValueError)

    def test_message_format(self):
        err = PriceTooLowError(10**18, 10 * 10**18)
        msg = str(err)
        assert "below minimum" in msg
        assert "UMA bond" in msg


class TestNegotiationHandler:
    def _make_handler(self, **kwargs):
        defaults = {
            "base_price": "20000000000000000000",
            "currency": "0xToken",
        }
        defaults.update(kwargs)
        return NegotiationHandler(**defaults)

    def test_basic_accept(self):
        handler = self._make_handler()
        request_data = {
            "task_description": "Get news",
            "terms": {
                "service_type": "blockchain-news",
                "deliverables": "summary",
                "quality_standards": "accurate",
            },
        }
        result = handler.negotiate(request_data)
        assert result.accepted is True
        assert result.response["terms"]["price"] == "20000000000000000000"
        assert result.request_hash.startswith("0x")
        assert result.response_hash.startswith("0x")

    def test_invalid_format_rejection(self):
        handler = self._make_handler()
        result = handler.negotiate({"bad": "data"})
        assert result.accepted is False
        assert result.response.get("reason_code") == ReasonCode.AMBIGUOUS_TERMS

    def test_unsupported_service_type(self):
        handler = self._make_handler(supported_service_types=["translation"])
        request_data = {
            "task_description": "Get news",
            "terms": {
                "service_type": "blockchain-news",
                "deliverables": "summary",
                "quality_standards": "accurate",
            },
        }
        result = handler.negotiate(request_data)
        assert result.accepted is False
        assert result.response.get("reason_code") == ReasonCode.UNSUPPORTED

    def test_missing_quality_standards(self):
        handler = self._make_handler(require_quality_standards=True)
        request_data = {
            "task_description": "Do something",
            "terms": {
                "service_type": "test",
                "deliverables": "output",
                "quality_standards": "",
            },
        }
        result = handler.negotiate(request_data)
        assert result.accepted is False
        assert result.response.get("reason_code") == ReasonCode.AMBIGUOUS_TERMS

    def test_price_too_low_at_init(self):
        with pytest.raises(PriceTooLowError):
            NegotiationHandler(
                base_price="1",
                currency="0xToken",
                min_service_fee=10**18,
                validate_price=True,
            )

    def test_from_apex_client(self):
        mock_client = MagicMock()
        mock_client.min_service_fee.return_value = 10**18
        mock_client.payment_token.return_value = "0xTokenAddr"
        handler = NegotiationHandler.from_apex_client(
            apex_client=mock_client,
            base_price="20000000000000000000",
            validate_price=True,
        )
        assert handler._currency == "0xTokenAddr"
        assert handler.min_service_fee == 10**18
