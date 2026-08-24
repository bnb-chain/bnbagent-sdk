"""Turnkey API-key request stamping (X-Stamp header).

Turnkey authenticates every request by a signature over the exact JSON body:
the caller signs the raw bytes with a locally held P-256 API key (ECDSA /
SHA-256, DER-encoded), wraps it as
``{"publicKey", "scheme": "SIGNATURE_SCHEME_TK_API_P256", "signature"}`` and
sends it base64url-encoded (padding stripped) in the ``X-Stamp`` header.
Mirrors ``@turnkey/api-key-stamper`` byte-for-byte; the private key never
leaves this process.

The P-256 primitive comes from the ``cryptography`` package, an **optional
extra** (``pip install 'bnbagent[turnkey]'``) imported lazily at first use —
mirroring the TypeScript side's optional-peer semantics, so ``import
bnbagent`` never requires it.
"""

from __future__ import annotations

import base64
import json

STAMP_HEADER_NAME = "X-Stamp"
_SIGNATURE_SCHEME = "SIGNATURE_SCHEME_TK_API_P256"

_INSTALL_HINT = (
    "The Turnkey wallet provider requires the optional 'cryptography' "
    "dependency (not installed). Install it with: pip install 'bnbagent[turnkey]'"
)


def _load_ec():
    """Import the P-256 primitives lazily, with an actionable error."""
    try:
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import ec
    except ImportError as exc:  # pragma: no cover - exercised via unit test
        raise RuntimeError(_INSTALL_HINT) from exc
    return ec, hashes


def _strip_hex(value: str) -> str:
    return value[2:] if value.startswith(("0x", "0X")) else value


class ApiKeyStamper:
    """Stamps request bodies with a Turnkey P-256 API key pair.

    Args:
        api_public_key: Compressed P-256 public key, hex (33 bytes / 66 hex
            chars, ``02``/``03`` prefix).
        api_private_key: Raw P-256 private scalar, hex (32 bytes).

    Raises:
        RuntimeError: If ``cryptography`` is not installed, or the public key
            does not match the private key (swapped/wrong credentials fail
            here with a clear message instead of an opaque 401).
    """

    def __init__(self, *, api_public_key: str, api_private_key: str) -> None:
        ec, hashes = _load_ec()
        self._hashes = hashes
        self._ec = ec
        self._api_public_key = _strip_hex(api_public_key).lower()
        try:
            self._key = ec.derive_private_key(int(_strip_hex(api_private_key), 16), ec.SECP256R1())
        except ValueError as exc:
            raise RuntimeError(
                f"TURNKEY_API_PRIVATE_KEY is not a valid P-256 private key hex: {exc}"
            ) from exc

        from cryptography.hazmat.primitives.serialization import (
            Encoding,
            PublicFormat,
        )

        derived = self._key.public_key().public_bytes(Encoding.X962, PublicFormat.CompressedPoint)
        if derived.hex() != self._api_public_key:
            raise RuntimeError(
                "TURNKEY_API_PUBLIC_KEY does not match TURNKEY_API_PRIVATE_KEY "
                "(the compressed public key derived from the private key differs) "
                "— check that both halves come from the same Turnkey API key."
            )

    def stamp(self, payload: str) -> tuple[str, str]:
        """Sign ``payload`` (the exact request body string) for ``X-Stamp``.

        Returns:
            ``(header_name, header_value)``.
        """
        signature = self._key.sign(payload.encode("utf-8"), self._ec.ECDSA(self._hashes.SHA256()))
        stamp = {
            "publicKey": self._api_public_key,
            "scheme": _SIGNATURE_SCHEME,
            "signature": signature.hex(),
        }
        encoded = base64.urlsafe_b64encode(
            json.dumps(stamp, separators=(",", ":")).encode("utf-8")
        )
        return STAMP_HEADER_NAME, encoded.decode("ascii").rstrip("=")
