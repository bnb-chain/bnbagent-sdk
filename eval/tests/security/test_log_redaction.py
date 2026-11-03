"""``logging_utils`` redaction: secrets inside Python dict reprs and X-PAYMENT headers with a
JSON payload are redacted."""

from __future__ import annotations

import logging

from bnbagent_eval.logging_utils import get_logger, redact

KEY = "0x" + "ab" * 32  # not a real key


def test_python_dict_repr_private_key_is_redacted() -> None:
    text = str({"private_key": KEY, "max_tokens": 5, "api_key": "abc123"})
    out = redact(text)
    assert KEY not in out and "abc123" not in out
    assert "'max_tokens': 5" in out  # not a secret


def test_x_payment_json_header_is_redacted() -> None:
    header = 'X-PAYMENT: {"nonce": "n-1", "signature": "0x1234deadbeef", "payer": "0x01"}'
    out = redact(header)
    assert "deadbeef" not in out and "nonce" not in out
    assert redact('{"X-PAYMENT": "eyJhYmMiOiAxfQ=="}') == '{"X-PAYMENT": "***"}'


def test_existing_forms_still_redacted() -> None:
    assert redact(f"PRIVATE_KEY={KEY}") == "PRIVATE_KEY=***"
    assert redact('{"password": "hunter2"}') == '{"password": "***"}'
    assert "abcdefghijk" not in redact("Authorization: Bearer abcdefghijk")
    tx = "0x" + "cd" * 32
    assert redact(f"tx {tx}") == f"tx {tx}"  # hashes are not secrets


def test_logger_redacts_dict_args(caplog) -> None:
    log = get_logger("bnbagent_eval.redaction_test")
    with caplog.at_level(logging.INFO, logger="bnbagent_eval.redaction_test"):
        log.info("wallet config %s", {"private_key": KEY})
    assert KEY not in caplog.text and "***" in caplog.text
