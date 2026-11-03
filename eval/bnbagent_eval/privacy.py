"""Report privacy: hash-only reports by default, plus regex redaction.

Convention for metric authors: detail keys that carry case *content* end in ``_text`` (or are
named ``excerpt``); they are dropped in ``hashes`` mode and redacted in ``redacted`` mode.
"""

from __future__ import annotations

import dataclasses
import re
from collections.abc import Mapping
from typing import Any

from .types.options import Privacy
from .types.result import CaseResult, MetricResult, RunResult

REDACTED = "[redacted]"
REDACT_TIMEOUT_S = 0.5  # per pattern and string (``regex`` engine)


def is_content_key(key: str) -> bool:
    return key == "excerpt" or key.endswith("_text")


def _compile(patterns: tuple[str, ...] | list[str]) -> list[Any]:
    """Compile with the ``regex`` engine (it supports timeouts) when installed."""
    from ._optional import available

    if available("regex"):
        import regex

        return [regex.compile(p) for p in patterns]
    return [re.compile(p) for p in patterns]


def _redact_text(text: str, patterns: list[Any]) -> str:
    for p in patterns:
        if isinstance(p, re.Pattern):
            text = p.sub(REDACTED, text)
            continue
        try:
            text = p.sub(REDACTED, text, timeout=REDACT_TIMEOUT_S)
        except TimeoutError:
            return REDACTED  # fail closed: a pattern that cannot finish redacts everything
    return text


def _scrub(value: Any, patterns: list[Any], mode: str) -> Any:
    if isinstance(value, str):
        return _redact_text(value, patterns)
    if isinstance(value, Mapping):
        out = {}
        for k, v in value.items():
            if is_content_key(str(k)):
                if mode == "hashes":
                    continue
                if mode == "redacted":
                    out[k] = REDACTED
                    continue
            out[k] = _scrub(v, patterns, mode)
        return out
    if isinstance(value, (list, tuple)):
        return [_scrub(v, patterns, mode) for v in value]
    return value


def _apply_metric(result: MetricResult, patterns: list[Any], mode: str) -> MetricResult:
    if mode == "full" and not patterns:
        return result
    reason = _redact_text(result.reason, patterns) if result.reason else result.reason
    error = result.error
    if error is not None and error.message and patterns:
        error = dataclasses.replace(error, message=_redact_text(error.message, patterns))
    return dataclasses.replace(
        result, reason=reason, details=_scrub(result.details, patterns, mode), error=error
    )


def _apply_run(run: RunResult, patterns: list[Any]) -> RunResult:
    if not patterns or not run.error:
        return run
    return dataclasses.replace(run, error=_redact_text(run.error, patterns))


def apply(cases: tuple[CaseResult, ...], privacy: Privacy) -> tuple[CaseResult, ...]:
    """Return ``cases`` with the privacy policy applied to every metric result and run error."""
    patterns = _compile(privacy.redact)
    mode = privacy.report_contents
    return tuple(
        dataclasses.replace(
            c,
            metrics=tuple(_apply_metric(m, patterns, mode) for m in c.metrics),
            runs=tuple(_apply_run(r, patterns) for r in c.runs),
        )
        for c in cases
    )


# --------------------------------------------------------------------------- report encryption

ENVELOPE_SCHEME = "nacl-secretbox+sealedbox/v1"


def generate_keypair() -> tuple[str, str]:
    """A new X25519 key pair as ``(private_hex, public_hex)`` (``[crypto]``)."""
    from ._optional import require

    public = require("nacl.public", "crypto")
    key = public.PrivateKey.generate()
    return bytes(key).hex(), bytes(key.public_key).hex()


def encrypt_report(
    doc: Mapping[str, Any], recipients: list[str] | tuple[str, ...]
) -> dict[str, Any]:
    """Encrypt a JSON document to X25519 public keys (hex) for public storage (``[crypto]``).

    A random symmetric key encrypts the canonical JSON (SecretBox); the key is sealed to each
    recipient (SealedBox). ``plaintext_hash`` lets a recipient check what was decrypted.
    """
    import base64

    from ._optional import require
    from .hashing import canonical_json, keccak_hex

    if not recipients:
        raise ValueError("at least one recipient public key is required")
    public = require("nacl.public", "crypto")
    secret = require("nacl.secret", "crypto")
    utils = require("nacl.utils", "crypto")
    plaintext = canonical_json(dict(doc))
    key = utils.random(secret.SecretBox.KEY_SIZE)
    ciphertext = secret.SecretBox(key).encrypt(plaintext.encode())
    sealed = [
        {
            "public_key": r,
            "sealed_key": base64.b64encode(
                public.SealedBox(public.PublicKey(bytes.fromhex(r))).encrypt(key)
            ).decode(),
        }
        for r in recipients
    ]
    return {
        "scheme": ENVELOPE_SCHEME,
        "recipients": sealed,
        "ciphertext": base64.b64encode(ciphertext).decode(),
        "plaintext_hash": keccak_hex(plaintext),
    }


def decrypt_report(envelope: Mapping[str, Any], private_key_hex: str) -> dict[str, Any]:
    """Decrypt an envelope with a recipient's X25519 private key (hex); verifies the hash."""
    import base64
    import json

    from ._optional import require
    from .hashing import keccak_hex

    if envelope.get("scheme") != ENVELOPE_SCHEME:
        raise ValueError(f"unsupported envelope scheme {envelope.get('scheme')!r}")
    public = require("nacl.public", "crypto")
    secret = require("nacl.secret", "crypto")
    exceptions = require("nacl.exceptions", "crypto")
    private = public.PrivateKey(bytes.fromhex(private_key_hex))
    mine = bytes(private.public_key).hex()
    entry = next((r for r in envelope["recipients"] if r["public_key"].lower() == mine), None)
    if entry is None:
        raise ValueError("this key is not a recipient of the envelope")
    try:
        key = public.SealedBox(private).decrypt(base64.b64decode(entry["sealed_key"]))
        plaintext = secret.SecretBox(key).decrypt(base64.b64decode(envelope["ciphertext"]))
    except exceptions.CryptoError as exc:
        raise ValueError(f"decryption failed: {exc}") from exc
    if keccak_hex(plaintext.decode()) != envelope["plaintext_hash"]:
        raise ValueError("decrypted content does not match plaintext_hash")
    return json.loads(plaintext)
