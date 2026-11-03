"""ERC-8183 deliverable manifest — byte-for-byte the SDK's ``DeliverableManifest`` rules.

The on-chain ``deliverable`` is ``keccak256(json.dumps(manifest, sort_keys=True,
separators=(",", ":")))`` (default ``ensure_ascii=True``; no float normalization). This module
re-implements that without the SDK so read-only verifiers need no web3; a parity test in the
bnbagent adapter suite checks it against ``DeliverableManifest.manifest_hash()``.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from ..hashing import keccak_hex

MANIFEST_VERSION = 1


def canonical_manifest_json(manifest: Mapping[str, Any]) -> str:
    return json.dumps(dict(manifest), sort_keys=True, separators=(",", ":"))


def manifest_hash(manifest: Mapping[str, Any]) -> str:
    """0x-prefixed keccak of the canonical manifest JSON (== the on-chain ``deliverable``)."""
    return keccak_hex(canonical_manifest_json(manifest))


def build_manifest(
    *,
    job_id: int,
    chain_id: int,
    contracts: Mapping[str, str],
    content: str,
    content_type: str = "text/plain",
    metadata: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "version": MANIFEST_VERSION,
        "job_id": job_id,
        "chain_id": chain_id,
        "contracts": dict(contracts),
        "response": {"content": content, "content_type": content_type},
        "metadata": dict(metadata or {}),
    }


def parse_manifest(data: Mapping[str, Any]) -> dict[str, Any]:
    """Validate like the SDK's ``DeliverableManifest.from_dict`` and return the canonical dict."""
    if data.get("version") != MANIFEST_VERSION:
        raise ValueError(f"unsupported manifest version {data.get('version')!r}")
    response = data.get("response")
    if not isinstance(response, Mapping) or "content" not in response:
        raise ValueError("manifest response must be an object with 'content'")
    for name in ("job_id", "chain_id", "contracts"):
        if name not in data:
            raise ValueError(f"manifest missing required field {name!r}")
    return {
        "version": data["version"],
        "job_id": data["job_id"],
        "chain_id": data["chain_id"],
        "contracts": data["contracts"],
        "response": dict(response),
        "metadata": dict(data.get("metadata") or {}),
    }


def same_hash(a: str | bytes | None, b: str | bytes | None) -> bool:
    def norm(x: str | bytes | None) -> str | None:
        if x is None:
            return None
        if isinstance(x, (bytes, bytearray)):
            return "0x" + bytes(x).hex()
        return x.lower() if x.startswith("0x") else "0x" + x.lower()

    return norm(a) is not None and norm(a) == norm(b)


def content_hash(content: str) -> str:
    """``output_hash`` for a JobSubject: keccak of the deliverable content (UTF-8)."""
    return keccak_hex(content)
