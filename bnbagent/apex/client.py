"""``APEXClient`` — single-entry facade over the APEX v1 contract stack.

APEX v1 is a three-layer protocol:

- ``AgenticCommerceUpgradeable`` — ERC-8183 kernel (escrow).
- ``EvaluatorRouterUpgradeable`` — routing layer acting as ``job.evaluator``
  and ``job.hook`` for every routed job.
- ``OptimisticPolicy``           — UMA-style silence-approves policy with
  a whitelisted-voter reject quorum.

``APEXClient`` composes three thin sub-clients (``commerce`` / ``router`` /
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
from ._erc20 import MinimalERC20Client
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


class APEXClient:
    """High-level facade over Commerce + Router + Policy.

    Parameters
    ----------
    wallet_provider:
        Required ``WalletProvider`` that performs all signing. Raw private
        keys never cross this boundary — wrap them in ``EVMWalletProvider``
        first.
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
        wallet_provider: WalletProvider,
        network: str | NetworkConfig = "bsc-testnet",
        *,
        debug: bool = False,
    ) -> None:
        if wallet_provider is None:
            raise ValueError(
                "wallet_provider is required. Wrap your key in EVMWalletProvider "
                "(e.g. EVMWalletProvider(password='...', private_key='0x...'))."
            )

        nc = resolve_network(network)
        for field_name in ("commerce_contract", "router_contract", "policy_contract"):
            if not getattr(nc, field_name):
                raise ValueError(
                    f"network '{nc.name}' is missing {field_name}; "
                    "pass a NetworkConfig with all three APEX addresses set."
                )

        self.debug = debug
        self.network = nc
        self.w3 = create_web3(nc.rpc_url)

        self._wallet_provider = wallet_provider
        self.address: str = wallet_provider.address

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
    ) -> dict[str, Any]:
        """Create a job with the Router set as evaluator + hook.

        Parameters mirror ``AgenticCommerceUpgradeable.createJob`` except
        ``evaluator`` / ``hook`` default to the Router address (the
        v1 deployment pattern).
        """
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
        ``apex.approve_payment_token(spender, cap)``; the allowance check
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
                "[APEXClient] topping up allowance: current=%s amount=%s cap=%s",
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
        if "deliverable_url" not in opt_params:
            raise ValueError("opt_params must contain 'deliverable_url'")
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

        Args:
            hint_block: approximate block where the job was submitted.
                Passing this narrows the log query window and avoids RPC
                block-range limits on providers like NodeReal.
        """
        return self.policy.get_deliverable_url(job_id, hint_block=hint_block)

    def get_verdict(self, job_id: int, evidence: bytes = b"") -> tuple[Verdict, bytes]:
        """Simulate the verdict the Router would see right now."""
        return self.policy.check(job_id, evidence)
