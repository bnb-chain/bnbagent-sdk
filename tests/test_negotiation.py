"""Tests for negotiation data structures and handler."""

import json
import time
from unittest.mock import MagicMock

import pytest

from bnbagent.apex.negotiation import (
    NegotiationHandler,
    NegotiationRequest,
    NegotiationResponse,
    NegotiationResult,
    ReasonCode,
    TermSpecification,
    _sanitize_for_claim,
    build_job_description,
    parse_job_description,
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


def _make_accepted_result(
    task="Get latest news",
    price="20000000000000000000",
    currency="0xToken",
    negotiation_hash="0xabc",
    provider_sig="0xsig",
) -> dict:
    """Build a minimal accepted negotiation result dict for testing build_job_description."""
    now = int(time.time())
    return {
        "request": {
            "task_description": task,
            "terms": {
                "service_type": "blockchain-news",
                "deliverables": "news summary",
                "quality_standards": "accurate, sourced",
                "deadline_seconds": None,
                "evaluation_required": True,
                "evaluator_type": "uma_oov3",
            },
        },
        "request_hash": "0xreq",
        "response": {
            "accepted": True,
            "terms": {
                "service_type": "blockchain-news",
                "deliverables": "news summary",
                "quality_standards": "accurate, sourced",
                "deadline_seconds": None,
                "evaluation_required": True,
                "evaluator_type": "uma_oov3",
                "price": price,
                "currency": currency,
            },
            "quote_expires_at": now + 3600,
            "negotiated_at": now,
        },
        "response_hash": "0xresp",
        "negotiation_hash": negotiation_hash,
        "provider_sig": provider_sig,
    }


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

    def test_to_dict_with_quote_expires_at(self):
        exp = int(time.time()) + 3600
        resp = NegotiationResponse(
            accepted=True,
            terms=_make_terms(price="100", currency="0xTok"),
            quote_expires_at=exp,
        )
        d = resp.to_dict()
        assert d["quote_expires_at"] == exp

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

    def test_compute_hash_includes_quote_expires_at(self):
        exp = 9999999
        resp1 = NegotiationResponse(accepted=True, terms=_make_terms(price="1", currency="0x"))
        resp2 = NegotiationResponse(
            accepted=True, terms=_make_terms(price="1", currency="0x"), quote_expires_at=exp
        )
        assert resp1.compute_hash() != resp2.compute_hash()

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

    def test_from_dict_quote_expires_at(self):
        exp = int(time.time()) + 1800
        d = {"accepted": True, "quote_expires_at": exp}
        resp = NegotiationResponse.from_dict(d)
        assert resp.quote_expires_at == exp

    def test_compute_hash_deterministic(self):
        resp = NegotiationResponse(accepted=False, reason_code="0x01")
        h1 = resp.compute_hash()
        h2 = resp.compute_hash()
        assert h1 == h2


class TestNegotiationResult:
    def test_accepted_property(self):
        result = NegotiationResult(
            request={},
            request_hash="0x123",
            response={"accepted": True},
            response_hash="0x456",
        )
        assert result.accepted is True

    def test_to_dict_basic(self):
        result = NegotiationResult(
            request={"task": "x"},
            request_hash="0xabc",
            response={"accepted": False},
            response_hash="0xdef",
        )
        d = result.to_dict()
        assert d["request"] == {"task": "x"}
        assert d["request_hash"] == "0xabc"
        assert d["response"] == {"accepted": False}
        assert "negotiation_hash" not in d
        assert "provider_sig" not in d

    def test_to_dict_with_sig(self):
        result = NegotiationResult(
            request={},
            request_hash="0x1",
            response={"accepted": True},
            response_hash="0x2",
            negotiation_hash="0xhash",
            provider_sig="0xsig",
        )
        d = result.to_dict()
        assert d["negotiation_hash"] == "0xhash"
        assert d["provider_sig"] == "0xsig"


class TestSanitizeForClaim:
    def test_replaces_brackets(self):
        assert _sanitize_for_claim("[REQUEST]") == "(REQUEST)"
        assert _sanitize_for_claim("[RESPONSE]") == "(RESPONSE)"
        assert _sanitize_for_claim("[VERIFY]") == "(VERIFY)"

    def test_strips_null_bytes(self):
        assert "\x00" not in _sanitize_for_claim("hello\x00world")

    def test_strips_control_chars(self):
        # ASCII control chars below 0x20 (except tab/newline) should be removed
        assert "\x01" not in _sanitize_for_claim("a\x01b")
        assert "\x1f" not in _sanitize_for_claim("a\x1fb")

    def test_preserves_normal_text(self):
        s = "Accurate, well-sourced, covers at least 5 news items"
        assert _sanitize_for_claim(s) == s

    def test_handles_non_string(self):
        assert isinstance(_sanitize_for_claim(42), str)  # type: ignore[arg-type]


class TestBuildJobDescription:
    def test_basic_structure(self):
        result = _make_accepted_result()
        desc = build_job_description(result)
        parsed = json.loads(desc)
        assert parsed["v"] == 1
        assert parsed["task"] == "Get latest news"
        assert "terms" in parsed
        assert parsed["price"] == "20000000000000000000"
        assert parsed["currency"] == "0xToken"
        assert parsed["negotiation_hash"] == "0xabc"
        assert parsed["provider_sig"] == "0xsig"

    def test_terms_content(self):
        result = _make_accepted_result()
        desc = build_job_description(result)
        parsed = json.loads(desc)
        terms = parsed["terms"]
        assert terms["service_type"] == "blockchain-news"
        assert terms["deliverables"] == "news summary"
        assert terms["quality_standards"] == "accurate, sourced"
        # price and currency should NOT be in terms
        assert "price" not in terms
        assert "currency" not in terms

    def test_compact_json(self):
        result = _make_accepted_result()
        desc = build_job_description(result)
        # Verify compact format: re-serializing the parsed result should match
        parsed = json.loads(desc)
        expected_compact = json.dumps(parsed, sort_keys=True, separators=(",", ":"))
        assert desc == expected_compact

    def test_sanitizes_brackets_in_task(self):
        result = _make_accepted_result(task="[REQUEST] tricky task [VERIFY]")
        desc = build_job_description(result)
        assert "[" not in desc or "negotiation_hash" in desc  # only hex values may have no brackets
        parsed = json.loads(desc)
        assert "[" not in parsed["task"]

    def test_raises_on_rejected_negotiation(self):
        rejected = {
            "request": {"task_description": "x", "terms": {}},
            "request_hash": "0x",
            "response": {"accepted": False, "reason": "No"},
            "response_hash": "0x",
        }
        with pytest.raises(ValueError, match="rejected"):
            build_job_description(rejected)

    def test_raises_missing_price(self):
        result = _make_accepted_result(price="")
        with pytest.raises(ValueError, match="price"):
            build_job_description(result)

    def test_raises_missing_currency(self):
        result = _make_accepted_result(currency="")
        with pytest.raises(ValueError, match="currency"):
            build_job_description(result)

    def test_max_length_truncates_task(self):
        long_task = "A" * 1000
        result = _make_accepted_result(task=long_task)
        desc = build_job_description(result, max_length=500)
        assert len(desc) <= 500
        parsed = json.loads(desc)
        assert parsed["task"].endswith("...")

    def test_quote_expires_at_included(self):
        result = _make_accepted_result()
        desc = build_job_description(result)
        parsed = json.loads(desc)
        assert "quote_expires_at" in parsed
        assert parsed["quote_expires_at"] > int(time.time())

    def test_without_sig(self):
        result = _make_accepted_result(negotiation_hash="", provider_sig="")
        desc = build_job_description(result)
        parsed = json.loads(desc)
        assert "negotiation_hash" not in parsed
        assert "provider_sig" not in parsed

    def test_success_criteria_included(self):
        result = _make_accepted_result()
        result["response"]["terms"]["success_criteria"] = ["criterion 1", "criterion 2"]
        desc = build_job_description(result)
        parsed = json.loads(desc)
        assert parsed["terms"]["success_criteria"] == ["criterion 1", "criterion 2"]


class TestParseJobDescription:
    def test_parses_valid_structured(self):
        result = _make_accepted_result()
        desc = build_job_description(result)
        parsed = parse_job_description(desc)
        assert parsed is not None
        assert parsed["v"] == 1
        assert "task" in parsed

    def test_returns_none_for_plain_text(self):
        assert parse_job_description("Search for BNB Chain news") is None

    def test_returns_none_for_empty(self):
        assert parse_job_description("") is None

    def test_returns_none_for_invalid_json(self):
        assert parse_job_description("{not valid json}") is None

    def test_returns_none_for_json_without_v(self):
        assert parse_job_description('{"task": "something"}') is None

    def test_roundtrip(self):
        result = _make_accepted_result()
        desc = build_job_description(result)
        parsed = parse_job_description(desc)
        assert parsed["task"] == "Get latest news"
        assert parsed["price"] == "20000000000000000000"


class TestNegotiationHandler:
    def _make_handler(self, **kwargs):
        defaults = {
            "service_price": "20000000000000000000",
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

    def test_quote_expires_at_in_response(self):
        handler = self._make_handler(quote_ttl_seconds=1800)
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
        quote_exp = result.response.get("quote_expires_at")
        assert quote_exp is not None
        assert quote_exp > int(time.time())
        assert quote_exp <= int(time.time()) + 1800 + 5  # small tolerance

    def test_signs_with_wallet_provider(self):
        mock_wallet = MagicMock()
        mock_wallet.sign_message.return_value = {"signature": b"\xab" * 65}

        handler = self._make_handler(wallet_provider=mock_wallet)
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
        assert result.negotiation_hash.startswith("0x")
        assert result.provider_sig.startswith("0x")
        mock_wallet.sign_message.assert_called_once()

    def test_no_sig_without_wallet(self):
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
        assert result.negotiation_hash == ""
        assert result.provider_sig == ""

    def test_negotiation_hash_is_keccak256_of_content(self):
        from web3 import Web3
        from bnbagent.apex.negotiation import _build_description_content

        mock_wallet = MagicMock()
        mock_wallet.sign_message.return_value = {"signature": b"\xab" * 65}
        handler = self._make_handler(wallet_provider=mock_wallet)

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

        # Re-derive the hash independently
        result_dict = result.to_dict()
        content = _build_description_content(result_dict)
        canonical = json.dumps(content, sort_keys=True, separators=(",", ":"))
        expected_hash = "0x" + Web3.keccak(text=canonical).hex().lstrip("0x")
        # Compare (both should have 0x prefix and 64 hex chars)
        assert result.negotiation_hash == expected_hash or result.negotiation_hash.lstrip("0x") == expected_hash.lstrip("0x")

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

    def test_from_apex_client(self):
        mock_client = MagicMock()
        mock_client.payment_token = "0xTokenAddr"
        handler = NegotiationHandler.from_apex_client(
            apex_client=mock_client,
            service_price="20000000000000000000",
        )
        assert handler._currency == "0xTokenAddr"

    def test_from_apex_client_passes_wallet(self):
        mock_client = MagicMock()
        mock_client.payment_token = "0xTokenAddr"
        mock_wallet = MagicMock()
        handler = NegotiationHandler.from_apex_client(
            apex_client=mock_client,
            service_price="20000000000000000000",
            wallet_provider=mock_wallet,
        )
        assert handler._wallet_provider is mock_wallet
