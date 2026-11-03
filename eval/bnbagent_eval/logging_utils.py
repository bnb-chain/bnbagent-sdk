"""Logging helpers: secret redaction for log records and strings."""

from __future__ import annotations

import logging
import re

_SECRET_PATTERNS = [
    # Key = value, "key": "value" (JSON) and 'key': 'value' (Python repr of a dict).
    re.compile(
        r"(?i)(api[_-]?key|authorization|x-payment|private[_-]?key|secret|password|token)"
        r"([\"']?\s*[:=]\s*[\"']?)((?:bearer|basic)\s+)?([^\s\"',{][^\s\"',]*)"
    ),
    # An X-PAYMENT header carrying a JSON payload: redact the whole object.
    re.compile(r"(?i)(?<=x-payment: )\{[^\n]*"),
    re.compile(r"\bsk-[A-Za-z0-9_\-]{16,}\b"),  # common API-key shape
    re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=\-]{8,}"),  # bare bearer tokens
    re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b"),  # AWS access key ids
]

REDACTED = "***"


def redact(text: str) -> str:
    """Redact secrets (API keys, private keys, payment headers) from ``text``."""
    out = _SECRET_PATTERNS[0].sub(
        lambda m: f"{m.group(1)}{m.group(2)}{m.group(3) or ''}{REDACTED}", text
    )
    for pattern in _SECRET_PATTERNS[1:]:
        out = pattern.sub(REDACTED, out)
    return out


def get_logger(name: str) -> logging.Logger:
    """``logging.getLogger(name)`` with a :class:`RedactingFilter` attached (idempotent)."""
    logger = logging.getLogger(name)
    if not any(isinstance(f, RedactingFilter) for f in logger.filters):
        logger.addFilter(RedactingFilter())
    return logger


class RedactingFilter(logging.Filter):
    """A logging filter that redacts secrets from formatted messages."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:  # pragma: no cover - defensive: never break logging
            return True
        redacted = redact(message)
        if redacted != message:
            record.msg = redacted
            record.args = None
        return True
