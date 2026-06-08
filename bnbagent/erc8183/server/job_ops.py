"""ERC8183JobOps — async job lifecycle operations for ERC-8183 provider agents.

Wraps ``ERC8183Client`` (synchronous) for use from async frameworks (FastAPI etc.).
All blocking web3 calls go through ``asyncio.to_thread(...)`` so the event loop
is never blocked.

Responsibilities
----------------
- Discover pending funded jobs for this agent.
- Verify jobs (status / provider / expiry / budget / negotiation quote).
- Submit deliverables (with optional off-chain upload via ``StorageProvider``).
"""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
import re
import time
from collections.abc import Callable
from typing import Any

from web3 import Web3

from ...config import NetworkConfig
from ...core.config import get_env
from ...exceptions import RpcRangeLimitError
from ...storage.storage_provider import StorageProvider
from ...wallets.wallet_provider import WalletProvider
from ..client import ERC8183Client
from ..config import ERC8183_ENV_PREFIX
from ..schema import SCHEMA_VERSION, DeliverableManifest
from ..types import JobStatus

logger = logging.getLogger(__name__)


_DEFAULT_MAX_RESPONSE_BYTES = 5 * 1024 * 1024  # 5 MB
_DEFAULT_MAX_METADATA_BYTES = 256 * 1024       # 256 KB


def _read_int_env(key: str, default: int) -> int:
    raw = get_env(key, prefix=ERC8183_ENV_PREFIX)
    if raw is None:
        return default
    try:
        value = int(raw)
        if value <= 0:
            raise ValueError
        return value
    except ValueError:
        logger.warning(
            "[ERC8183JobOps] %s%s=%r invalid, using default %d",
            ERC8183_ENV_PREFIX, key, raw, default,
        )
        return default


def _max_response_bytes() -> int:
    return _read_int_env("MAX_RESPONSE_BYTES", _DEFAULT_MAX_RESPONSE_BYTES)


def _max_metadata_bytes() -> int:
    return _read_int_env("MAX_METADATA_BYTES", _DEFAULT_MAX_METADATA_BYTES)


_TRANSIENT_ERROR_KEYWORDS = (
    "timeout", "connection", "network", "rpc",
    "429", "too many requests", "rate limit", "limit exceeded",
)


def _exc_error_fields(exc: Exception) -> dict[str, Any]:
    """Safe ``{"error", "error_code"}`` fields for an exception.

    web3 RPC errors carry ``{'code': ..., 'message': ...}`` whose ``str()``
    is a Python dict-repr — surface the inner message instead so consumers
    embedding ``error`` never produce nested, json-rejecting blobs.
    Transport errors are replaced by a generic message and any URL-shaped
    token is redacted (RPC endpoints embed API keys in the path); other
    messages (e.g. revert reasons) pass through. ``error_code`` is
    HTTP-like: 503 for transient chain/RPC trouble, 500 otherwise. The raw
    JSON-RPC code (e.g. ``-32005``), when present, rides along separately
    as ``rpc_error_code`` — never mixed into ``error_code``.
    """
    payload = exc.args[0] if exc.args else None
    rpc_code = None
    if isinstance(payload, dict) and "message" in payload:
        message = str(payload["message"])
        if isinstance(payload.get("code"), int):
            rpc_code = payload["code"]
    else:
        message = str(exc)
    if any(k in message.lower() for k in _TRANSIENT_ERROR_KEYWORDS):
        fields = {"error": "Temporary chain/RPC error", "error_code": 503}
    else:
        message = re.sub(r"\S+://\S+", "<redacted>", message)
        fields = {"error": message, "error_code": 500}
    if rpc_code is not None:
        fields["rpc_error_code"] = rpc_code
    return fields


class ERC8183JobOps:
    """Async job-lifecycle operations for a provider agent.

    Parameters
    ----------
    wallet_provider
        Provider signing material (required).
    network
        Preset name or a ``NetworkConfig`` for custom deployments.
    storage_provider
        Optional off-chain storage for deliverable payloads.
    service_price
        Minimum acceptable budget in token raw units. Used by
        ``verify_job`` to reject under-priced jobs. Advertised decimals in
        402 responses are fetched dynamically from the payment token.
    """

    def __init__(
        self,
        wallet_provider: WalletProvider | None = None,
        network: str | NetworkConfig = "bsc-testnet",
        *,
        provider_address: str | None = None,
        storage_provider: StorageProvider | None = None,
        service_price: int = 0,
        agent_url: str | None = None,
    ) -> None:
        if wallet_provider is None and provider_address is None:
            raise ValueError(
                "ERC8183JobOps needs a wallet_provider (to sign) or a "
                "provider_address (read/poll-only)"
            )

        self._wallet_provider = wallet_provider
        self._agent_address = (
            wallet_provider.address
            if wallet_provider is not None
            else Web3.to_checksum_address(provider_address)
        )
        self._network = network
        self._storage = storage_provider
        self._service_price = service_price
        self._agent_url = agent_url

        self._client: ERC8183Client | None = None
        self._deliverable_urls: dict[int, str] = {}
        self._last_known_counter: int = 0
        self._startup_scan_done: bool = False
        self._pending_open_ids: set[int] = set()

    # -------------------------------------------------------- URL resolution

    def _public_deliverable_url(self, job_id: int, storage_url: str) -> str:
        """Return a URL that is reachable by client/voter.

        Non-file:// URLs (ipfs://, https://, etc.) are passed through unchanged.
        file:// (or empty) URLs fall back to the agent's own HTTP endpoint
        ``{agent_url}/job/{job_id}/response``.  Raises RuntimeError when the
        fallback is needed but ERC8183_AGENT_URL was not configured.
        """
        if storage_url and not storage_url.startswith("file://"):
            return storage_url
        if not self._agent_url:
            raise RuntimeError(
                "Cannot publish deliverable: storage returned a non-public URL "
                "and ERC8183_AGENT_URL is not set. "
                "Set ERC8183_AGENT_URL to the agent's public base URL including /erc8183 "
                "(e.g. http://localhost:8003/erc8183)."
            )
        return f"{self._agent_url.rstrip('/')}/job/{job_id}/response"

    # ----------------------------------------------------------- construction

    def _get_client(self) -> ERC8183Client:
        if self._client is None:
            self._client = ERC8183Client(self._wallet_provider, self._network)
        return self._client

    @property
    def agent_address(self) -> str:
        return self._agent_address

    @property
    def erc8183_client(self) -> ERC8183Client:
        return self._get_client()

    # ------------------------------------------------------------- submission

    async def submit_result(
        self,
        job_id: int,
        response_content: str,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Build a structured deliverable, upload it, and call ``submit`` on-chain.

        The on-chain ``deliverable`` (bytes32) is ``DeliverableManifest.manifest_hash()``
        — keccak256 of the canonical manifest JSON (all fields, not just content).
        The full manifest JSON is uploaded to storage and its URL is passed as
        ``optParams`` so verifiers can fetch, re-hash, and confirm integrity.
        """
        if self._wallet_provider is None:
            raise ValueError("submit_result requires a signing wallet_provider")
        try:
            verification = await self.verify_job(job_id)
            if not verification.get("valid"):
                return {
                    "success": False,
                    "error": f"Job verification failed: {verification.get('error', 'unknown')}",
                    "error_code": verification.get("error_code"),
                }

            max_resp = _max_response_bytes()
            actual_resp = len(response_content.encode("utf-8"))
            if actual_resp > max_resp:
                return {
                    "success": False,
                    "error": (
                        f"response_content size {actual_resp} bytes exceeds "
                        f"limit {max_resp} bytes"
                    ),
                    "error_code": 413,
                }

            if metadata is not None:
                max_meta = _max_metadata_bytes()
                actual_meta = len(
                    json.dumps(metadata, separators=(",", ":")).encode("utf-8")
                )
                if actual_meta > max_meta:
                    return {
                        "success": False,
                        "error": (
                            f"metadata size {actual_meta} bytes exceeds "
                            f"limit {max_meta} bytes"
                        ),
                        "error_code": 413,
                    }

            erc8183 = self._get_client()

            chain_id = await asyncio.to_thread(lambda: erc8183.commerce.w3.eth.chain_id)
            manifest = DeliverableManifest(
                version=SCHEMA_VERSION,
                job_id=job_id,
                chain_id=chain_id,
                contracts={
                    "commerce": erc8183.commerce.address,
                    "router": erc8183.router.address,
                    "policy": erc8183.policy.address,
                },
                response={
                    "content": response_content,
                    "content_type": "text/plain",
                },
                metadata=metadata or {},
            )
            data = manifest.to_dict()
            deliverable = manifest.manifest_hash()

            storage_url = ""
            if self._storage:
                storage_url = await self._storage.upload(data, f"erc8183-job-{job_id}.json")
                logger.info(f"[ERC8183JobOps] Deliverable uploaded: {storage_url}")
                self._deliverable_urls[job_id] = storage_url

            public_url = self._public_deliverable_url(job_id, storage_url)
            result = await asyncio.to_thread(
                erc8183.submit, job_id, deliverable, {"deliverable_url": public_url}
            )
            logger.info(f"[ERC8183JobOps] submit({job_id}) tx: {result['transactionHash']}")
            return {
                "success": True,
                "txHash": result["transactionHash"],
                "deliverableUrl": public_url,
                "deliverable": Web3.to_hex(deliverable),
            }
        except Exception as exc:
            logger.error(f"[ERC8183JobOps] submit({job_id}) failed: {exc}")
            return {"success": False, **_exc_error_fields(exc)}

    # ------------------------------------------------------------------ reads

    async def get_job(self, job_id: int) -> dict[str, Any]:
        try:
            job = await asyncio.to_thread(self._get_client().get_job, job_id)
            return {
                "success": True,
                "jobId": job.id,
                "client": job.client,
                "provider": job.provider,
                "evaluator": job.evaluator,
                "description": job.description,
                "budget": job.budget,
                "expiredAt": job.expired_at,
                "submittedAt": job.submitted_at,
                "status": job.status,
                "hook": job.hook,
                "deliverable": Web3.to_hex(job.deliverable),
            }
        except Exception as exc:
            logger.error(f"[ERC8183JobOps] get_job({job_id}) failed: {exc}")
            # Return a generic message — the raw exception can embed the RPC
            # URL (and its API key) on transport errors. Classify here so
            # callers still get the right status without parsing the message.
            is_net = any(k in str(exc).lower() for k in ("timeout", "connection", "network", "rpc"))
            return {
                "success": False,
                "error": "Temporary chain/RPC error" if is_net else "Failed to fetch job from chain",
                "error_code": 503 if is_net else 500,
            }

    async def get_job_status(self, job_id: int) -> dict[str, Any]:
        result = await self.get_job(job_id)
        if not result.get("success"):
            return result
        return {"success": True, "status": result["status"]}

    async def get_response(self, job_id: int) -> dict[str, Any]:
        """Retrieve stored deliverable (cache -> local file -> on-chain URL)."""
        if not self._storage:
            return {"success": False, "error": "No storage configured"}

        url = self._deliverable_urls.get(job_id)
        if url:
            try:
                data = await self._storage.download(url)
                return {"success": True, **data}
            except Exception as exc:
                logger.warning(f"[ERC8183JobOps] get_response({job_id}) download failed: {exc}")

        if hasattr(self._storage, "_base"):
            try:
                filepath = self._storage._base / f"erc8183-job-{job_id}.json"
                if filepath.exists():
                    data = json.loads(filepath.read_text(encoding="utf-8"))
                    return {"success": True, **data}
            except Exception as exc:
                logger.warning(f"[ERC8183JobOps] get_response({job_id}) file read failed: {exc}")

        try:
            erc8183 = self._get_client()
            deliverable_url = await asyncio.to_thread(
                erc8183.get_deliverable_url, job_id
            )
            if deliverable_url:
                self._deliverable_urls[job_id] = deliverable_url
                data = await self._storage.download(deliverable_url)
                return {"success": True, **data}
        except RpcRangeLimitError as exc:
            logger.warning(f"[ERC8183JobOps] get_response({job_id}) rate-limited: {exc}")
            return {
                "success": False,
                "error": (
                    f"Deliverable for job {job_id} temporarily unresolvable "
                    "(RPC rate limit); retry"
                ),
                "error_code": 503,
            }
        except Exception as exc:
            logger.warning(f"[ERC8183JobOps] get_response({job_id}) on-chain fallback failed: {exc}")

        # A job that has been submitted on-chain MUST have a JobInitialised
        # event, so failing to resolve its URL above (rate-limited RPC,
        # submit older than the fallback scan window, storage hiccup) is a
        # resolution failure — retryable, not proof of absence. Only a job
        # that never reached SUBMITTED genuinely has no response.
        status_result = await self.get_job_status(job_id)
        if not status_result.get("success") or status_result.get("status") in (
            JobStatus.SUBMITTED,
            JobStatus.COMPLETED,
        ):
            return {
                "success": False,
                "error": f"Deliverable for job {job_id} temporarily unresolvable; retry",
                "error_code": 503,
            }
        return {
            "success": False,
            "error": f"Response not found for job {job_id}",
            "error_code": 404,
        }

    # ---------------------------------------------------- verification helper

    async def verify_job(self, job_id: int) -> dict[str, Any]:
        """Check job can be worked by this agent. Returns ``{valid, error, job, warnings}``."""
        try:
            job_result = await self.get_job(job_id)
            if not job_result.get("success"):
                # get_job already returns a sanitized message + error_code.
                return {
                    "valid": False,
                    "error": job_result.get("error", "Failed to fetch job from chain"),
                    "error_code": job_result.get("error_code", 500),
                }

            me = self.agent_address.lower()

            status = job_result.get("status")
            if status != JobStatus.FUNDED:
                status_name = status.name if hasattr(status, "name") else str(status)
                return {
                    "valid": False,
                    "error": f"Job status is {status_name}, expected FUNDED",
                    "error_code": 409,
                }

            if str(job_result.get("provider", "")).lower() != me:
                return {
                    "valid": False,
                    "error": "This agent is not the provider for this job",
                    "error_code": 403,
                }

            now = int(time.time())
            expired_at = job_result.get("expiredAt", 0)
            if expired_at <= now:
                return {"valid": False, "error": "Job has expired", "error_code": 408}

            # OptimisticPolicy reverts ``commerce.submit`` with ``SubmissionTooLate``
            # once ``now > expiredAt - disputeWindow``. Detect that here so the
            # agent doesn't keep retrying every funded-poll tick on a job whose
            # submit deadline has already passed.
            try:
                dispute_window = await asyncio.to_thread(
                    self._get_client().policy.dispute_window
                )
                submit_deadline = expired_at - int(dispute_window)
                if now > submit_deadline:
                    return {
                        "valid": False,
                        "error": (
                            "Submission deadline has passed "
                            f"(expiredAt - disputeWindow = {submit_deadline}, now = {now})"
                        ),
                        "error_code": 410,
                    }
            except Exception as exc:
                logger.warning(
                    f"[ERC8183JobOps] dispute_window lookup failed; proceeding without "
                    f"submit-deadline check: {exc}"
                )

            description = job_result.get("description", "")
            if description:
                from ..negotiation import parse_job_description

                try:
                    parsed = parse_job_description(description)
                except Exception as exc:
                    return {
                        "valid": False,
                        "error": f"Malformed job description: {exc}",
                        "error_code": 410,
                    }
                if parsed and parsed.quote_expires_at is not None:
                    if now > parsed.quote_expires_at:
                        return {
                            "valid": False,
                            "error": "Negotiation quote has expired",
                            "error_code": 410,
                        }

            if self._service_price > 0:
                budget = job_result.get("budget", 0)
                if budget < self._service_price:
                    decimals = await asyncio.to_thread(self._get_client().token_decimals)
                    return {
                        "valid": False,
                        "error": (
                            f"Job budget ({budget}) is below agent's"
                            f" service price ({self._service_price})"
                        ),
                        "error_code": 402,
                        "service_price": str(self._service_price),
                        "decimals": decimals,
                    }

            warnings = []
            evaluator = str(job_result.get("evaluator", "")).lower()
            client = str(job_result.get("client", "")).lower()
            if evaluator == client:
                warnings.append(
                    {
                        "code": "CLIENT_AS_EVALUATOR",
                        "message": (
                            "Evaluator equals client — client can self-reject"
                            " and refund after you submit."
                        ),
                    }
                )

            return {
                "valid": True,
                "job": job_result,
                "warnings": warnings if warnings else None,
            }
        except Exception as exc:
            logger.error(f"[ERC8183JobOps] verify_job({job_id}) failed: {exc}")
            is_net = any(k in str(exc).lower() for k in ("timeout", "connection", "network", "rpc"))
            return {
                "valid": False,
                "error": "Temporary chain/RPC error" if is_net else "Failed to verify job",
                "error_code": 503 if is_net else 500,
            }

    # ----------------------------------------------------- pending-job scanner

    async def _multicall_scan(self, job_ids: list[int]) -> dict[str, Any]:
        if not job_ids:
            return {"success": True, "jobs": []}

        erc8183 = self._get_client()
        me = self.agent_address.lower()

        jobs = await asyncio.to_thread(erc8183.commerce.get_jobs_batch, list(job_ids))

        now = int(time.time())
        pending: list[dict[str, Any]] = []
        for job in jobs:
            if job is None:
                continue
            if job.provider.lower() != me:
                logger.debug(
                    f"[ERC8183JobOps] job #{job.id} skipped: provider={job.provider} != agent={me}"
                )
                self._pending_open_ids.discard(job.id)
                continue
            if job.status == JobStatus.FUNDED and job.expired_at > now:
                pending.append(
                    {
                        "success": True,
                        "jobId": job.id,
                        "client": job.client,
                        "provider": job.provider,
                        "evaluator": job.evaluator,
                        "description": job.description,
                        "budget": job.budget,
                        "expiredAt": job.expired_at,
                        "status": job.status,
                        "hook": job.hook,
                        "deliverable": Web3.to_hex(job.deliverable),
                    }
                )
                self._pending_open_ids.discard(job.id)
            elif job.status == JobStatus.OPEN:
                self._pending_open_ids.add(job.id)
            else:
                self._pending_open_ids.discard(job.id)

        return {"success": True, "jobs": pending}

    async def _startup_scan(self) -> dict[str, Any]:
        erc8183 = self._get_client()
        try:
            counter = await asyncio.to_thread(erc8183.commerce.job_counter)
        except Exception as exc:
            logger.warning(f"[ERC8183JobOps] startup scan counter failed: {exc}")
            self._startup_scan_done = True
            return {"success": False, **_exc_error_fields(exc), "jobs": []}

        if counter == 0:
            self._startup_scan_done = True
            return {"success": True, "jobs": []}

        result = await self._multicall_scan(list(range(1, counter + 1)))
        self._last_known_counter = counter
        self._startup_scan_done = True
        logger.info(
            f"[ERC8183JobOps] Startup scan: {len(result['jobs'])} pending of {counter} total"
            f" (agent={self.agent_address})"
        )
        return result

    async def get_pending_jobs(self) -> dict[str, Any]:
        """Return funded, non-expired jobs assigned to this provider."""
        try:
            if not self._startup_scan_done:
                return await self._startup_scan()

            erc8183 = self._get_client()
            counter = await asyncio.to_thread(erc8183.commerce.job_counter)
            scan_set: set[int] = set()
            if counter > self._last_known_counter:
                scan_set.update(range(self._last_known_counter + 1, counter + 1))
            scan_set.update(self._pending_open_ids)
            if not scan_set:
                return {"success": True, "jobs": []}

            result = await self._multicall_scan(sorted(scan_set))
            self._last_known_counter = counter
            return result
        except Exception as exc:
            logger.error(f"[ERC8183JobOps] get_pending_jobs failed: {exc}")
            return {"success": False, **_exc_error_fields(exc), "jobs": []}

    async def get_submitted_jobs(self) -> dict[str, Any]:
        """Return SUBMITTED jobs assigned to this provider (opt-in auto-settle).

        Unlike :meth:`get_pending_jobs` (FUNDED, incremental cursor), this does a
        full scan each call — SUBMITTED jobs sit awaiting the dispute window, so
        there is no monotonic cursor. Each dict includes ``submittedAt`` so the
        caller can check the window exactly instead of approximating it with
        ``expiredAt``.
        """
        try:
            erc8183 = self._get_client()
            me = self._agent_address.lower()
            counter = await asyncio.to_thread(erc8183.commerce.job_counter)
            if counter == 0:
                return {"success": True, "jobs": []}
            jobs = await asyncio.to_thread(
                erc8183.commerce.get_jobs_batch, list(range(1, counter + 1))
            )
            submitted = [
                {
                    "jobId": job.id,
                    "client": job.client,
                    "provider": job.provider,
                    "evaluator": job.evaluator,
                    "description": job.description,
                    "budget": job.budget,
                    "expiredAt": job.expired_at,
                    "submittedAt": job.submitted_at,
                    "status": job.status,
                    "hook": job.hook,
                    "deliverable": Web3.to_hex(job.deliverable),
                }
                for job in jobs
                if job is not None
                and job.provider.lower() == me
                and job.status == JobStatus.SUBMITTED
            ]
            return {"success": True, "jobs": submitted}
        except Exception as exc:
            logger.error(f"[ERC8183JobOps] get_submitted_jobs failed: {exc}")
            return {"success": False, **_exc_error_fields(exc), "jobs": []}


async def funded_job_watcher(
    job_ops: ERC8183JobOps,
    on_funded: Callable[[dict[str, Any]], Any],
    *,
    interval: float = 30.0,
    stop: asyncio.Event | None = None,
) -> None:
    """Poll ``job_ops.get_pending_jobs()`` and fire ``on_funded(job)`` per FUNDED job.

    Signer-free detection loop for keyless services: it NEVER submits or settles
    — the caller decides what to do (e.g. delegate signing to a separate Agent).
    ``on_funded`` may be sync or async.

    Retry contract: a job fires once on success. ``on_funded`` raising, or
    returning ``False`` / ``{"retry": True}``, marks the job for retry on the
    next tick (after re-checking on-chain that it is still FUNDED and
    unexpired — ``get_pending_jobs`` reports each job only once, so retries
    are re-validated via ``get_job``). Retries stop naturally when the job
    leaves FUNDED or expires. Any other return value (incl. ``None``) keeps
    fire-once behavior. Pass an ``asyncio.Event`` as ``stop`` to end the
    loop; otherwise it runs until cancelled.
    """
    seen: set[int] = set()
    retry: set[int] = set()
    is_async = inspect.iscoroutinefunction(on_funded)

    async def _fire(job: dict[str, Any]) -> None:
        job_id = job["jobId"]
        try:
            if is_async:
                result = await on_funded(job)
            else:
                result = await asyncio.to_thread(on_funded, job)
        except Exception as exc:
            logger.error(
                "[funded_job_watcher] on_funded(%s) failed; will retry: %s",
                job_id, exc,
            )
            retry.add(job_id)
            return
        if result is False or (isinstance(result, dict) and result.get("retry")):
            retry.add(job_id)
        else:
            retry.discard(job_id)
            seen.add(job_id)

    while True:
        try:
            # Re-validate + re-fire previously failed jobs first, so a job
            # failing below is not retried within the same tick.
            for job_id in list(retry):
                fresh = await job_ops.get_job(job_id)
                if not fresh.get("success"):
                    continue  # transient read error — keep for next tick
                if (
                    fresh.get("status") != JobStatus.FUNDED
                    or fresh.get("expiredAt", 0) <= int(time.time())
                ):
                    retry.discard(job_id)  # job moved on — stop retrying
                    continue
                await _fire(fresh)

            result = await job_ops.get_pending_jobs()
            if result.get("success"):
                for job in result.get("jobs", []):
                    if job["jobId"] in seen or job["jobId"] in retry:
                        continue
                    await _fire(job)
            else:
                logger.warning(
                    "[funded_job_watcher] poll error: %s", result.get("error")
                )
        except Exception as exc:
            logger.error("[funded_job_watcher] iteration failed: %s", exc)

        if stop is not None:
            try:
                await asyncio.wait_for(stop.wait(), timeout=interval)
                return
            except asyncio.TimeoutError:
                continue
        else:
            await asyncio.sleep(interval)

