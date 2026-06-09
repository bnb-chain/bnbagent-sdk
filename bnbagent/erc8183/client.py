"""``ERC8183Client`` — single-entry facade over the ERC-8183 contract stack.

ERC-8183 is a three-layer protocol:

- ``AgenticCommerceUpgradeable`` — ERC-8183 kernel (escrow).
- ``EvaluatorRouterUpgradeable`` — routing layer acting as ``job.evaluator``
  and ``job.hook`` for every routed job.
- ``OptimisticPolicy``           — UMA-style silence-approves policy with
  a whitelisted-voter reject quorum.

``ERC8183Client`` composes three thin sub-clients (``commerce`` / ``router`` /
``policy``) and a minimal ERC-20 helper. Most callers only use the top-level
methods; advanced users can reach the sub-clients via attributes.

Design notes
------------
- Synchronous. Async callers wrap via ``asyncio.to_thread(...)``.
- Signing is wallet-provider only — raw private keys never cross this API.
- Network configuration goes through a single ``network`` argument that
  accepts either a preset name (``"bsc-testnet"``) or a ``NetworkConfig``
  object for custom deployments (local forks, private RPCs, etc.).
- Payment token address is NOT a configuration input — it is immutable
  on the kernel and fetched lazily via ``commerce.paymentToken()``.
- ``fund`` uses a **floor-based** approval strategy (see
  ``fund`` docstring). Default floor is ``100 * 10**decimals``, which
  assumes a stablecoin payment token.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from ..config import NetworkConfig, resolve_network
from ..core.abi_loader import create_web3
from ..wallets.wallet_provider import WalletProvider
from ..erc20.client import MinimalERC20Client
from .commerce import CommerceClient
from .policy import PolicyClient
from .router import RouterClient
from .types import ZERO_ADDRESS, ZERO_REASON, Job, JobStatus, Verdict

logger = logging.getLogger(__name__)


# Default floor for auto-approval in ``fund``, expressed in whole token units.
# Multiplied by ``10 ** token_decimals`` at call time.
# Assumes a stablecoin payment token; non-stable deployments should pass
# ``approve_floor=0`` (exact) or a custom floor.
DEFAULT_APPROVE_FLOOR_UNITS: int = 100


class ERC8183Client:
    """High-level facade over Commerce + Router + Policy.

    Parameters
    ----------
    wallet_provider:
        ``WalletProvider`` that performs all signing. Raw private keys never
        cross this boundary — wrap them in ``EVMWalletProvider`` first. Pass
        ``None`` for a read-only client (reads work; writes raise).
    network:
        Either a preset name (``"bsc-testnet"`` / ``"bsc-mainnet"``) or a
        ``NetworkConfig`` instance. Use a ``NetworkConfig`` (e.g. via
        ``dataclasses.replace(resolve_network("bsc-testnet"), rpc_url=...)``)
        to override RPC or contract addresses for custom deployments.
    debug:
        Enables extra debug logging.
    """

    def __init__(
        self,
        wallet_provider: WalletProvider | None = None,
        network: str | NetworkConfig = "bsc-testnet",
        *,
        debug: bool = False,
    ) -> None:
        # wallet_provider is optional: a read-only client (None) serves all
        # reads; write operations raise via _send_tx ("client is read-only").
        nc = resolve_network(network)
        for field_name in ("commerce_contract", "router_contract", "policy_contract"):
            if not getattr(nc, field_name):
                raise ValueError(
                    f"network '{nc.name}' is missing {field_name}; "
                    "pass a NetworkConfig with all three ERC-8183 addresses set."
                )

        self.debug = debug
        self.network = nc
        self.w3 = create_web3(nc.rpc_url)

        # Defense-in-depth: refuse to operate when the RPC serves a different
        # chain than the NetworkConfig claims. Prevents wrong-chain signing
        # when RPC_URL is misconfigured or maliciously redirected.
        actual_chain_id = self.w3.eth.chain_id
        if actual_chain_id != nc.chain_id:
            raise ValueError(
                f"RPC chain_id mismatch for network '{nc.name}': "
                f"expected {nc.chain_id}, got {actual_chain_id}. "
                f"The RPC at {nc.rpc_url} is serving a different chain."
            )

        self._wallet_provider = wallet_provider
        self.address: str | None = (
            wallet_provider.address if wallet_provider is not None else None
        )

        self.commerce = CommerceClient(self.w3, nc.commerce_contract, wallet_provider)
        self.router = RouterClient(self.w3, nc.router_contract, wallet_provider)
        self.policy = PolicyClient(self.w3, nc.policy_contract, wallet_provider)

        # Cached payment-token state (populated lazily).
        self._payment_token_address: str | None = None
        self._payment_token_decimals: int | None = None
        self._payment_token_symbol: str | None = None
        self._erc20: MinimalERC20Client | None = None

    # ------------------------------------------------------------ token cache

    @property
    def payment_token(self) -> str:
        """Payment token address (cached). Fetched from ``commerce.paymentToken``."""
        if self._payment_token_address is None:
            self._payment_token_address = self.commerce.payment_token()
        return self._payment_token_address

    def _erc20_client(self) -> MinimalERC20Client:
        if self._erc20 is None:
            self._erc20 = MinimalERC20Client(
                self.w3, self.payment_token, self._wallet_provider
            )
        return self._erc20

    def token_decimals(self) -> int:
        if self._payment_token_decimals is None:
            self._payment_token_decimals = self._erc20_client().decimals()
        return self._payment_token_decimals

    def token_symbol(self) -> str:
        if self._payment_token_symbol is None:
            self._payment_token_symbol = self._erc20_client().symbol()
        return self._payment_token_symbol

    def token_balance(self, address: str | None = None) -> int:
        return self._erc20_client().balance_of(address or self.address)

    def token_allowance(self, owner: str, spender: str) -> int:
        return self._erc20_client().allowance(owner, spender)

    def approve_payment_token(self, spender: str, amount: int) -> dict[str, Any]:
        """Send ``approve(spender, amount)`` on the payment token."""
        return self._erc20_client().approve(spender, amount)

    # ----------------------------------------------------------------- writes

    def create_job(
        self,
        *,
        provider: str = ZERO_ADDRESS,
        expired_at: int,
        description: str = "",
        hook: str | None = None,
        skip_expiry_check: bool = False,
    ) -> dict[str, Any]:
        """Create a job with the Router set as evaluator + hook.

        Parameters mirror ``AgenticCommerceUpgradeable.createJob`` except
        ``evaluator`` / ``hook`` default to the Router address (the
        v1 deployment pattern).

        Pre-flights ``expired_at`` against the bound policy's
        ``disputeWindow`` to catch the foot-gun where the SDK lets you
        fund a job that ``submit()`` will always revert with
        ``SubmissionTooLate()`` — see
        https://github.com/bnb-chain/bnbagent-sdk/issues/41 for details.

        Pass ``skip_expiry_check=True`` to bypass the validation (e.g. for
        tests that intentionally exercise the revert path).
        """
        if not skip_expiry_check:
            try:
                import time
                dispute_window = int(self.policy.dispute_window())
                now = int(time.time())
                if expired_at - now <= dispute_window:
                    raise ValueError(
                        f"expired_at ({expired_at}) is too close to now ({now}). "
                        f"OptimisticPolicy on this network has dispute_window="
                        f"{dispute_window}s ({dispute_window/86400:.1f}d), so the "
                        f"submit deadline (expired_at - dispute_window = "
                        f"{expired_at - dispute_window}) is already in the past or "
                        f"within seconds. provider.submit() would revert with "
                        f"SubmissionTooLate(). Set expired_at >= now + "
                        f"dispute_window + a buffer (e.g. now + "
                        f"{dispute_window + 86400}). Pass skip_expiry_check=True "
                        f"to bypass this guard."
                    )
            except ValueError:
                raise
            except Exception as exc:
                # Don't block job creation if dispute_window can't be read
                # (custom policies, RPC hiccup, etc.) — just warn.
                logger.warning(
                    "[ERC8183Client] dispute_window pre-flight failed; "
                    "create_job proceeding without expiry check: %s",
                    exc,
                )

        return self.commerce.create_job(
            provider=provider,
            evaluator=self.router.address,
            expired_at=expired_at,
            description=description,
            hook=hook if hook is not None else self.router.address,
        )

    def register_job(self, job_id: int, policy: str | None = None) -> dict[str, Any]:
        """Bind the configured policy (or an override) to a job on the Router."""
        return self.router.register_job(job_id, policy or self.policy.address)

    def set_provider(self, job_id: int, provider: str) -> dict[str, Any]:
        return self.commerce.set_provider(job_id, provider)

    def set_budget(self, job_id: int, amount: int) -> dict[str, Any]:
        return self.commerce.set_budget(job_id, amount)

    def fund(
        self,
        job_id: int,
        amount: int,
        *,
        approve_floor: int | None = None,
    ) -> dict[str, Any]:
        """Fund a job, topping up the payment-token allowance if needed.

        Approval strategy (gas-aware, security-first):

        1. If ``allowance(client, commerce) >= amount`` → call ``fund`` only.
        2. Otherwise approve ``max(amount, floor)`` where ``floor`` is:
             - ``approve_floor`` if provided (``0`` = exact ``amount``).
             - Else ``DEFAULT_APPROVE_FLOOR_UNITS * 10**decimals`` (≈100 of
               the token, a stablecoin-friendly default).

        The floor pattern saves approve transactions for streams of
        small-budget jobs; large-budget jobs always fall back to exact
        approve so residual allowance is bounded.

        Note
        ----
        Callers who want full manual control should pre-approve via
        ``erc8183.approve_payment_token(spender, cap)``; the allowance check
        above will then detect the existing allowance and skip the approve.
        """
        current = self.token_allowance(self.address, self.commerce.address)
        if current < amount:
            if approve_floor is None:
                floor = DEFAULT_APPROVE_FLOOR_UNITS * (10 ** self.token_decimals())
            else:
                if approve_floor < 0:
                    raise ValueError("approve_floor must be >= 0")
                floor = approve_floor
            cap = max(amount, floor)
            logger.debug(
                "[ERC8183Client] topping up allowance: current=%s amount=%s cap=%s",
                current, amount, cap,
            )
            self.approve_payment_token(self.commerce.address, cap)

        return self.commerce.fund(job_id, amount)

    def submit(
        self,
        job_id: int,
        deliverable: bytes,
        opt_params: dict,
    ) -> dict[str, Any]:
        """Provider submits.

        ``deliverable`` is ``DeliverableManifest.manifest_hash()`` — the
        keccak256 of the canonical manifest JSON (32 bytes). Stored on-chain
        as the ERC-8183 ``deliverable`` field (bytes32).

        ``opt_params`` is a dict serialised to JSON bytes and stored on-chain
        as ``optParams``. Must contain ``"deliverable_url"`` (the URL where
        the full manifest JSON can be fetched for verification). Example::

            {"deliverable_url": "ipfs://Qm..."}
        """
        if not opt_params.get("deliverable_url"):
            raise ValueError(
                "opt_params['deliverable_url'] must be a non-empty URL "
                "(storage URL or agent HTTP endpoint)"
            )
        encoded = json.dumps(opt_params, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return self.commerce.submit(job_id, deliverable, encoded)

    def cancel_open(
        self,
        job_id: int,
        reason: bytes = ZERO_REASON,
    ) -> dict[str, Any]:
        """Client cancels a job still in Open state (no escrow moved)."""
        return self.commerce.reject(job_id, reason)

    def claim_refund(self, job_id: int) -> dict[str, Any]:
        return self.commerce.claim_refund(job_id)

    def settle(self, job_id: int, evidence: bytes = b"") -> dict[str, Any]:
        """Permissionless: pull the policy verdict and apply it on-chain."""
        return self.router.settle(job_id, evidence)

    def mark_expired(self, job_id: int) -> dict[str, Any]:
        """Permissionless: reconcile the Router's in-flight counter for a
        job that exited via ``claimRefund`` (audit L03)."""
        return self.router.mark_expired(job_id)

    def dispute(self, job_id: int) -> dict[str, Any]:
        return self.policy.dispute(job_id)

    def vote_reject(self, job_id: int) -> dict[str, Any]:
        return self.policy.vote_reject(job_id)

    # ------------------------------------------------------------------ views

    def get_job(self, job_id: int) -> Job:
        return self.commerce.get_job(job_id)

    def get_job_status(self, job_id: int) -> JobStatus:
        return self.commerce.get_job(job_id).status

    def get_deliverable_url(self, job_id: int, *, hint_block: int | None = None) -> str | None:
        """Return the ``deliverable_url`` for a submitted job.

        Reads the ``JobInitialised`` event emitted by the policy and parses
        ``optParams`` JSON to extract ``deliverable_url``. Returns ``None``
        if the event is not found or the job has not been submitted yet.

        When ``hint_block`` is not provided the method self-resolves it by
        querying Commerce's ``JobSubmitted`` event first (tight 5-block window
        around current head, walking back in 1 000-block steps until found).
        This avoids wide log scans that exceed NodeReal's block-range limit.
        """
        if hint_block is None:
            hint_block = self._resolve_submit_block(job_id)
        return self.policy.get_deliverable_url(job_id, hint_block=hint_block)

    def _resolve_submit_block(self, job_id: int, *, lookback: int = 50_000, step: int = 1_000) -> int | None:
        """Find the block where ``JobSubmitted`` was emitted for *job_id*.

        Walks backwards from the current head in ``step``-block windows so
        each individual RPC call stays within NodeReal's 5 000-block limit.
        Returns the block number, or ``None`` if not found within ``lookback``.
        """
        try:
            current = self.commerce.w3.eth.block_number
        except Exception:
            return None

        for end in range(current, max(0, current - lookback) - 1, -step):
            start = max(0, end - step + 1)
            try:
                logs = self.commerce.contract.events.JobSubmitted().get_logs(
                    from_block=start,
                    to_block=end,
                    argument_filters={"jobId": job_id},
                )
                if logs:
                    return logs[0]["blockNumber"]
            except Exception:
                pass
        return None

    def get_verdict(self, job_id: int, evidence: bytes = b"") -> tuple[Verdict, bytes]:
        """Simulate the verdict the Router would see right now."""
        return self.policy.check(job_id, evidence)

    def inflight_job_count(self) -> int:
        """Number of jobs the Router currently considers in-flight (audit L03)."""
        return self.router.inflight_job_count()

    def dispute_quorum_snapshot(self, job_id: int) -> int:
        """Quorum threshold snapshotted at ``dispute()`` time (audit L08)."""
        return self.policy.dispute_quorum_snapshot(job_id)
