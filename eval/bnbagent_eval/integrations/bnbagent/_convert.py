"""SDK job objects and dicts → neutral value types."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ...ports import JobStatus, JobView

STATUS_NAMES: tuple[JobStatus, ...] = (
    "open",
    "funded",
    "submitted",
    "completed",
    "rejected",
    "expired",
)
ZERO_ADDRESS = "0x" + "00" * 20
ZERO_HASH = "0x" + "00" * 32

# Our field → accepted SDK spellings (Job dataclass attrs, job-ops camelCase dict keys).
_FIELDS: dict[str, tuple[str, ...]] = {
    "job_id": ("id", "jobId", "job_id"),
    "client": ("client",),
    "provider": ("provider",),
    "evaluator": ("evaluator",),
    "description": ("description",),
    "budget": ("budget",),
    "expired_at": ("expired_at", "expiredAt"),
    "submitted_at": ("submitted_at", "submittedAt"),
    "status": ("status",),
    "deliverable": ("deliverable",),
}


def status_name(status: Any) -> JobStatus:
    """``JobStatus`` IntEnum, int, or name → our literal."""
    if isinstance(status, str) and not status.isdigit():
        name = status.rsplit(".", 1)[-1].lower()
        if name in STATUS_NAMES:
            return name  # type: ignore[return-value]
        raise ValueError(f"unknown job status {status!r}")
    index = int(status)
    if not 0 <= index < len(STATUS_NAMES):
        raise ValueError(f"unknown job status {status!r}")
    return STATUS_NAMES[index]


def _get(raw: Any, field: str) -> Any:
    for name in _FIELDS[field]:
        if isinstance(raw, Mapping):
            if name in raw:
                return raw[name]
        elif hasattr(raw, name):
            return getattr(raw, name)
    return None


def _hash_or_none(value: Any) -> str | None:
    if value is None:
        return None
    text = "0x" + bytes(value).hex() if isinstance(value, (bytes, bytearray)) else str(value)
    text = text.lower() if text[:2].lower() == "0x" else "0x" + text.lower()
    return None if int(text, 16) == 0 else text


def _int_or_none(value: Any) -> int | None:
    return None if value in (None, 0, "") else int(value)


def job_view(raw: Any) -> JobView:
    """A ``bnbagent.erc8183.types.Job`` or a job-ops / watcher dict (camelCase) → ``JobView``."""
    evaluator = _get(raw, "evaluator")
    budget = _get(raw, "budget")
    return JobView(
        job_id=int(_get(raw, "job_id")),
        client=str(_get(raw, "client")),
        provider=str(_get(raw, "provider")),
        status=status_name(_get(raw, "status")),
        deliverable=_hash_or_none(_get(raw, "deliverable")),
        submitted_at=_int_or_none(_get(raw, "submitted_at")),
        expired_at=_int_or_none(_get(raw, "expired_at")),
        description=_get(raw, "description"),
        budget=None if budget is None else int(budget),
        evaluator=None if evaluator is None else str(evaluator),
    )


def plain_job_dict(raw: Mapping[str, Any]) -> dict[str, Any]:
    """A watcher job dict with SDK types replaced by plain ones (status → our literal)."""
    out = dict(raw)
    if "status" in out:
        out["status"] = status_name(out["status"])
    if isinstance(out.get("deliverable"), (bytes, bytearray)):
        out["deliverable"] = "0x" + bytes(out["deliverable"]).hex()
    return out
