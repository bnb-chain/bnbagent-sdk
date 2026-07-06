"""Consumer-facing narrow signing protocols (interface segregation).

Each :class:`~typing.Protocol` here is the *exact* dependency surface of one
wallet consumer — an ``address`` property plus the single ``sign_*`` method it
calls — instead of the full :class:`~bnbagent.wallets.WalletProvider` ABC.
They map 1:1 to the capability registry: ``sign.message`` ↔
:class:`MessageSigner`, ``sign.typed_data`` ↔ :class:`TypedDataSigner`.

Matching is structural (PEP 544): any object with the right shape can be
passed — no inheritance from ``WalletProvider`` required. The shape mirrors
coinbase x402's ``ClientEvmSigner`` (prior art: "deliberately tiny,
structural — the entire wallet contract for the buyer").

Known limitation: structural matching cannot *statically* exclude a wallet
whose method exists but raises. Every ``WalletProvider`` subclass inherits
``sign_typed_data`` from the base (where the default raises
:class:`~bnbagent.wallets.errors.UnsupportedWalletOperation`), so a
type-checker will accept e.g. ``X402Signer(twak_provider)``. The runtime
capability gates carry that weight — the composition-time ``supports()``
check in consumers, and the raising base default itself.
"""

from __future__ import annotations

from typing import Any, Protocol


class MessageSigner(Protocol):
    """The narrow contract ERC-8183 negotiation actually depends on."""

    @property
    def address(self) -> str: ...

    def sign_message(self, message: str) -> dict[str, Any]: ...


class TypedDataSigner(Protocol):
    """The narrow contract X402Signer actually depends on."""

    @property
    def address(self) -> str: ...

    def sign_typed_data(
        self,
        domain: dict[str, Any],
        types: dict[str, list[dict[str, str]]],
        message: dict[str, Any],
    ) -> dict[str, Any]: ...
