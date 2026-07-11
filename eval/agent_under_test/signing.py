"""Deterministic signing — the Agent is the SOLE key-holder/signer.

Every on-chain WRITE the Agent performs lives here as FIXED code:

    sign_quote(...)    EIP-191 sign the (clamped) negotiated offer
    submit_result(...) build manifest → upload → on-chain ``submit``
    settle(...)        claim payment after the dispute window

These functions are NEVER registered as LLM-callable tools (``tools.py`` holds
only read-only tools). The price is a FIXED list price from studio.toml
(``list_price()``, clamped by ``main.py`` BEFORE it reaches here) — the LLM only
produces the work text and never moves money or sets a price.

The key is loaded by ``bnbagent_studio_core.wallet.get_wallet()`` (local keystore,
unlocked by ``WALLET_PASSWORD``). It is injected into the AgentCore runtime via
the secret store, never bundled into the code package.

You own this file — edit the pricing clamp source / manifest shape if your
domain needs it, but keep these ops OUT of the LLM tool list.
"""
from __future__ import annotations

from bnbagent.erc8183 import NegotiationHandler

from bnbagent_studio_core import config
from bnbagent_studio_core.erc8183 import submit_workflow
from bnbagent_studio_core.erc8183.client import get_8183_client
from bnbagent_studio_core.erc8183.workflows import settle_workflow
from bnbagent_studio_core.wallet import get_wallet


def _erc8183_cfg() -> dict:
    """Read ``[payments.erc8183]`` from studio.toml ({} when absent)."""
    try:
        cfg = config.load_studio_toml()
    except FileNotFoundError:
        cfg = {}
    return cfg.get("payments", {}).get("erc8183", {}) or {}


def price_bounds() -> tuple[int, int]:
    """Return ``(min_price, max_price)`` in raw wei from studio.toml.

    These are the clamp bounds ``main.py`` applies to the LLM-proposed price
    BEFORE signing — a hallucinated / manipulated quote can never sign an
    out-of-bounds number. ``min_price``/``max_price`` are raw uint256 strings
    in ``[payments.erc8183]``.
    """
    cfg = _erc8183_cfg()
    # TODO: if min/max are absent the bounds default to (0, +inf) — i.e. NO
    # clamp. Set [payments.erc8183].min_price / max_price in studio.toml to
    # enforce a real floor/ceiling (strongly recommended for production).
    # The scaffold ships max_price = "" (an empty string, not absent), so treat
    # empty/whitespace the same as missing → fall back to the default bound.
    def _raw(key: str, default: int) -> int:
        s = str(cfg.get(key, "")).strip()
        return int(s) if s else default

    min_price = _raw("min_price", 0)
    max_price = _raw("max_price", 2**256 - 1)
    return min_price, max_price


def list_price() -> int:
    """Return the seller's list price in raw wei from studio.toml.

    Reads ``[payments.erc8183].price`` — the deterministic asking price every
    quote uses (rule-based pricing; no LLM in the quote path). Empty/absent → 0.
    Edit ``price`` in studio.toml to change what you charge. The value is still
    clamped to ``[min_price, max_price]`` by :func:`clamp_price` before signing.
    """
    s = str(_erc8183_cfg().get("price", "")).strip()
    return int(s) if s else 0


def clamp_price(proposed_wei: int) -> int:
    """Clamp a proposed price into ``[min_price, max_price]``."""
    lo, hi = price_bounds()
    return max(lo, min(proposed_wei, hi))


_handler: NegotiationHandler | None = None


def _get_handler() -> NegotiationHandler:
    """Return the process-wide :class:`NegotiationHandler` (lazy, cached).

    The handler's chain_id + verifying_contract come from
    :func:`get_8183_client` and are stable per process, so we build it once.
    The per-request clamped price is passed via ``negotiate(..., price=...)``
    (see :func:`sign_quote`), so the construction-time ``service_price`` is a
    placeholder that is always overridden.
    """
    global _handler
    if _handler is None:
        cfg = _erc8183_cfg()
        currency = cfg.get("currency", "")  # the Agent owns the currency now
        ttl = int(cfg.get("quote_ttl_seconds", 300))
        est = int(cfg.get("default_estimated_completion_seconds", 600))
        # chain_id + verifying_contract bind provider_sig to this chain/contract
        # (prevents cross-chain replay). Read off the live ERC-8183 client.
        client = get_8183_client()
        _handler = NegotiationHandler(
            service_price="0",  # placeholder — overridden per call via price=
            currency=currency,
            estimated_completion_seconds=est,
            wallet_provider=get_wallet(),
            quote_ttl_seconds=ttl,
            chain_id=client.network.chain_id,
            verifying_contract=client.commerce.address,
        )
    return _handler


def sign_quote(request: dict, clamped_price_wei: int) -> dict:
    """Negotiate + EIP-191-sign a quote at ``clamped_price_wei``; return the SDK envelope.

    Reuses a process-wide :class:`NegotiationHandler` (cached — its chain_id +
    verifying_contract are stable per process) and overrides the price for this
    request via ``negotiate(..., price=str(clamped_price_wei))``.

    Returns the SDK's ``NegotiationResult.to_dict()`` envelope **verbatim** — the
    exact wire structure a buyer parses and feeds to ``build_job_description`` to
    anchor on-chain (see docs/design/erc8183-sdk-reference.md §2). On accept it
    carries ``response.terms.price``/``currency``, ``quote_expires_at``,
    ``negotiation_hash``, ``response_hash``, ``provider_sig``, ``chain_id``,
    ``verifying_contract``; on reject it carries ``response.reason_code`` /
    ``reason`` (empty hash + sig). We do NOT invent a custom shape.
    """
    cfg = _erc8183_cfg()
    est = int(cfg.get("default_estimated_completion_seconds", 600))

    handler = _get_handler()
    result = handler.negotiate(
        request, price=str(clamped_price_wei), estimated_completion_seconds=est
    )

    # NegotiationHandler signs non-fatally: if sign_message failed it returns an
    # accepted result WITHOUT provider_sig. Never relay an unsigned "accepted".
    if result.accepted and (not result.negotiation_hash or not result.provider_sig):
        raise RuntimeError(
            "quote accepted but provider_sig is missing (wallet sign failed); "
            "refusing to relay an unsigned offer"
        )

    return result.to_dict()


def verify_signed_job(job_id: int) -> tuple[bool, str, bool]:
    """Verify funded ``job_id`` carries the quote THIS agent signed.

    Thin wrapper over :func:`bnbagent_studio_core.erc8183.verify.verify_signed_job` with
    ``expected_signer`` = our own wallet address. Returns ``(ok, reason,
    permanent)``: ``ok`` → safe to work; otherwise ``permanent`` distinguishes a
    job to skip-forever (record + tell the client) from a transient retry.
    """
    from bnbagent_studio_core.erc8183.verify import verify_signed_job as _verify

    v = _verify(job_id, expected_signer=get_wallet().address)
    return v.ok, v.reason, v.permanent


def job_spec(job_id: int):
    """Return the on-chain :class:`JobDescription` for ``job_id`` (``None`` if unstructured).

    The task + terms the buyer ANCHORED ON-CHAIN — and that this agent's
    ``provider_sig`` covers — are the authoritative work spec. ``handle_fulfill``
    reads the task from HERE, not from Service-relayed context (the keyless
    Service forwards none), so the Agent delivers exactly the deal it signed.
    Returns ``None`` for legacy/plain-text descriptions (caller falls back).
    """
    from bnbagent.erc8183.schema import JobDescription

    job = get_8183_client().get_job(job_id)
    return JobDescription.from_str(job.description)


def submit_result(job_id: int, response_content: str, metadata: dict | None = None):
    """Sign + broadcast the on-chain ``submit`` for ``job_id``.

    Delegates to :func:`bnbagent_studio_core.erc8183.submit_workflow`, which re-verifies
    the job is genuinely FUNDED + assigned to us (via the SDK's
    ``ERC8183JobOps.verify_job``), builds the ``DeliverableManifest``, uploads
    it to storage, and calls on-chain ``submit`` — all ``audited_op``-wrapped.
    Returns the :class:`SubmitResult` (``.submit_tx`` + ``.deliverable_url``); the
    Agent relays ``deliverable_url`` to the keyless Service so it serves the
    CANONICAL manifest from storage without an on-chain log scan.
    """
    return submit_workflow(job_id, response_content, metadata=metadata)


def settle(job_id: int) -> str:
    """Sign + broadcast ``settle`` (claim payment) for ``job_id``.

    Delegates to :func:`bnbagent_studio_core.erc8183.workflows.settle_workflow` with the
    default ``approve`` action → SDK ``router.settle(job_id)``, ``audited_op``-
    wrapped. Returns the settle tx hash.
    """
    return settle_workflow(job_id, action="approve")
