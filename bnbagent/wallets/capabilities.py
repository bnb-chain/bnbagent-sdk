"""Wallet capability registry — an open set of string constants.

A capability is a routing-relevant "can this wallet do X" bit, consumed by
:meth:`~bnbagent.wallets.WalletProvider.capabilities` /
:meth:`~bnbagent.wallets.WalletProvider.supports` to pick a path (which
executor, which x402 flow, which tools enter the LLM's list). Behavioral
variation within a supported path (e.g. ``fund_bundles_approval``) is **not**
a capability — it stays a plain provider attribute.

Rules of the registry (design doc §3.4):

- **Open set.** These are plain strings, not an Enum. Third parties may add
  vendor-namespaced values (``"acme.batch_sign"``) without touching the core.
- **Unknown ⇒ ignore, absent ⇒ unsupported.** Consumers MUST ignore
  capability values they do not recognise and MUST treat the absence of a
  value as "not supported" (the EIP-5792 omission rule). Never probe by
  calling and catching.
- **``sign.*`` values are auto-derived** from method overrides by the base
  :meth:`WalletProvider.capabilities` (declaration cannot drift from
  behavior). Corollary: never override a ``sign_*`` method just to raise —
  the base default already raises a descriptive
  :class:`~bnbagent.wallets.errors.UnsupportedWalletOperation`, and an
  override-to-raise would falsely claim the capability. Non-``sign.*``
  capabilities are declared via ``_extra_capabilities``.
"""

from __future__ import annotations

#: EIP-191 personal-sign (``sign_message``). Auto-derived.
SIGN_MESSAGE = "sign.message"

#: Raw transaction signing (``sign_transaction``). Auto-derived; the
#: prerequisite for the default ``LocalExecutor`` path.
SIGN_TRANSACTION = "sign.transaction"

#: EIP-712 typed-data signing (``sign_typed_data``). Auto-derived; the
#: prerequisite for ``X402Signer``.
SIGN_TYPED_DATA = "sign.typed_data"

#: Arbitrary mechanical contract calls (vs. a fixed command menu).
CALLS_ARBITRARY = "calls.arbitrary"

#: The wallet broadcasts its own transactions (it is its own executor).
BROADCAST_SELF = "broadcast.self"

#: Executes ERC-8004 identity intents natively.
INTENTS_ERC8004 = "intents.erc8004"

#: Executes ERC-8183 job intents natively.
INTENTS_ERC8183 = "intents.erc8183"

#: The SDK can complete an x402 payment with this wallet (locally signed or
#: fully delegated to the wallet backend).
X402_PAY = "x402.pay"

#: Transactions can be sponsored via a paymaster (MegaFuel) broadcast.
PAYMASTER_SPONSOR = "paymaster.sponsor"
