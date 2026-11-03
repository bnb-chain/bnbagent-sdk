"""ERC-8004: publish evaluation metrics in an agent's registration file.

``metrics`` is a list keyed by ``suite_hash``: publishing a second suite adds an entry, the same
suite replaces its entry, every other key of the registration file is preserved. Metrics are
written with ``set_agent_uri`` after registration (the SDK's ``register_agent`` drops unknown
keys). The full report goes to storage first, so a failed chain write leaves the old URI intact.
"""

from __future__ import annotations

import base64
import json
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from ..exceptions import ChainIntegrationError
from ..hashing import hash_obj
from ..ports import RegistryPort, StoragePort
from ..privacy import encrypt_report
from ..types.request import EvalResponse
from ..types.subject import AgentSubject

SCHEMA = "bnbagent-eval/registration-metrics/v1"
DATA_URI_PREFIX = "data:application/json;base64,"
METADATA_KEY_PREFIX = "eval.metrics."

# public name -> (metric ids in priority order, aggregate field)
DEFAULT_PUBLIC_NAMES: dict[str, tuple[tuple[str, ...], str]] = {
    "accuracy": (
        (
            "quality.answer_correctness",
            "text.exact_match",
            "text.quasi_exact_match",
            "text.token_f1",
        ),
        "mean",
    ),
    "consistency": (("ops.consistency",), "mean"),
    "latency_p95_ms": (("ops.latency",), "value_p95"),
    "cost_per_call_usd": (("ops.cost",), "value_mean"),
    "reliability": (("ops.reliability",), "mean"),
}


def encode_data_uri(doc: Mapping[str, Any]) -> str:
    """Same encoding as the SDK's ``generate_agent_uri`` (canonical JSON, base64)."""
    raw = json.dumps(dict(doc), sort_keys=True, separators=(",", ":")).encode()
    return DATA_URI_PREFIX + base64.b64encode(raw).decode()


def public_metrics(
    response: EvalResponse, names: Mapping[str, tuple[tuple[str, ...], str]] = DEFAULT_PUBLIC_NAMES
) -> dict[str, float]:
    """Stable public metric names (``overall``, ``accuracy``, ``safety``, ...) from a response."""
    by_id: dict[str, Any] = {}
    for m in response.aggregate.metrics:
        by_id.setdefault(m.metric_id, m)
    out: dict[str, float] = {}
    if response.aggregate.score is not None:
        out["overall"] = response.aggregate.score
    if response.aggregate.pass_rate is not None:
        out["pass_rate"] = response.aggregate.pass_rate
    for public, (ids, attr) in names.items():
        for metric_id in ids:
            agg = by_id.get(metric_id)
            value = getattr(agg, attr, None) if agg is not None else None
            if value is not None:
                out[public] = value
                break
    safety = [
        m.mean
        for m in response.aggregate.metrics
        if m.metric_id.startswith("safety.") and m.mean is not None
    ]
    if safety:
        out["safety"] = sum(safety) / len(safety)
    return out


@dataclass(frozen=True)
class PublishResult:
    success: bool
    agent_uri: str | None = None
    tx_hash: str | None = None
    metadata_tx: str | None = None
    report_uri: str | None = None
    report_hash: str | None = None
    entry: Mapping[str, Any] = field(default_factory=dict)
    error: str | None = None


class MetricsPublisher:
    def __init__(
        self,
        registry: RegistryPort,
        storage: StoragePort | None = None,
        *,
        public_names: Mapping[str, tuple[tuple[str, ...], str]] | None = None,
        encrypt_to: tuple[str, ...] = (),
        clock=time.time,
    ):
        self.encrypt_to = tuple(encrypt_to)  # X25519 public keys: reports are encrypted
        self.registry = registry
        self.storage = storage
        self.public_names = dict(public_names or DEFAULT_PUBLIC_NAMES)
        self.clock = clock

    def build_entry(
        self,
        response: EvalResponse,
        *,
        report_uri: str | None = None,
        report_hash: str | None = None,
        benchmark: Mapping[str, Any] | None = None,
        ttl_days: int | None = 90,
    ) -> dict[str, Any]:
        now = int(self.clock())
        subject = response.subject if isinstance(response.subject, AgentSubject) else None
        entry: dict[str, Any] = {
            "schema": SCHEMA,
            "suite_hash": response.suite_hash,
            "result_hash": response.result_hash,
            "metrics": public_metrics(response, self.public_names),
            "verdict": response.verdict.status,
            "cases": response.aggregate.cases,
            "agent_version": subject.agent_version if subject else None,
            "benchmark": dict(benchmark) if benchmark else None,
            "report": {"uri": report_uri, "hash": report_hash} if report_uri else None,
            "evaluated_at": now,
            "expires_at": now + ttl_days * 86400 if ttl_days else None,
            "evaluator": response.attestation.evaluator if response.attestation else None,
            "attestation": response.attestation.to_dict() if response.attestation else None,
        }
        return entry

    def _check_subject(self, agent_id: int, response: EvalResponse) -> None:
        s = response.subject
        if not isinstance(s, AgentSubject):
            raise ChainIntegrationError(
                "the response subject must be an AgentSubject", code="subject_mismatch"
            )
        if (s.agent_id, s.chain_id) != (
            agent_id,
            self.registry.chain_id,
        ) or s.registry.lower() != self.registry.registry_address.lower():
            raise ChainIntegrationError(
                "the response was evaluated for a different agent", code="subject_mismatch"
            )

    async def publish(
        self,
        agent_id: int,
        response: EvalResponse,
        *,
        force: bool = False,
        set_metadata_pointer: bool = True,
        rehost: str = "auto",
        benchmark: Mapping[str, Any] | None = None,
        ttl_days: int | None = 90,
    ) -> PublishResult:
        if not force and (response.stage != "registration" or response.verdict.status != "pass"):
            return PublishResult(
                success=False,
                error="not publishable: needs a passing "
                "registration-stage evaluation (or force=True)",
            )
        try:
            self._check_subject(agent_id, response)
            current = await self.registry.read_registration(agent_id)
            if current is None:
                return PublishResult(success=False, error=f"agent {agent_id} not found")
            report_uri = report_hash = None
            if self.storage is not None:
                report = response.to_dict()
                if self.encrypt_to:
                    report = encrypt_report(report, self.encrypt_to)
                report_hash = hash_obj(report)
                report_uri = await self.storage.upload(report, f"eval-report-{agent_id}.json")
            entry = self.build_entry(
                response,
                report_uri=report_uri,
                report_hash=report_hash,
                benchmark=benchmark,
                ttl_days=ttl_days,
            )
            kept = [
                e
                for e in current.get("metrics", []) or []
                if isinstance(e, Mapping) and e.get("suite_hash") != response.suite_hash
            ]
            document = {**current, "metrics": [*kept, entry]}
            current_uri = await self.registry.get_agent_uri(agent_id) or ""
            if (
                rehost == "auto"
                and self.storage is not None
                and current_uri
                and not current_uri.startswith("data:")
            ):
                new_uri = await self.storage.upload(document, f"agent-{agent_id}.json")
            else:
                new_uri = encode_data_uri(document)
            tx = await self.registry.set_agent_uri(agent_id, new_uri)
            metadata_tx = None
            if set_metadata_pointer and report_uri:
                key = METADATA_KEY_PREFIX + response.suite_hash[2:12]
                metadata_tx = await self.registry.set_metadata(
                    agent_id, key, f"{report_uri}#{report_hash}"
                )
        except ChainIntegrationError as exc:
            return PublishResult(success=False, error=f"{exc.code}: {exc}")
        return PublishResult(
            success=True,
            agent_uri=new_uri,
            tx_hash=tx,
            metadata_tx=metadata_tx,
            report_uri=report_uri,
            report_hash=report_hash,
            entry=entry,
        )

    async def read(self, agent_id: int, *, verify_reports: bool = False) -> list[dict[str, Any]]:
        """Published metric entries, each with ``stale`` / ``report_ok`` flags."""
        doc = await self.registry.read_registration(agent_id)
        if doc is None:
            return []
        now = int(self.clock())
        out = []
        for entry in doc.get("metrics", []) or []:
            if not isinstance(entry, Mapping) or entry.get("schema") != SCHEMA:
                continue
            e = dict(entry)
            e["stale"] = bool(e.get("expires_at")) and now > int(e["expires_at"])
            report = e.get("report") or {}
            if verify_reports and report.get("uri") and self.storage is not None:
                try:
                    e["report_ok"] = hash_obj(
                        await self.storage.download(report["uri"])
                    ) == report.get("hash")
                except Exception:  # noqa: BLE001 - unreachable report
                    e["report_ok"] = False
            out.append(e)
        return out
