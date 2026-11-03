"""``custom.human_review``: a human-in-the-loop metric.

The first evaluation submits a review task to a sink and returns ``status="pending_human"``
(the verdict becomes ``pending_human``). Re-running the evaluation picks up the reviewer's
score once it exists. A score only counts if it is signed (EIP-191, ``[evm]``) by one of the
configured ``reviewers``; unsigned or wrong-signer scores are rejected.

Sinks: ``file`` (``<path>/tasks/<task_id>.json`` in, ``<path>/results/<task_id>.json`` out) and
``webhook`` (``POST <url>`` with the task; ``GET <url>/<task_id>`` returns the result or 404).
Local mode only: the eval service denies this method.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from ..._optional import require
from ...hashing import hash_obj
from ...types.case import EvalCase
from ..base import BaseMetric, Kind, MethodSpec, MetricContext, MetricSpec, Outcome
from ..registry import register
from ..text._common import as_text

MESSAGE_PREFIX = "bnbagent-eval human review"


def score_message(task_id: str, score: float) -> bytes:
    """The exact bytes a reviewer signs (EIP-191 personal_sign)."""
    return f"{MESSAGE_PREFIX}\ntask:{task_id}\nscore:{score:.6f}".encode()


def sign_human_score(task_id: str, score: float, signer: Any, reason: str = "") -> dict:
    """Produce a result document for ``task_id`` signed by ``signer`` (a SignerPort)."""
    if not 0.0 <= score <= 1.0:
        raise ValueError("score must be within [0, 1]")
    return {
        "task_id": task_id,
        "score": score,
        "reason": reason,
        "reviewer": signer.address,
        "signature": signer.sign_message(score_message(task_id, score)),
    }


def verify_human_score(result: Mapping[str, Any], task_id: str, reviewers: list[str]) -> str:
    """Return the reviewer address, or raise ``ValueError`` if the score is not acceptable."""
    if result.get("task_id") != task_id:
        raise ValueError("result is for a different task")
    score = float(result["score"])
    if not 0.0 <= score <= 1.0:
        raise ValueError("score out of range")
    signature = result.get("signature")
    if not signature:
        raise ValueError("unsigned score")
    account = require("eth_account", "evm").Account
    messages = require("eth_account.messages", "evm")
    try:
        signer = account.recover_message(
            messages.encode_defunct(primitive=score_message(task_id, score)), signature=signature
        )
    except Exception as exc:  # noqa: BLE001 - eth_account raises its own ValidationError / BadSignature
        raise ValueError(f"invalid signature: {type(exc).__name__}: {exc}") from exc
    allowed = {r.lower() for r in reviewers}
    if signer.lower() not in allowed:
        raise ValueError(f"score signed by {signer}, who is not an allowed reviewer")
    return signer


class FileSink:
    def __init__(self, path: str | Path):
        self.root = Path(path)

    async def submit(self, task: Mapping[str, Any]) -> None:
        file = self.root / "tasks" / f"{task['task_id']}.json"
        if not file.exists():
            file.parent.mkdir(parents=True, exist_ok=True)
            file.write_text(json.dumps(dict(task), indent=2, ensure_ascii=False))

    async def result(self, task_id: str) -> dict[str, Any] | None:
        file = self.root / "results" / f"{task_id}.json"
        return json.loads(file.read_text()) if file.exists() else None


class WebhookSink:
    def __init__(self, url: str, *, client: Any = None, headers: Mapping[str, str] | None = None):
        self.url = url.rstrip("/")
        self.headers = dict(headers or {})
        self._client = client

    def _http(self):
        if self._client is None:
            import httpx

            self._client = httpx.AsyncClient(timeout=30)
        return self._client

    async def submit(self, task: Mapping[str, Any]) -> None:
        resp = await self._http().post(self.url, json=dict(task), headers=self.headers)
        resp.raise_for_status()

    async def result(self, task_id: str) -> dict[str, Any] | None:
        resp = await self._http().get(f"{self.url}/{task_id}", headers=self.headers)
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        return resp.json()


def make_sink(params: Mapping[str, Any], client: Any = None):
    kind = params.get("sink", "file")
    if kind == "file":
        return FileSink(params["path"])
    if kind == "webhook":
        return WebhookSink(params["url"], client=client)
    raise ValueError(f"unknown sink {kind!r}")


@register
class HumanReview(BaseMetric):
    spec = MetricSpec(
        id="custom.human_review",
        category="custom",
        description="A signed human score; pending until an allowed reviewer has scored it.",
        methods=(
            MethodSpec(
                "rule",
                Kind.RULE,
                optional_inputs=frozenset({"input", "output", "expected"}),
                service_allowed=False,
            ),
        ),
        params_schema={
            "type": "object",
            "required": ["reviewers"],
            "properties": {
                "sink": {"enum": ["file", "webhook"]},
                "path": {"type": "string"},
                "url": {"type": "string"},
                "reviewers": {"type": "array", "minItems": 1, "items": {"type": "string"}},
                "question": {"type": "string"},
            },
            "additionalProperties": False,
        },
        default_threshold=0.5,
        version="2",  # v2: a malformed signature is a rejected result (pending), not an error
        sources=("bnbagent-eval:new",),
    )

    def __init__(self, client: Any = None):
        self._client = client  # injectable HTTP client (tests)

    def task(self, case: EvalCase, params: Mapping[str, Any]) -> dict[str, Any]:
        body = {
            "case_id": case.id,
            "question": params.get("question", "Rate the output from 0 to 1."),
            "input": as_text(case.input) if case.input is not None else None,
            "output": as_text(case.output) if case.output is not None else None,
            "expected": as_text(case.expected) if case.expected is not None else None,
        }
        return {"task_id": hash_obj(body)[2:18], **body, "reviewers": list(params["reviewers"])}

    async def _evaluate_rule(self, case: EvalCase, ctx: MetricContext) -> Outcome:
        params = ctx.params
        if params.get("sink", "file") == "file" and "path" not in params:
            raise ValueError("the file sink needs params.path")
        if params.get("sink") == "webhook" and "url" not in params:
            raise ValueError("the webhook sink needs params.url")
        sink = make_sink(params, self._client)
        task = self.task(case, params)
        result = await sink.result(task["task_id"])
        if result is None:
            await sink.submit(task)
            return Outcome(None, status="pending_human", details={"task_id": task["task_id"]})
        try:
            reviewer = verify_human_score(result, task["task_id"], list(params["reviewers"]))
        except (ValueError, KeyError, TypeError) as exc:
            return Outcome(
                None,
                status="pending_human",
                details={"task_id": task["task_id"], "rejected_result": str(exc)},
            )
        return Outcome(
            score=float(result["score"]),
            reason=result.get("reason") or None,
            details={"task_id": task["task_id"], "reviewer": reviewer},
        )
