"""format.artifact_integrity: fetched artifacts match their declared hash and size (media type:
the ``media_types`` allow-list).

Content is fetched from ``data:`` URIs (decoded inline), ``http(s)://`` (public hosts only:
private, loopback, link-local and reserved addresses are rejected before connecting and on
every redirect; downloads are size-capped) and ``memory://`` (through ``ctx.storage`` when the
context provides one). Hashes are keccak-256 of the raw bytes (``0x…`` or ``keccak256:…``);
``sha256:…`` is accepted too.
"""

from __future__ import annotations

import fnmatch
from typing import TYPE_CHECKING, Any

from ...net import DEFAULT_FETCH_CAP, FetchError, Resolver, fetch_artifact, hash_matches
from ...types.case import Artifact, EvalCase
from ..base import BaseMetric, Kind, MethodSpec, MetricContext, MetricSpec, Outcome
from ..registry import register

if TYPE_CHECKING:
    import httpx

__all__ = ["ArtifactIntegrity"]


def _short(uri: str) -> str:
    return uri if len(uri) <= 80 else uri[:77] + "..."


@register
class ArtifactIntegrity(BaseMetric):
    spec = MetricSpec(
        id="format.artifact_integrity",
        category="format",
        description="Fetched artifacts match their declared hash and size; declared media types "
        "are checked against the media_types allow-list.",
        methods=(MethodSpec("rule", Kind.RULE, required_inputs=frozenset({"artifacts"})),),
        params_schema={
            "type": "object",
            "properties": {
                "media_types": {"type": "array", "items": {"type": "string"}},
                "max_bytes": {"type": "integer", "minimum": 0},
            },
            "additionalProperties": False,
        },
        default_threshold=1.0,
        sources=("bnbagent-eval:new",),
    )

    def __init__(
        self, client: httpx.AsyncClient | None = None, resolver: Resolver | None = None
    ) -> None:
        self.client = client
        self.resolver = resolver

    async def _check(self, art: Artifact, ctx: MetricContext) -> dict[str, Any]:
        media_types = ctx.params.get("media_types")
        max_bytes = ctx.params.get("max_bytes")
        row: dict[str, Any] = {"uri": _short(art.uri), "name": art.name, "problems": []}
        if media_types is not None and not any(
            fnmatch.fnmatchcase(art.media_type.lower(), m.lower()) for m in media_types
        ):
            row["problems"].append("media_type")
        cap = DEFAULT_FETCH_CAP if max_bytes is None else max_bytes
        try:
            content, served = await fetch_artifact(
                art.uri,
                cap=cap,
                client=self.client,
                resolver=self.resolver,
                storage=getattr(ctx, "storage", None),
            )
        except FetchError as exc:
            row["problems"].append(exc.code)
            row["error"] = str(exc)
        else:
            row["bytes"] = len(content)
            row["served_media_type"] = served
            if not hash_matches(art.hash, content):
                row["problems"].append("hash_mismatch")
            if art.size is not None and art.size != len(content):
                row["problems"].append("size_mismatch")
        row["ok"] = not row["problems"]
        return row

    async def _evaluate_rule(self, case: EvalCase, ctx: MetricContext) -> Outcome:
        rows = [await self._check(art, ctx) for art in case.artifacts]
        passed = sum(1 for r in rows if r["ok"])
        failed = len(rows) - passed
        return Outcome(
            score=passed / len(rows),
            reason=f"{failed} artifact(s) failed integrity checks" if failed else None,
            details={"artifacts": rows},
        )
