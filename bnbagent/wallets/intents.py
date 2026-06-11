"""Intent — the SDK's high-level execution seam.

An :class:`Intent` describes a single high-level on-chain operation
(register an agent, set metadata, fund a job, ...). It is deliberately
*dual-representation* so that different execution backends can consume
whichever form they understand:

- **Semantic form** (``name`` + ``kwargs``): the operation expressed as a
  namespaced identifier and its high-level arguments, e.g.
  ``"erc8004.register"`` with ``{"agent_uri": ..., "metadata": [...]}``.
  Consumed by backends that rebuild the call themselves — for example a
  CLI- or REST-backed wallet that owns build + sign + broadcast and only
  speaks high-level commands.
- **Mechanical form** (``call``): a pre-encoded web3 ``ContractFunction``
  ready to be built, signed and broadcast. Consumed by the local signing
  executor, which stays protocol-agnostic — it never needs to know what
  ``name`` means.

The call site (a contract client that already holds the ABI) produces both
forms cheaply, so the asymmetry between backends is absorbed there rather
than leaking protocol knowledge into any executor.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

# ── Intent name constants (namespaced "<module>.<operation>") ──
# Used by semantic executors to recognise an operation. The local executor
# ignores these and works purely off ``Intent.call``.
ERC8004_REGISTER = "erc8004.register"
ERC8004_SET_METADATA = "erc8004.set_metadata"
ERC8004_SET_AGENT_URI = "erc8004.set_agent_uri"

ERC8183_CREATE_JOB = "erc8183.create_job"
ERC8183_SET_PROVIDER = "erc8183.set_provider"
ERC8183_SET_BUDGET = "erc8183.set_budget"
ERC8183_FUND = "erc8183.fund"
ERC8183_SUBMIT = "erc8183.submit"
ERC8183_COMPLETE = "erc8183.complete"
ERC8183_REJECT = "erc8183.reject"
ERC8183_CLAIM_REFUND = "erc8183.claim_refund"
ERC8183_REGISTER_JOB = "erc8183.register_job"
ERC8183_SETTLE = "erc8183.settle"
ERC8183_MARK_EXPIRED = "erc8183.mark_expired"
ERC8183_DISPUTE = "erc8183.dispute"
ERC8183_VOTE_REJECT = "erc8183.vote_reject"


@dataclass
class Intent:
    """A single high-level on-chain operation.

    Args:
        name: Namespaced operation identifier (e.g. ``"erc8004.register"``).
            Empty for purely mechanical calls. Used by semantic executors.
        kwargs: High-level arguments for the operation, keyed by name.
            Used by semantic executors; ignored by the local executor.
        call: Pre-encoded web3 ``ContractFunction`` for the operation. Used
            by the local build/sign/broadcast executor; ignored by semantic
            executors. Typed as ``Any`` to avoid a hard web3 import here.
        value: Native-token value (wei) to send with the call.
        gas: Optional explicit gas limit. ``None`` means the executor
            estimates it.
        description: Human-readable label used in logs.
    """

    name: str = ""
    kwargs: dict[str, Any] = field(default_factory=dict)
    call: Any = None
    value: int = 0
    gas: int | None = None
    description: str = "transaction"


@dataclass
class ExecutionContext:
    """Runtime context a wallet needs to build its executor.

    A pure-signing wallet has no web3 connection of its own, so to broadcast
    it must be handed one. This carries that connection (and an optional
    paymaster) to :meth:`~bnbagent.wallets.WalletProvider.make_executor`.
    Self-broadcasting wallets ignore it.

    Args:
        web3: Connected ``Web3`` instance (typed ``Any`` to avoid importing
            web3 into this lightweight module).
        paymaster: Optional paymaster for gas sponsorship.
        receipt_timeout: Seconds to wait for a transaction receipt.
    """

    web3: Any
    paymaster: Any = None
    receipt_timeout: int = 300


class IntentExecutor(ABC):
    """Executes an :class:`Intent` end-to-end.

    This is the SDK's primary execution seam. The local signing path
    (build + sign via a :class:`~bnbagent.wallets.WalletProvider` +
    broadcast) and any self-broadcasting backend (custodial / CLI-backed
    wallet, account-abstraction bundler, ...) are peer implementations of
    this interface, selected at construction time by the caller.
    """

    @abstractmethod
    def execute(self, intent: Intent) -> dict[str, Any]:
        """Execute the intent and return a canonical result dict.

        Returns:
            dict: At minimum ``{"transactionHash": str, "receipt": Any | None}``.
            Implementations may add operation-specific fields (e.g.
            ``"agentId"``). ``receipt`` may be ``None`` for backends that do
            not surface a full receipt.

        Raises:
            RuntimeError: If the operation reverts or otherwise fails.
            NotImplementedError: If the backend cannot service this intent.
        """
