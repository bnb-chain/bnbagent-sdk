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
- The default payment token remains available through ``paymentToken()``, while
  each job's authoritative token is read from ``jobPaymentToken(jobId)``.
- ``fund`` defaults to exact approval on the job-bound token. A larger approval
  floor is available only through the explicit legacy opt-in parameter.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any

from web3 import Web3

from ..config import NetworkConfig, resolve_network
from ..core.abi_loader import create_web3
from ..core.contract_mixin import READ_ONLY_MESSAGE
from ..erc20.client import MinimalERC20Client
from ..exceptions import JobPaymentTokenMismatchError
from ..networks import AssetId, get_asset, get_asset_by_address, list_assets, parse_asset_id
from ..wallets.wallet_provider import WalletProvider
from .commerce import CommerceClient
from .policy import PolicyClient
from .quote_verify import QuoteSignatureVerdict, verify_quote_signature
from .router import RouterClient
from .types import ZERO_ADDRESS, ZERO_REASON, Job, JobStatus, Verdict

logger = logging.getLogger(__name__)


TokenReference = AssetId | str


@dataclass(frozen=True)
class TokenMetadata:
    """On-chain ERC-20 metadata cached for one checksummed token address."""

    address: str
    decimals: int
    symbol: str


# Legacy convenience constant for callers that explicitly opt in to an
# approval floor. ``fund`` itself defaults to exact approval.
DEFAULT_APPROVE_FLOOR_UNITS: int = 100

# Chain IDs where MegaFuel sponsors ERC-8183 writes, so ``ERC8183Client``
# wires a paymaster into the write path. bsc-testnet (97) is sponsored;
# bsc-mainnet (56) is **never** sponsored for ERC-8183 — its writes self-pay,
# and we don't even probe ``isSponsorable`` there (the hot production path).
# If mainnet sponsorship ever lands, add 56 here — that single edit flips it.
# (ERC-8004 sponsorship is independent and handled in erc8004/agent.py.)
ERC8183_PAYMASTER_CHAIN_IDS: frozenset[int] = frozenset({97})


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
        self.address: str | None = wallet_provider.address if wallet_provider is not None else None

        # Gas sponsorship: wire a paymaster into the write path only on
        # networks where MegaFuel sponsors ERC-8183 (testnet today; mainnet
        # never — see ERC8183_PAYMASTER_CHAIN_IDS). The executor still gates
        # each write on isSponsorable and self-pays when it cannot sponsor, so
        # this only decides whether to *attempt* sponsorship at all.
        paymaster = self._build_paymaster(nc, debug)

        self.commerce = CommerceClient(
            self.w3, nc.commerce_contract, wallet_provider, paymaster=paymaster
        )
        self.router = RouterClient(
            self.w3, nc.router_contract, wallet_provider, paymaster=paymaster
        )
        self.policy = PolicyClient(
            self.w3, nc.policy_contract, wallet_provider, paymaster=paymaster
        )

        # Cached token state (populated lazily and keyed by checksum address).
        self._payment_token_address: str | None = None
        self._erc20_clients: dict[str, MinimalERC20Client] = {}
        self._token_decimals: dict[str, int] = {}
        self._token_symbols: dict[str, str] = {}
        self._token_metadata: dict[str, TokenMetadata] = {}

    @staticmethod
    def _build_paymaster(nc: NetworkConfig, debug: bool):
        """Return a ``Paymaster`` for ERC-8183 writes, or ``None`` to self-pay.

        Built only when the network enables a paymaster AND its chain is one
        MegaFuel sponsors ERC-8183 on (``ERC8183_PAYMASTER_CHAIN_IDS``). On
        every other network — notably bsc-mainnet — this returns ``None`` so
        writes self-pay without ever probing ``isSponsorable``.

        Note: the ERC-20 ``approve`` inside :meth:`fund` runs through the
        ERC-20 client's own self-pay path and is not sponsored here.
        """
        if nc.use_paymaster and nc.paymaster_url and nc.chain_id in ERC8183_PAYMASTER_CHAIN_IDS:
            from ..core.paymaster import Paymaster

            return Paymaster(paymaster_url=nc.paymaster_url, debug=debug)
        return None

    # ------------------------------------------------------------ token cache

    @property
    def payment_token(self) -> str:
        """Payment token address (cached). Fetched from ``commerce.paymentToken``."""
        if self._payment_token_address is None:
            self._payment_token_address = Web3.to_checksum_address(self.commerce.payment_token())
        return self._payment_token_address

    def _resolve_token_address(self, token: TokenReference) -> str:
        """Resolve a canonical AssetId or checksum a direct token address."""
        if isinstance(token, AssetId):
            return get_asset(self.network.chain_id, token).address
        if not isinstance(token, str):
            raise TypeError("token must be a canonical AssetId or address")
        if Web3.is_address(token):
            return Web3.to_checksum_address(token)
        canonical = parse_asset_id(token)
        return get_asset(self.network.chain_id, canonical).address

    def _resolve_job_creation_token(self, token: TokenReference) -> str:
        """Require current-chain catalog membership on cataloged networks."""
        address = self._resolve_token_address(token)
        try:
            list_assets(self.network.chain_id)
        except KeyError:
            return address
        return get_asset_by_address(self.network.chain_id, address).address

    def _erc20_client(self, token: TokenReference | None = None) -> MinimalERC20Client:
        address = self.payment_token if token is None else self._resolve_token_address(token)
        client = self._erc20_clients.get(address)
        if client is None:
            client = MinimalERC20Client(self.w3, address, self._wallet_provider)
            self._erc20_clients[address] = client
        return client

    def token_metadata(self, token: TokenReference) -> TokenMetadata:
        address = self._resolve_token_address(token)
        metadata = self._token_metadata.get(address)
        if metadata is None:
            metadata = TokenMetadata(
                address=address,
                decimals=self.token_decimals(address),
                symbol=self.token_symbol(address),
            )
            self._token_metadata[address] = metadata
        return metadata

    def token_balance_for(self, token: TokenReference, address: str | None = None) -> int:
        return self._erc20_client(token).balance_of(address or self.address)

    def token_allowance_for(self, token: TokenReference, owner: str, spender: str) -> int:
        return self._erc20_client(token).allowance(owner, spender)

    def approve_token(self, token: TokenReference, spender: str, amount: int) -> dict[str, Any]:
        return self._erc20_client(token).approve(spender, amount)

    def token_decimals(self, token: TokenReference | None = None) -> int:
        address = self.payment_token if token is None else self._resolve_token_address(token)
        if address not in self._token_decimals:
            self._token_decimals[address] = self._erc20_client(address).decimals()
        return self._token_decimals[address]

    def token_symbol(self, token: TokenReference | None = None) -> str:
        address = self.payment_token if token is None else self._resolve_token_address(token)
        if address not in self._token_symbols:
            self._token_symbols[address] = self._erc20_client(address).symbol()
        return self._token_symbols[address]

    def token_balance(self, address: str | None = None) -> int:
        return self._erc20_client().balance_of(address or self.address)

    def token_allowance(self, owner: str, spender: str) -> int:
        return self._erc20_client().allowance(owner, spender)

    def verify_negotiation_quote(
        self,
        envelope: dict[str, Any],
        *,
        expected_provider: str,
        block_number: int | None = None,
    ) -> QuoteSignatureVerdict:
        """Verify a provider quote before the buyer creates or funds a job.

        ``expected_provider`` is deliberately out-of-band: obtain it from a
        trusted ERC-8004 discovery result or operator configuration, never from
        ``envelope["provider_address"]``. The quote must be accepted, carry a
        positive integer price in this Commerce contract's payment token, bind
        this chain and Commerce address, and have a valid EIP-191/ERC-1271
        provider signature.
        """
        response = envelope.get("response")
        if not isinstance(response, dict):
            return QuoteSignatureVerdict(valid=False, reason="quote response is missing")
        if response.get("accepted") is not True:
            return QuoteSignatureVerdict(valid=False, reason="quote is not accepted")
        terms = response.get("terms")
        if not isinstance(terms, dict):
            return QuoteSignatureVerdict(valid=False, reason="quote terms are missing")

        price = terms.get("price")
        valid_price = (isinstance(price, int) and not isinstance(price, bool) and price > 0) or (
            isinstance(price, str)
            and price.isascii()
            and price.isdecimal()
            and not price.startswith("0")
        )
        if not valid_price:
            return QuoteSignatureVerdict(
                valid=False, reason="quote price must be a positive integer"
            )

        currency = terms.get("currency")
        try:
            currency_address = Web3.to_checksum_address(currency)
            payment_token = Web3.to_checksum_address(self.payment_token)
        except (TypeError, ValueError):
            return QuoteSignatureVerdict(valid=False, reason="quote currency is invalid")
        if currency_address != payment_token:
            return QuoteSignatureVerdict(
                valid=False, reason="quote currency does not match payment token"
            )

        signed_chain_id = envelope.get("chain_id")
        if (
            not isinstance(signed_chain_id, int)
            or isinstance(signed_chain_id, bool)
            or signed_chain_id != self.network.chain_id
        ):
            return QuoteSignatureVerdict(valid=False, reason="quote chain_id mismatch")

        return verify_quote_signature(
            envelope=envelope,
            provider=expected_provider,
            w3=self.w3,
            expected_verifying_contract=self.commerce.address,
            block_number=block_number,
        )

    def approve_payment_token(self, spender: str, amount: int) -> dict[str, Any]:
        """Send ``approve(spender, amount)`` on the payment token."""
        return self.approve_token(self.payment_token, spender, amount)

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
        self._validate_expiry(expired_at, skip_expiry_check)

        return self.commerce.create_job(
            provider=provider,
            evaluator=self.router.address,
            expired_at=expired_at,
            description=description,
            hook=hook if hook is not None else self.router.address,
        )

    def create_job_with_token(
        self,
        *,
        asset: TokenReference,
        provider: str = ZERO_ADDRESS,
        expired_at: int,
        description: str = "",
        hook: str | None = None,
        skip_expiry_check: bool = False,
    ) -> dict[str, Any]:
        """Create a routed job bound to a canonical asset or catalog address."""
        token = self._resolve_job_creation_token(asset)
        self._validate_expiry(expired_at, skip_expiry_check)
        return self.commerce.create_job_with_token(
            provider=provider,
            evaluator=self.router.address,
            expired_at=expired_at,
            description=description,
            hook=hook if hook is not None else self.router.address,
            token=token,
        )

    def _validate_expiry(self, expired_at: int, skip_expiry_check: bool) -> None:
        if not skip_expiry_check:
            try:
                import time

                dispute_window = int(self.policy.dispute_window())
                now = int(time.time())
                if expired_at - now <= dispute_window:
                    raise ValueError(
                        f"expired_at ({expired_at}) is too close to now ({now}). "
                        f"OptimisticPolicy on this network has dispute_window="
                        f"{dispute_window}s ({dispute_window / 86400:.1f}d), so the "
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
        expected_token: TokenReference | None = None,
        approve_floor: int | None = None,
    ) -> dict[str, Any]:
        """Fund a job using its authoritative on-chain payment token.

        The job token is read before any allowance or approval action. When
        ``expected_token`` is provided, a mismatch raises
        :class:`JobPaymentTokenMismatchError` before funds can move.

        Missing allowance is approved for exactly ``amount`` by default.
        ``approve_floor`` remains an explicit legacy opt-in that approves
        ``max(amount, approve_floor)``. A zero-price job never approves.
        """
        if amount < 0:
            raise ValueError("amount must be >= 0")
        if approve_floor is not None and approve_floor < 0:
            raise ValueError("approve_floor must be >= 0")
        if not self._wallet_provider or not self.address:
            raise RuntimeError(READ_ONLY_MESSAGE)

        actual_token = self.job_payment_token(job_id)
        if expected_token is not None:
            expected_address = self._resolve_token_address(expected_token)
            if expected_address != actual_token:
                raise JobPaymentTokenMismatchError(
                    job_id=job_id,
                    expected_token=expected_address,
                    actual_token=actual_token,
                )

        if amount == 0:
            return self.commerce.fund(job_id, amount)

        # A self-broadcasting backend (e.g. twak) bundles approve+deposit in
        # its own fund operation — skip the SDK-side allowance management.
        # ``is True`` guards against MagicMock wallets in tests.
        if getattr(self._wallet_provider, "fund_bundles_approval", False) is True:
            return self.commerce.fund(job_id, amount)

        current = self.token_allowance_for(actual_token, self.address, self.commerce.address)
        if current < amount:
            cap = amount if approve_floor is None else max(amount, approve_floor)
            logger.debug(
                "[ERC8183Client] topping up allowance: current=%s amount=%s cap=%s",
                current,
                amount,
                cap,
            )
            self.approve_token(actual_token, self.commerce.address, cap)

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

    def job_payment_token(self, job_id: int) -> str:
        """Return the checksummed token address bound to ``job_id``."""
        return Web3.to_checksum_address(self.commerce.job_payment_token(job_id))

    def is_payment_token_supported(self, token: TokenReference) -> bool:
        """Read the Commerce allowlist for a canonical asset or address."""
        return self.commerce.is_payment_token_supported(self._resolve_token_address(token))

    def get_job_status(self, job_id: int) -> JobStatus:
        return self.commerce.get_job(job_id).status

    def get_job_funded_block(
        self,
        job_id: int,
        *,
        negotiated_at: int,
        quote_expires_at: int,
    ) -> int | None:
        """Return the ``JobFunded`` block inside a signed quote window.

        Timestamp-to-block binary searches keep the indexed event query
        narrow.  ``None`` means the job was not economically accepted while
        the quote was valid and callers must fail closed.
        """
        if (
            not isinstance(negotiated_at, int)
            or isinstance(negotiated_at, bool)
            or not isinstance(quote_expires_at, int)
            or isinstance(quote_expires_at, bool)
            or negotiated_at < 0
            or quote_expires_at <= negotiated_at
        ):
            raise ValueError("invalid signed quote time window")

        head_number = self.w3.eth.block_number
        head = self.w3.eth.get_block(head_number)
        if int(head["timestamp"]) < negotiated_at:
            return None
        from_block = self._first_block_at_or_after(negotiated_at, head_number)
        to_block = (
            head_number
            if int(head["timestamp"]) < quote_expires_at
            else self._first_block_at_or_after(quote_expires_at, head_number)
        )
        events = self.commerce.get_job_funded_events(
            from_block,
            to_block,
            job_id=job_id,
        )
        return int(events[0]["blockNumber"]) if events else None

    def _first_block_at_or_after(self, timestamp: int, head: int) -> int:
        """Return the lowest block whose timestamp is at least ``timestamp``."""
        low = 0
        high = head
        while low < high:
            mid = (low + high) // 2
            block = self.w3.eth.get_block(mid)
            if int(block["timestamp"]) < timestamp:
                low = mid + 1
            else:
                high = mid
        return low

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

    def _resolve_submit_block(
        self, job_id: int, *, lookback: int = 50_000, step: int = 1_000
    ) -> int | None:
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
