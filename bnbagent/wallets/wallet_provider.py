"""
Wallet Provider Abstract Base Class

Defines the interface that all wallet providers must implement.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any, ClassVar

from .capabilities import SIGN_MESSAGE, SIGN_TRANSACTION, SIGN_TYPED_DATA
from .errors import UnsupportedWalletOperation

if TYPE_CHECKING:
    from .intents import ExecutionContext, IntentExecutor


class WalletProvider(ABC):
    """
    Abstract base class for wallet providers.

    This interface defines the contract that all wallet providers must implement,
    allowing for easy swapping between different wallet implementations (EVM, MPC, etc.).
    """

    #: Stable, lowercase identifier for this provider kind (``"evm"``,
    #: ``"twak"``, ``"mpc"``, ...). Used by
    #: :func:`~bnbagent.wallets.create_wallet_provider` to select an
    #: implementation and by :meth:`describe` for uniform introspection.
    #: Concrete providers override it; third-party subclasses keep the default.
    kind: ClassVar[str] = "custom"

    #: Whether this wallet's ERC-8183 ``fund`` execution bundles the
    #: payment-token approval itself (fund bundles approval: approve +
    #: deposit in one operation). ``False`` for pure signers — the SDK
    #: manages the allowance and sends a separate ``approve`` before
    #: ``fund``. A self-broadcasting backend that owns the funding flow
    #: end-to-end (e.g. the twak CLI, whose ``erc8183 fund`` approves then
    #: deposits) sets this to ``True`` so the SDK skips its own allowance
    #: top-up.
    fund_bundles_approval: ClassVar[bool] = False

    #: Non-``sign.*`` capabilities this provider declares (execution- and
    #: service-side bits like ``calls.arbitrary`` or ``broadcast.self``).
    #: Concrete providers set this; ``sign.*`` values are never listed here —
    #: they are auto-derived by :meth:`capabilities`.
    _extra_capabilities: ClassVar[frozenset[str]] = frozenset()

    @property
    def key_location(self) -> str | None:
        """Human-readable description of *where this wallet's key lives*.

        There is no single shared key store across providers — each owns its
        own custody (the SDK keystore directory, an external CLI's keychain, a
        remote MPC enclave, ...). This property gives a uniform way to answer
        "where is my key?" without unifying the underlying storage. Returns
        ``None`` when the location is unknown or not applicable.
        """
        return None

    def exists(self) -> bool:
        """Whether durable key material already backs this provider.

        Defaults to ``True`` (a constructed provider is assumed usable).
        Providers with an on-disk or external store override this to report
        whether the wallet has actually been created and persisted, so callers
        can implement a uniform "get-or-create" flow. Implementations MUST NOT
        raise — they return ``False`` when existence cannot be confirmed.
        """
        return True

    def capabilities(self) -> frozenset[str]:
        """The set of capability strings this wallet supports.

        Values come from :mod:`bnbagent.wallets.capabilities` (an open set —
        third parties may add vendor-namespaced strings; consumers ignore
        unknown values and treat absence as unsupported).

        ``sign.*`` values are **auto-derived from method overrides**: a
        provider that implements ``sign_message`` / ``sign_transaction`` /
        ``sign_typed_data`` declares the matching capability by that override
        alone, so declaration cannot drift from behavior. Corollary
        (the don't-override-to-raise discipline): never override a ``sign_*``
        method just to raise — the base default already raises a descriptive
        :class:`UnsupportedWalletOperation`, and an override-to-raise would
        falsely claim the capability. Everything else comes from the
        provider's ``_extra_capabilities`` class attribute.
        """
        derived = {
            capability
            for capability, name in (
                (SIGN_MESSAGE, "sign_message"),
                (SIGN_TRANSACTION, "sign_transaction"),
                (SIGN_TYPED_DATA, "sign_typed_data"),
            )
            if getattr(type(self), name) is not getattr(WalletProvider, name)
        }
        return frozenset(derived) | self._extra_capabilities

    def supports(self, capability: str) -> bool:
        """Whether ``capability`` is in :meth:`capabilities` (membership test)."""
        return capability in self.capabilities()

    def describe(self) -> dict[str, Any]:
        """Return a uniform, non-sensitive summary of this wallet.

        Keys: ``kind``, ``address`` (``None`` if unavailable),
        ``key_location``, ``exists`` and ``capabilities`` (sorted list).
        Never includes private key material.
        """
        try:
            address: str | None = self.address
        except Exception:
            address = None
        return {
            "kind": self.kind,
            "address": address,
            "key_location": self.key_location,
            "exists": self.exists(),
            "capabilities": sorted(self.capabilities()),
        }

    def make_executor(self, context: ExecutionContext) -> IntentExecutor:
        """Return the :class:`IntentExecutor` that runs operations for this wallet.

        This makes execution polymorphic, so callers never special-case wallet
        kinds. The default wraps this signer in a
        :class:`~bnbagent.wallets.local_executor.LocalExecutor` that builds,
        signs and broadcasts via the provided web3/paymaster ``context`` — the
        path every pure-signing wallet (EVM, hardware, ...) shares.

        A self-broadcasting wallet (one that owns the broadcast step, e.g. a
        CLI-backed backend) overrides this to return ``self``.

        The default requires the ``sign.transaction`` capability and raises
        :class:`UnsupportedWalletOperation` at this construction point —
        before any intent runs — when it is absent.
        """
        if not self.supports(SIGN_TRANSACTION):
            raise UnsupportedWalletOperation(
                SIGN_TRANSACTION,
                reason="the default executor signs transactions locally",
                alternative=(
                    "self-broadcasting wallets must override make_executor() "
                    "to return their own IntentExecutor"
                ),
            )
        from .local_executor import LocalExecutor

        return LocalExecutor(
            web3=context.web3,
            wallet_provider=self,
            paymaster=context.paymaster,
            receipt_timeout=context.receipt_timeout,
        )

    @property
    @abstractmethod
    def address(self) -> str:
        """
        Get the wallet address.

        Returns:
            str: The Ethereum address of the wallet
        """
        pass

    def sign_transaction(self, transaction: dict[str, Any]) -> dict[str, Any]:
        """
        Sign a transaction.

        The default raises :class:`UnsupportedWalletOperation`; implementing
        this method declares the ``sign.transaction`` capability.

        Args:
            transaction: Transaction dictionary with fields like 'to', 'value', 'gas',
                        'gasPrice', 'nonce', 'data', 'chainId'

        Returns:
            dict: Signed transaction dictionary with 'rawTransaction', 'hash', 'r', 's', 'v'
        """
        raise UnsupportedWalletOperation(
            SIGN_TRANSACTION,
            reason=(
                f"the {self.kind!r} wallet does not implement raw-transaction "
                "signing"
            ),
            alternative=(
                "use a wallet whose capabilities() include 'sign.transaction', "
                "or route high-level operations through the wallet's own "
                "executor (make_executor() / execute(Intent(...)))"
            ),
        )

    def sign_message(self, message: str) -> dict[str, Any]:
        """
        Sign a message using EIP-191 personal sign.

        The default raises :class:`UnsupportedWalletOperation`; implementing
        this method declares the ``sign.message`` capability.

        Args:
            message: Message string to sign

        Returns:
            dict: Signature dictionary with 'messageHash', 'r', 's', 'v',
                  'signature'. ``messageHash`` is the **EIP-191 personal-sign
                  digest** (``keccak256("\\x19Ethereum Signed Message:\\n" || len ||
                  message)``) — *not* interchangeable with the digest returned by
                  :meth:`sign_typed_data`.
        """
        raise UnsupportedWalletOperation(
            SIGN_MESSAGE,
            reason=(
                f"the {self.kind!r} wallet does not implement EIP-191 "
                "personal-sign"
            ),
            alternative=(
                "use a wallet whose capabilities() include 'sign.message'"
            ),
        )

    def sign_typed_data(
        self,
        domain: dict[str, Any],
        types: dict[str, list[dict[str, str]]],
        message: dict[str, Any],
    ) -> dict[str, Any]:
        """
        Sign typed structured data per EIP-712, gated by a SigningPolicy.

        The default raises :class:`UnsupportedWalletOperation`; implementing
        this method declares the ``sign.typed_data`` capability.

        Used for protocols requiring signed structured payloads — EIP-3009
        transferWithAuthorization (x402 micropay), ERC-8183 negotiate quotes,
        permit2, etc. The signing key never leaves the wallet implementation.

        Implementations MUST invoke their configured ``SigningPolicy.check()``
        on ``(domain, types, message)`` *before* producing a signature, and
        propagate :class:`bnbagent.signing.PolicyViolation` on rejection. This
        is the SDK's first-line defense against blind-sign attacks via a
        malicious EIP-712 payload (unknown verifyingContract, unbounded
        Permit, open-ended validBefore, etc.). To intentionally bypass the
        policy (tests / migrations only), call the implementation-private
        ``_DANGEROUS_sign_typed_data_no_policy`` method, which logs a WARN
        with the caller module for auditability.

        Args:
            domain: EIP-712 domain separator, e.g.
                    ``{"name": "United Stables", "version": "1",
                       "chainId": 56, "verifyingContract": "0x..."}``.
            types: Dict mapping each EIP-712 struct name to a list of
                   ``{"name": str, "type": str}`` field descriptors. Must include
                   the ``EIP712Domain`` entry alongside the message struct(s).
            message: The struct values keyed by field name. The primary type is
                     inferred as the only struct in ``types`` that is not
                     ``EIP712Domain``.

        Returns:
            dict: Signature dictionary with 'messageHash', 'r', 's', 'v',
                  'signature'. Same shape as :meth:`sign_message`, but
                  ``messageHash`` here is the **EIP-712 typed-data digest**
                  (``keccak256("\\x19\\x01" || domainSeparator ||
                  hashStruct(message))``) — *not* the EIP-191 digest returned by
                  :meth:`sign_message`. This is the value that on-chain
                  ``ecrecover`` will use against this signature.

        Raises:
            bnbagent.signing.PolicyViolation: If the configured SigningPolicy
                refuses the request (unknown domain, denylisted primary
                type, validity window too wide, etc.).
            UnsupportedWalletOperation: For wallet kinds that do not
                implement EIP-712 signing (the base default raise; a
                ``NotImplementedError`` subclass, so existing callers keep
                working).
        """
        raise UnsupportedWalletOperation(
            SIGN_TYPED_DATA,
            reason=(
                f"the {self.kind!r} wallet does not implement EIP-712 "
                "typed-data signing"
            ),
            alternative=(
                "use a wallet whose capabilities() include 'sign.typed_data', "
                "or a delegated flow that signs internally (e.g. the x402 "
                "payer path for payments)"
            ),
        )
