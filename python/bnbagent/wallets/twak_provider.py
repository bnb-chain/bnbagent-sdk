"""TWAKProvider — a self-broadcasting wallet backed by the ``twak`` CLI.

The Trust Wallet Agent Kit (``twak``) CLI owns the full build + sign +
broadcast lifecycle and exposes only high-level *intent* commands
(``erc8004 register``, ``erc8183 create-job``, ...) plus message signing.
It therefore integrates at the **intent layer**: ``TWAKProvider``
is both a :class:`~bnbagent.wallets.WalletProvider` (it has an address and
can sign messages) and an :class:`~bnbagent.wallets.IntentExecutor` (it is
its own execution backend — there is no separate local signer to wrap).

Key custody lives entirely inside ``twak`` (its keystore + OS keychain).
The agent's on-chain identity is therefore the ``twak`` wallet address, kept
consistent by reading it from the CLI.

Prerequisites (one-time setup, the caller's responsibility)
-----------------------------------------------------------
``TWAKProvider`` assumes ``twak`` is already configured — it never creates a
wallet or handles secrets itself. Before constructing it, the host must have:

1. **The CLI installed:** ``npm install -g @trustwallet/cli``.
2. **API credentials:** ``twak init --api-key <id> --api-secret <secret>``
   (persisted to ``~/.twak/credentials.json``), *or* the env vars
   ``TWAK_ACCESS_ID`` / ``TWAK_HMAC_SECRET`` for CI. ``twak`` reads these
   itself; this provider never passes them.
3. **A wallet:** ``twak wallet create --password <pw>`` (encrypted to
   ``~/.twak/wallet.json``).
4. **A reachable password** for signing: ``TWAK_WALLET_PASSWORD`` env var or
   ``twak wallet keychain save``. Again resolved by ``twak``, never by this
   provider.

When any of these are missing, commands fail and the raised error carries a
short pointer back to these steps (see :data:`_SETUP_HINT`).

Security / operational notes:
- Commands are invoked with an argument *list* (never a shell string), so
  there is no shell-injection surface.
- The wallet password is resolved by ``twak`` itself from the OS keychain or
  the ``TWAK_WALLET_PASSWORD`` environment variable; it is never passed on
  the command line by this provider.
- ``--json`` is always appended. It does not skip twak v0.20.0's
  ``x402 request`` confirmation: that command confirms only with ``--yes``.
  Paid requests from ``TwakX402Payer`` omit ``--yes`` and answer twak's
  pre-signing prompt after checking the final terms (``x402_request``).

Compatibility notes against ``twak`` v0.20.0 — the minimum supported version
(v0.20.0 added ``--paymaster-url``, which this provider emits on every write
whenever a paymaster is configured — the default bsc-testnet config is.
v0.19.0 is excluded outright: its ``sign-message`` hex-decoded ``0x``-shaped
messages and signed the wrong bytes — fixed in v0.19.1, "input is always text". The
capability reference (supported methods per protocol) is ``docs/twak.md``;
on an older CLI, flags this provider emits fail loudly with an upgrade
hint):
- Every erc8183 write passes ``opt_params`` through raw as ``--opt-params``
  (REQ-1 for ``submit`` — the seller role works end-to-end — and S-1 for the
  rest, both shipped in v0.19.0).
- ``fund`` pins the amount with ``--expected-budget`` (S-2, v0.19.0): the
  contract reverts atomically with ``BudgetMismatch()`` on drift, which
  replaced this provider's old client-side ``status`` pre-check.
- ``erc8004 register`` takes repeatable ``--metadata key=value`` flags, so
  registration metadata (including the SDK's injected ``built_with``) is
  atomic with the mint.
- Supported chain keys are ``bsc`` (mainnet) and ``bsctestnet``; the spec's
  ``bsc-testnet`` is rejected by the real CLI with ``CHAIN_UNSUPPORTED``
  (field-verified). This provider rejects non-BSC chains.
- ``sign_typed_data`` raises :class:`UnsupportedWalletOperation` by design
  decision (P0): twak signs ERC-8004/8183/x402 payloads internally via its
  dedicated commands and provides no generic EIP-712 primitive.
- Gas: v0.20.0 added ``--paymaster-url`` on every erc8004/erc8183 write
  (REQ-2 shipped). When the execution context carries a paymaster, its URL is
  forwarded on each write so twak routes the broadcast through the
  SDK-configured sponsorship endpoint (MegaFuel) — this makes sponsored
  bsctestnet writes reachable. Without one, twak keeps its own defaults:
  mainnet sponsors automatically (Trust gateway), bsctestnet self-pays.
"""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlsplit

from eth_account import Account
from eth_account.messages import defunct_hash_message, encode_defunct

from ..config import NETWORKS
from ..core import relay_verifier
from ..core.config import get_env
from ..core.relay_verifier import SecondaryConfirmation
from ..exceptions import RelaySubmissionUnverifiedError, TransactionPendingError
from .capabilities import (
    BROADCAST_SELF,
    INTENTS_ERC8004,
    INTENTS_ERC8183,
)
from .errors import UnsupportedWalletOperation, WalletIdentityMismatch
from .intents import (
    ERC8004_REGISTER,
    ERC8004_SET_AGENT_URI,
    ERC8004_SET_METADATA,
    ERC8183_CLAIM_REFUND,
    ERC8183_COMPLETE,
    ERC8183_CREATE_JOB,
    ERC8183_CREATE_JOB_WITH_TOKEN,
    ERC8183_DISPUTE,
    ERC8183_FUND,
    ERC8183_MARK_EXPIRED,
    ERC8183_REGISTER_JOB,
    ERC8183_REJECT,
    ERC8183_SET_BUDGET,
    ERC8183_SET_PROVIDER,
    ERC8183_SETTLE,
    ERC8183_SUBMIT,
    ERC8183_VOTE_REJECT,
    ExecutionContext,
    Intent,
    IntentExecutor,
)
from .wallet_provider import WalletProvider

logger = logging.getLogger(__name__)


def _is_cli_parser_error(message: str, *, flag: str | None = None) -> bool:
    """Match explicit CLI-parser diagnostics, never transaction/revert text."""
    prefixes = (
        "unknown command",
        "unknown option",
        "unknown argument",
        "unknown flag",
        "unrecognized option",
        "unrecognized argument",
        "unexpected option",
        "unexpected argument",
    )
    for line in message.lower().splitlines():
        detail = line.strip()
        if detail.startswith("twak command failed (") and "): " in detail:
            detail = detail.split("): ", 1)[1]
        if detail.startswith("error:"):
            detail = detail.removeprefix("error:").strip()
        if detail.startswith(prefixes) and (flag is None or flag.lower() in detail):
            return True
    return False


DEFAULT_TWAK_BIN = "twak"
DEFAULT_TIMEOUT = 120  # seconds per CLI invocation
#: twak release line whose ``x402 request`` pre-signing prompt the SDK parses
#: and has verified against the real CLI (tests/test_twak_real_cli.py). Paid
#: requests with ``expected_terms`` refuse other releases unless the provider
#: was built with ``allow_unverified_cli_version=True``.
TWAK_X402_VERIFIED_CLI = "0.20"
# twak v0.20.0's public-receipt wait before it reports NETWORK_ERROR.
_TWAK_RECEIPT_TIMEOUT_SECONDS = 60
_RECEIPT_TIMEOUT_RE = re.compile(r"Timed out waiting for receipt (0x[0-9a-fA-F]{64}) on (\S+)")
_CHAIN_ID_FOR_TWAK_CHAIN = {"bsc": 56, "bsctestnet": 97}


def _parse_receipt_timeout(stdout: str) -> tuple[str, str] | None:
    """(tx hash, chain) of twak's receipt-timeout NETWORK_ERROR envelope."""
    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict) or payload.get("errorCode") != "NETWORK_ERROR":
        return None
    error = payload.get("error")
    match = _RECEIPT_TIMEOUT_RE.fullmatch(error.strip()) if isinstance(error, str) else None
    return (match.group(1), match.group(2)) if match else None


# Appended to command-failure errors: the failure is most often a missing
# one-time setup step, so point the caller at the fix without claiming the
# exact cause (twak's own message is preserved ahead of this).
_SETUP_HINT = (
    "If this is a setup issue, ensure twak is configured: "
    "(1) credentials via `twak init --api-key <id> --api-secret <secret>` "
    "or the TWAK_ACCESS_ID / TWAK_HMAC_SECRET env vars; "
    "(2) a wallet via `twak wallet create --password <pw>`; "
    "(3) the password reachable via TWAK_WALLET_PASSWORD or "
    "`twak wallet keychain save`."
)

# twak chain keys for BNB Smart Chain. ERC-8004/8183 are deployed on both
# mainnet ("bsc") and testnet ("bsctestnet"). Field-verified against twak
# v0.18.0: the spec's "bsc-testnet" key is rejected by the real CLI with
# CHAIN_UNSUPPORTED ('Did you mean "bsc"?') — "bsctestnet" is the key that
# works.
_DEFAULT_CHAIN = "bsc"
_ALLOWED_CHAINS = {"bsc", "bsctestnet"}

# ``wallet sign-message --chain`` is a *key-family* selector (help: "e.g.,
# ethereum, solana"), not a network selector: it accepts "bsc" but rejects
# "bsctestnet" (S-10, field-verified v0.19.0/v0.19.1). EIP-191 carries no
# chain information and the wallet address is identical on both BNB
# networks, so this pin is permanently correct regardless of upstream.
_SIGN_MESSAGE_CHAIN = "bsc"

#: SDK network preset name → twak CLI chain key. The single source of this
#: mapping — consumers (configs, examples, downstream wallet factories)
#: should use it instead of hand-rolling the "bsc-testnet" → "bsctestnet"
#: translation.
TWAK_CHAIN_FOR_NETWORK = {
    "bsc-mainnet": "bsc",
    "bsc-testnet": "bsctestnet",
}


@dataclass(frozen=True)
class TwakPaymentTerms:
    """The payment terms a caller has already validated for one x402 request.

    ``TWAKProvider.x402_request`` compares these with the terms twak prints
    in its pre-signing confirmation prompt: ``asset``, ``network`` and
    ``pay_to`` must match exactly, and ``amount`` is the most twak may sign
    (a lower final amount is confirmed).
    """

    amount: int
    asset: str
    network: str
    pay_to: str


class TwakPaymentTermsMismatchError(RuntimeError):
    """twak was about to sign terms other than the validated ones.

    The provider answered ``n`` to twak's confirmation prompt, so no payment
    authorization was signed or sent. ``field`` is ``"amount"``, ``"asset"``,
    ``"network"``, ``"pay_to"`` or ``"prompt"`` (unrecognised prompt).
    """

    def __init__(self, field: str, expected: str, actual: str) -> None:
        self.field = field
        self.expected = expected
        self.actual = actual
        relation = "exceeds" if field == "amount" else "does not match"
        super().__init__(
            f"twak's payment {field} {actual!r} {relation} the validated {expected!r}; "
            "payment declined before signing"
        )


class TwakPaymentNotSentError(RuntimeError):
    """A paid x402 request failed before twak could send a payment.

    Raised by ``x402_request(expected_terms=...)`` for failures before the
    provider confirmed twak's prompt, and for twak's own "payment cancelled"
    / "Permit2 approval required" outcomes. ``error`` is the underlying
    failure.
    """

    def __init__(self, error: BaseException) -> None:
        self.error = error
        super().__init__(str(error))


class TwakPaymentStateUnknownError(RuntimeError):
    """A paid x402 request failed after the provider confirmed the payment.

    twak may already have sent the signed authorization, so the payment can
    still settle. Reconcile with the merchant before retrying.
    """


# twak v0.20.0 ``x402 request`` without ``--yes`` prints this block to stderr
# (dist/index.js makeReadlineConfirm) and waits for ``y`` on stdin before it
# signs. The token name is server-controlled, so it may not contain newlines
# and every marker must appear exactly once; anything else is declined.
_SIGN_PROMPT_TAIL = "Confirm? [y/N] "
_SIGN_PROMPT_RE = re.compile(
    r"\nSign payment authorization: (?P<amount>\d+) \(atomic\) (?P<token>[^\n]*) "
    r"on (?P<network>[^\s()]+) \((?P<chain>[^\s()]+)\)\n"
    r"Token contract: (?P<asset>0x[0-9a-fA-F]{40})\n"
    r"Recipient:\s+(?P<pay_to>0x[0-9a-fA-F]{40})\n"
    r"Confirm\? \[y/N\] \Z"
)
_SIGN_PROMPT_MARKERS = ("Sign payment authorization:", "Token contract:", "Recipient:")
# Asked after the payment prompt when a Permit2 route has no allowance and
# --auto-approve is off. stdin is closed by then, so twak exits there with no
# output, before broadcasting the approval or signing the permit.
_APPROVAL_PROMPT_TAIL = "Approve unlimited Permit2 allowance? [y/N] "
# A token name can embed a fake prompt tail, so a pipe read that stops inside
# the name would look like a complete prompt. twak writes the prompt in one
# write and then blocks on stdin; answer only once stderr has been quiet this
# long, so the whole block (with any duplicated markers) is checked.
_PROMPT_QUIET_SECONDS = 0.2
# twak error codes that mean the confirmed payment was still not signed.
_NOT_SENT_ERROR_CODES = frozenset({"PAYMENT_CANCELLED", "PERMIT2_APPROVAL_REQUIRED"})
_MAY_HAVE_PAID = (
    "the payment was confirmed and may have been sent, so the session budget "
    "stays reserved; reconcile with the merchant before retrying"
)


def _error_code(stdout: str) -> str | None:
    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError:
        return None
    code = payload.get("errorCode") if isinstance(payload, dict) else None
    return code if isinstance(code, str) else None


def _check_sign_prompt(
    text: str, expected: TwakPaymentTerms
) -> TwakPaymentTermsMismatchError | None:
    """Return None if twak's pending prompt shows ``expected`` (the amount may be lower)."""
    match = _SIGN_PROMPT_RE.search(text)
    if match is None or any(text.count(marker) != 1 for marker in _SIGN_PROMPT_MARKERS):
        return TwakPaymentTermsMismatchError(
            "prompt", "twak v0.20.0 confirmation prompt", text[-300:]
        )
    if int(match["amount"]) > expected.amount:
        return TwakPaymentTermsMismatchError("amount", str(expected.amount), match["amount"])
    shown = {
        "asset": (expected.asset.lower(), match["asset"].lower()),
        "network": (expected.network.lower(), match["network"].lower()),
        "pay_to": (expected.pay_to.lower(), match["pay_to"].lower()),
    }
    for field, (want, got) in shown.items():
        if want != got:
            return TwakPaymentTermsMismatchError(field, want, got)
    return None


_ZERO_ADDRESS = "0x" + "00" * 20
_ZERO_REASON = b"\x00" * 32
_SENSITIVE_QUERY_KEYS = {
    "apikey",
    "api_key",
    "access_token",
    "auth",
    "authorization",
    "credential",
    "key",
    "password",
    "secret",
    "signature",
    "token",
}


def _reject_sensitive_argv_url(url: str) -> None:
    """Refuse credentials that TWAK would expose through process argv."""
    parsed = urlsplit(url)
    if parsed.username is not None or parsed.password is not None:
        raise ValueError(
            "TWAK x402 URLs must not contain userinfo credentials; the CLI "
            "receives URLs through process argv"
        )
    for key, _value in parse_qsl(parsed.query, keep_blank_values=True):
        if key.lower() in _SENSITIVE_QUERY_KEYS:
            raise ValueError(
                f"TWAK x402 URL query parameter {key!r} may contain credentials; "
                "the CLI receives URLs through process argv"
            )


_NETWORK_FOR_TWAK_CHAIN = {
    "bsc": "bsc-mainnet",
    "bsctestnet": "bsc-testnet",
}

_CONTRACT_FIELD_BY_INTENT = {
    ERC8004_REGISTER: "registry_contract",
    ERC8004_SET_METADATA: "registry_contract",
    ERC8004_SET_AGENT_URI: "registry_contract",
    ERC8183_CREATE_JOB: "commerce_contract",
    ERC8183_CREATE_JOB_WITH_TOKEN: "commerce_contract",
    ERC8183_SET_PROVIDER: "commerce_contract",
    ERC8183_SET_BUDGET: "commerce_contract",
    ERC8183_FUND: "commerce_contract",
    ERC8183_SUBMIT: "commerce_contract",
    ERC8183_COMPLETE: "commerce_contract",
    ERC8183_REJECT: "commerce_contract",
    ERC8183_CLAIM_REFUND: "commerce_contract",
    ERC8183_REGISTER_JOB: "router_contract",
    ERC8183_SETTLE: "router_contract",
    ERC8183_MARK_EXPIRED: "router_contract",
    ERC8183_DISPUTE: "policy_contract",
    ERC8183_VOTE_REJECT: "policy_contract",
}


class TWAKProvider(WalletProvider, IntentExecutor):
    """Wallet + execution backend delegating to the ``twak`` CLI.

    Args:
        chain: twak chain key for BNB Smart Chain — ``"bsc"`` (mainnet) or
            ``"bsctestnet"``. ERC-8004/8183 are deployed on both. The value
            is passed through to twak verbatim. (Note: the spec's
            ``bsc-testnet`` spelling is rejected by the real CLI.)
        twak_bin: Path to (or name of) the ``twak`` executable.
        timeout: Per-command timeout in seconds.
        home: When set, every twak subprocess runs with ``HOME=<home>`` so
            twak resolves its state under ``<home>/.twak`` instead of the OS
            user's home (Node's ``os.homedir()`` reads ``$HOME`` —
            field-verified). This solves three deployments: read-only code
            mounts (AgentCore — materialize into a writable dir and point
            ``home`` at it), multi-agent isolation on one OS user (twak is
            otherwise one-wallet-per-OS-user), and test isolation. Tracked
            upstream as gaps S-5: when twak ships a native ``TWAK_HOME``,
            the implementation switches to it with this signature unchanged.
            ``None`` inherits the environment untouched.
        expected_address: Pin the wallet's on-chain identity. On the first
            successful address lookup the reported address is compared
            case-insensitively; a mismatch raises
            :class:`~bnbagent.wallets.errors.WalletIdentityMismatch` before
            any state-changing operation can proceed (INV-4).
        auto_create: When ``True`` (default, dev-machine parity with
            ``EVMWalletProvider``) a missing wallet is created lazily on the
            first operation. Deployments must pass ``False``: the wallet may
            only come from materialization
            (:func:`~bnbagent.wallets.twak_custody.materialize_twak_home`,
            fed from the ``TWAK_WALLET_JSON`` bundle key) and a missing
            wallet raises instead of silently minting a new on-chain
            identity (INV-4).
        confirm_tx_unseen: Secondary-confirmation seam for sponsored receipt
            timeouts (tests replace the real multi-RPC probe). Default:
            :func:`~bnbagent.core.relay_verifier.confirm_tx_unseen`.
        allow_unverified_cli_version: Confirm x402 payments with a twak
            release other than :data:`TWAK_X402_VERIFIED_CLI`. Off by
            default: the payment check reads twak's pre-signing prompt, whose
            wording or meaning a new release may change. A prompt that no
            longer parses is still declined either way. Setting
            ``BNBAGENT_TWAK_ALLOW_UNVERIFIED_CLI=1`` has the same effect
            without a code change.

    Like ``EVMWalletProvider``, this provider auto-creates a wallet when none
    exists — see :meth:`_ensure_wallet` / :meth:`create_wallet` (disable with
    ``auto_create=False``). The check (and any creation) happens lazily on the
    **first** operation rather than at construction, so building the provider
    stays side-effect-free (constructing it, reading ``kind``, etc. never
    shell out). Creation needs the API credentials and wallet password already
    reachable by ``twak`` (``twak init`` / ``TWAK_ACCESS_ID`` +
    ``TWAK_HMAC_SECRET``; ``TWAK_WALLET_PASSWORD``
    / keychain); when they are missing the operation blocks with a clear error.

    Raises:
        ValueError: If ``chain`` is not ``"bsc"`` / ``"bsctestnet"``.
    """

    kind = "twak"
    # twak's `fund` does approve + deposit itself, so the SDK facade skips
    # its own allowance top-up for this wallet.
    fund_bundles_approval = True
    # sign.message derives automatically from the override below; twak has
    # no sign_transaction / sign_typed_data, so the base defaults raise.
    # x402.pay stays undeclared until TwakX402Payer implements request_exact.
    # quote/request remain available via make_x402_payer.
    _extra_capabilities = frozenset({BROADCAST_SELF, INTENTS_ERC8004, INTENTS_ERC8183})

    def __init__(
        self,
        *,
        chain: str = _DEFAULT_CHAIN,
        twak_bin: str = DEFAULT_TWAK_BIN,
        timeout: int = DEFAULT_TIMEOUT,
        home: str | Path | None = None,
        expected_address: str | None = None,
        auto_create: bool = True,
        allow_unverified_cli_version: bool = False,
        confirm_tx_unseen: Callable[..., SecondaryConfirmation] | None = None,
    ):
        if chain.lower() not in _ALLOWED_CHAINS:
            raise ValueError(
                f"TWAKProvider supports BNB Smart Chain only — chain must be "
                f"one of {sorted(_ALLOWED_CHAINS)} (got chain={chain!r}). "
                "ERC-8004/8183 are deployed on bsc (mainnet) and bsctestnet "
                "(note: twak's testnet key is 'bsctestnet', not 'bsc-testnet')."
            )
        self._chain = chain.lower()
        self._twak_bin = twak_bin
        self._timeout = timeout
        self._home = Path(home) if home is not None else None
        self._expected_address = expected_address
        self._auto_create = auto_create
        self._allow_unverified_cli_version = allow_unverified_cli_version
        self._cli_verified = False
        self._confirm_tx_unseen = confirm_tx_unseen or relay_verifier.confirm_tx_unseen
        # Public web3 from the ExecutionContext, used only to reconcile
        # sponsored receipt timeouts.
        self._web3: Any = None
        self._address: str | None = None
        self._ensured = False  # guards the one-shot lazy auto-create
        # Captured from the ExecutionContext (make_executor); forwarded to
        # every write command as --paymaster-url (twak v0.20.0, REQ-2).
        self._paymaster_url: str | None = None

    # ── subprocess plumbing ──

    def _run(self, args: list[str], *, paid_body: bool = False) -> Any:
        """Run ``twak <args> --json`` and return the parsed JSON object.

        Failure = non-zero exit, a truthy ``error`` field, or ``success`` set
        to false. The *absence* of ``success`` is never trusted as success —
        the real CLI omits the field inconsistently across error envelopes
        (field-verified on v0.18.0).

        When ``home`` was set on the provider, the subprocess runs with
        ``HOME=<home>`` (full environment otherwise inherited) so twak
        resolves ``<home>/.twak`` — the single env override point for every
        invocation. With ``home=None`` the environment is inherited untouched.

        Raises:
            RuntimeError: If the binary is missing, the command exits
                non-zero, the output is not JSON, or the envelope reports
                an error.
        """
        cmd = [self._twak_bin, *args, "--json"]
        env = {**os.environ, "HOME": str(self._home)} if self._home is not None else None
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=self._timeout,
                env=env,
            )
        except FileNotFoundError as e:
            raise RuntimeError(
                f"twak CLI not found (looked for {self._twak_bin!r}). "
                "Install it with `npm install -g @trustwallet/cli`, then "
                "configure it (see TWAKProvider prerequisites)."
            ) from e
        except subprocess.TimeoutExpired as e:
            raise RuntimeError(
                f"twak command timed out after {self._timeout}s: {_redact(cmd)}"
            ) from e
        if paid_body:
            return self._paid_body(cmd, proc.returncode, proc.stdout, proc.stderr)
        timeout = _parse_receipt_timeout(proc.stdout) if self._paymaster_url else None
        if timeout is not None and timeout[1] == self._chain:
            raise self._classify_sponsored_receipt_timeout(timeout[0])
        return self._interpret(cmd, proc.returncode, proc.stdout, proc.stderr)

    def _classify_sponsored_receipt_timeout(self, tx_hash: str) -> Exception:
        """Tell "visible but not mined" from "the relay hash is nowhere".

        twak gave up waiting for the receipt of a sponsored write. If the
        public chain (or two independent fallback RPCs) can see the hash, the
        write is pending: waiting is right, re-sending is not. Otherwise the
        relay may never have broadcast it: do not retry blindly.
        """
        visible = False
        try:
            visible = (
                self._web3 is not None and self._web3.eth.get_transaction(tx_hash) is not None
            )
        except Exception:  # noqa: BLE001 - not found or RPC failure: not visible
            visible = False
        secondary: SecondaryConfirmation | str = "not-checked"
        if not visible:
            try:
                secondary = self._confirm_tx_unseen(
                    tx_hash, chain_id=_CHAIN_ID_FOR_TWAK_CHAIN[self._chain]
                )
            except Exception:  # noqa: BLE001 - treat a failing probe as no quorum
                secondary = "inconclusive"
            visible = secondary == "seen"
        if visible:
            pending = TransactionPendingError(
                tx_hash,
                _TWAK_RECEIPT_TIMEOUT_SECONDS,
                f"TWAK sponsored transaction {tx_hash} is visible on the public chain, but "
                "TWAK timed out waiting for its receipt. Do not retry until the transaction "
                "or job state is reconciled.",
            )
            pending.relay_status = "receipt_timeout_but_tx_visible"
            return pending
        unverified = RelaySubmissionUnverifiedError(
            tx_hash,
            _TWAK_RECEIPT_TIMEOUT_SECONDS,
            f"TWAK sponsored relay returned transaction {tx_hash}, but the SDK could not "
            "verify it on the public chain after TWAK timed out waiting for its receipt. "
            "Do not retry blindly; reconcile the relay transaction and wallet nonce before "
            "issuing another write.",
        )
        if secondary in ("confirmed-unseen", "inconclusive"):
            unverified.secondary_rpc_result = secondary
        return unverified

    def _paid_body(self, cmd: list[str], returncode: int, stdout: str, stderr: str) -> Any:
        """An ``x402 request`` result: exit 0 is the paid endpoint's body verbatim.

        The body may be any JSON (object, array, null, string) and may carry
        its own ``error`` / ``success`` fields; they belong to the endpoint,
        not to twak, whose own failures always exit non-zero.
        """
        if returncode != 0:
            raise RuntimeError(self._format_error(cmd, stdout, stderr))
        try:
            return json.loads(stdout) if stdout.strip() else None
        except json.JSONDecodeError:
            return stdout

    def _interpret(
        self, cmd: list[str], returncode: int, stdout: str, stderr: str
    ) -> dict[str, Any]:
        """Parse one finished invocation with the field-verified envelope quirks."""
        proc = subprocess.CompletedProcess(cmd, returncode, stdout, stderr)
        if proc.returncode != 0:
            # Quirk (field-verified v0.18.0): `x402 quote` exits non-zero on an
            # empty `accepts` list while emitting an explicit success envelope
            # ({"success": true}, no "error"). When the structured envelope and
            # the exit code disagree in the *success* direction, trust the
            # envelope — an error envelope or unparseable output still raises.
            try:
                data = json.loads(proc.stdout) if proc.stdout.strip() else {}
            except json.JSONDecodeError:
                data = {}
            if data.get("success") is True and not data.get("error"):
                return data
            raise RuntimeError(self._format_error(cmd, proc.stdout, proc.stderr))

        try:
            data = json.loads(proc.stdout) if proc.stdout.strip() else {}
        except json.JSONDecodeError as e:
            raise RuntimeError(
                f"twak returned non-JSON output for {_redact(cmd)}: {proc.stdout[:500]!r}"
            ) from e

        if data.get("error") or data.get("success") is False:
            raise RuntimeError(self._format_error(cmd, proc.stdout, proc.stderr))
        return data

    def _require_verified_cli(self) -> None:
        """Refuse a twak release whose x402 prompt the SDK was not verified on."""
        if (
            self._cli_verified
            or self._allow_unverified_cli_version
            or get_env("BNBAGENT_TWAK_ALLOW_UNVERIFIED_CLI") == "1"
        ):
            return
        cmd = [self._twak_bin, "--version"]
        env = {**os.environ, "HOME": str(self._home)} if self._home is not None else None
        try:
            proc = subprocess.run(
                cmd, capture_output=True, text=True, timeout=self._timeout, env=env
            )
        except FileNotFoundError as e:
            raise RuntimeError(f"twak CLI not found (looked for {self._twak_bin!r})") from e
        except subprocess.TimeoutExpired as e:
            raise RuntimeError(f"twak --version timed out after {self._timeout}s") from e
        found = re.search(r"\d+\.\d+\.\d+", proc.stdout)
        version = found.group(0) if found else (proc.stdout.strip() or "unknown")
        if not re.fullmatch(rf"{re.escape(TWAK_X402_VERIFIED_CLI)}\.\d+", version):
            raise RuntimeError(
                f"twak {version} is not verified for x402 payments: the SDK checks "
                f"twak's pre-signing prompt and was verified on twak "
                f"{TWAK_X402_VERIFIED_CLI}.x. Install @trustwallet/cli@"
                f"{TWAK_X402_VERIFIED_CLI} or a bnbagent release verified on "
                f"{version}; allow_unverified_cli_version=True or "
                "BNBAGENT_TWAK_ALLOW_UNVERIFIED_CLI=1 tries anyway (a prompt that no "
                "longer parses is still declined)."
            )
        self._cli_verified = True

    def _run_x402_confirmed(
        self,
        args: list[str],
        expected: TwakPaymentTerms,
        on_confirm: Callable[[TwakPaymentTerms], None] | None = None,
    ) -> dict[str, Any]:
        """Run ``twak <args> --json``, answering its pre-signing prompt on stdin.

        twak prints the final payment terms and waits for ``y`` before it
        signs. The provider answers ``y`` only if they equal ``expected``;
        otherwise (or for an unrecognised prompt) it answers ``n`` and raises
        :class:`TwakPaymentTermsMismatchError`, so nothing is signed. stdin is
        closed right after the answer: twak cannot be confirmed twice, and it
        exits once the request is done.

        Exit 0 returns the endpoint body verbatim (any JSON). Failures raise
        :class:`TwakPaymentNotSentError` when no payment can have left twak
        (before the ``y``, twak reports it cancelled / needing Permit2
        approval, or the verified twak release exited 0 without asking) and
        :class:`TwakPaymentStateUnknownError` otherwise.
        """
        cmd = [self._twak_bin, *args, "--json"]
        env = {**os.environ, "HOME": str(self._home)} if self._home is not None else None
        try:
            proc = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=env,
            )
        except FileNotFoundError as e:
            missing = RuntimeError(
                f"twak CLI not found (looked for {self._twak_bin!r}). "
                "Install it with `npm install -g @trustwallet/cli`, then "
                "configure it (see TWAKProvider prerequisites)."
            )
            raise TwakPaymentNotSentError(missing) from e

        stdout_chunks: list[bytes] = []
        stderr_buf = bytearray()
        changed = threading.Condition()

        def pump(stream: Any, sink: Any) -> None:
            for chunk in iter(lambda: os.read(stream.fileno(), 4096), b""):
                with changed:
                    sink(chunk)
                    changed.notify_all()
            with changed:
                changed.notify_all()

        readers = [
            threading.Thread(target=pump, args=(proc.stdout, stdout_chunks.append), daemon=True),
            threading.Thread(target=pump, args=(proc.stderr, stderr_buf.extend), daemon=True),
        ]
        for reader in readers:
            reader.start()

        def close_stdin(reply: bytes = b"") -> None:
            try:
                proc.stdin.write(reply)
                proc.stdin.close()
            except OSError:
                pass

        deadline = time.monotonic() + self._timeout
        mismatch: TwakPaymentTermsMismatchError | None = None
        confirmed = False
        pending_len, pending_since = 0, 0.0
        try:
            while True:
                with changed:
                    finished = proc.poll() is not None and not any(r.is_alive() for r in readers)
                    if not finished:
                        changed.wait(timeout=0.05)
                    text = stderr_buf.decode("utf-8", errors="replace")
                if finished:
                    break
                now = time.monotonic()
                if now > deadline:
                    proc.kill()
                    timed_out = f"twak command timed out after {self._timeout}s: {_redact(cmd)}"
                    if confirmed:
                        raise TwakPaymentStateUnknownError(f"{timed_out}; {_MAY_HAVE_PAID}")
                    raise TwakPaymentNotSentError(RuntimeError(timed_out))
                if proc.stdin.closed or not text.endswith(_SIGN_PROMPT_TAIL):
                    continue
                if len(text) != pending_len:
                    pending_len, pending_since = len(text), now
                elif now - pending_since >= _PROMPT_QUIET_SECONDS:
                    mismatch = _check_sign_prompt(text, expected)
                    if mismatch is None and on_confirm is not None:
                        signed = _SIGN_PROMPT_RE.search(text)
                        on_confirm(replace(expected, amount=int(signed["amount"])))
                    confirmed = mismatch is None
                    close_stdin(b"y\n" if confirmed else b"n\n")
        finally:
            if not proc.stdin.closed:
                close_stdin()
            proc.wait()
            for reader in readers:
                reader.join(timeout=1)

        if mismatch is not None:
            raise mismatch
        stdout = b"".join(stdout_chunks).decode("utf-8", errors="replace")
        stderr = stderr_buf.decode("utf-8", errors="replace")
        if proc.returncode == 0 and not stdout.strip() and stderr.endswith(_APPROVAL_PROMPT_TAIL):
            raise TwakPaymentNotSentError(
                RuntimeError(
                    "twak stopped at its Permit2 approval prompt, so nothing was signed; "
                    "approve Permit2 for this token first or pass auto_approve=True"
                )
            )
        # The verified release signs only after this prompt was answered, so an
        # exit 0 that never asked paid nothing. Another release might sign
        # without asking; there the body stays a payment and keeps the budget.
        if proc.returncode == 0 and not confirmed and self._cli_verified:
            raise TwakPaymentNotSentError(
                RuntimeError(
                    "twak returned the endpoint's response without asking to sign a "
                    "payment (the paid request got no 402 challenge), so nothing was "
                    "paid and the response is not returned as a paid x402 result"
                )
            )
        if proc.returncode == 0:
            return self._paid_body(cmd, proc.returncode, stdout, stderr)
        failure = RuntimeError(self._format_error(cmd, stdout, stderr))
        if not confirmed or _error_code(stdout) in _NOT_SENT_ERROR_CODES:
            raise TwakPaymentNotSentError(failure)
        raise TwakPaymentStateUnknownError(f"{failure}; {_MAY_HAVE_PAID}")

    @staticmethod
    def _format_error(cmd: list[str], stdout: str, stderr: str) -> str:
        """Build a helpful error message, surfacing any structured error."""
        detail = stderr.strip() or stdout.strip()
        try:
            payload = json.loads(stdout)
            err = payload.get("error")
            if isinstance(err, dict):
                detail = err.get("message") or err.get("name") or err.get("selector") or detail
            elif isinstance(err, str):
                detail = err
        except (json.JSONDecodeError, AttributeError):
            pass
        # "unknown command/option" means the installed twak predates the
        # command surface this provider targets — point at the upgrade, not
        # at the (irrelevant) setup steps.
        combined = f"{stderr}\n{stdout}"
        if _is_cli_parser_error(combined):
            hint = (
                "The installed twak CLI does not recognise this command/option "
                "— upgrade twak to >= v0.20.0 (`npm install -g @trustwallet/cli`)."
            )
        else:
            hint = _SETUP_HINT
        return f"twak command failed ({_redact(cmd)}): {detail or '<no output>'}. {hint}"

    @staticmethod
    def _extract_tx_hash(data: dict[str, Any]) -> str | None:
        """Pull the tx hash out of a twak result envelope, tolerantly.

        The spec says ``txHash`` but the real CLI returns ``hash``
        (gaps REQ-3) — try the field-verified name first.
        """
        return data.get("hash") or data.get("txHash") or data.get("transactionHash")

    @staticmethod
    def _split_signature(signature: str) -> dict[str, Any]:
        """Split a 65-byte 0x signature into r / s / v components."""
        sig = signature[2:] if signature.startswith("0x") else signature
        if len(sig) != 130:
            return {"r": None, "s": None, "v": None}
        return {
            "r": int(sig[0:64], 16),
            "s": int(sig[64:128], 16),
            "v": int(sig[128:130], 16),
        }

    # ── WalletProvider ──

    @property
    def address(self) -> str:
        """The twak wallet address (cached after first lookup)."""
        self._ensure_wallet()
        if self._address is None:
            self._lookup_address()
        return self._address

    def _lookup_address(self) -> None:
        """Resolve the wallet address from the CLI and cache it.

        The ``expected_address`` identity check lives here, on the first
        successful lookup and **before** the cache is populated: on a
        mismatch nothing is cached, so every subsequent attempt re-checks
        and re-raises instead of operating under a drifted identity.
        """
        data = self._run(["wallet", "address", "--chain", self._chain])
        addr = data.get("address") or data.get("wallet")
        if not addr:
            raise RuntimeError(f"twak `wallet address` did not return an address: {data!r}")
        if self._expected_address is not None and addr.lower() != self._expected_address.lower():
            raise WalletIdentityMismatch(expected=self._expected_address, actual=addr)
        self._address = addr

    @property
    def key_location(self) -> str | None:
        """twak owns custody; the key never lives in the SDK's store.

        The encrypted BIP39 mnemonic lives in ``<home>/.twak/wallet.json``
        (``~`` unless a custom ``home`` was set); the signing password is
        resolved from ``TWAK_WALLET_PASSWORD`` or the OS keychain (API
        credentials are separate, in ``<home>/.twak/credentials.json``).
        """
        base = str(self._home) if self._home is not None else "~"
        return (
            f"{base}/.twak/wallet.json (encrypted by the twak CLI) "
            "+ OS keychain/TWAK_WALLET_PASSWORD"
        )

    def exists(self) -> bool:
        """True if twak reports a configured wallet (best-effort).

        Probes ``twak wallet status``. The CLI exits 0 even when no wallet is
        configured (field-verified v0.18.0: ``{"agentWallet": "not
        configured"}``), so the exit code alone is a false positive — the
        ``agentWallet`` field is the actual signal. Any failure (missing
        binary, non-zero exit) is treated as "does not exist" rather than
        raising.
        """
        try:
            data = self._run(["wallet", "status"])
            return data.get("agentWallet") == "configured"
        except Exception:  # noqa: BLE001 - introspection must not raise
            return False

    def create_wallet(
        self, *, skip_password_check: bool = False, no_keychain: bool = False
    ) -> str:
        """Create a twak wallet if none exists yet (idempotent).

        Mirrors :meth:`EVMWalletProvider`'s auto-create. Consistent with the
        rest of this provider, the password is **never** passed on the command
        line — twak resolves it from ``TWAK_WALLET_PASSWORD`` or the OS
        keychain. API credentials must already be configured (``twak init`` /
        env). Returns the wallet address.

        Args:
            skip_password_check: Pass ``--skip-password-check`` (test only —
                bypasses twak's password-strength rules).
            no_keychain: Pass ``--no-keychain`` (do not store the password in
                the OS keychain).
        """
        if self.exists():
            return self.address
        args = ["wallet", "create"]
        if skip_password_check:
            args.append("--skip-password-check")
        if no_keychain:
            args.append("--no-keychain")
        try:
            self._run(args)
        except RuntimeError as e:
            # Field-verified v0.18.0: `wallet create` hard-requires --password
            # on the command line; TWAK_WALLET_PASSWORD only unlocks existing
            # wallets. This provider never puts secrets on argv (visible in
            # `ps`), so creation cannot proceed programmatically until twak
            # honors the env var at creation time (gaps S-8). Point the caller
            # at the manual path instead of surfacing a cryptic CLI error.
            # Match commander's exact failure shape ("required option
            # '--password ...' not specified") — both fragments must be
            # present, since the generic _SETUP_HINT also mentions --password.
            detail = str(e).lower()
            if "required option" in detail and "--password" in detail:
                raise RuntimeError(
                    "twak v0.18.0 requires the wallet password on the command "
                    "line for `wallet create`, which this SDK refuses to do "
                    "(secrets on argv are visible to every process). Create "
                    "the wallet manually once — `twak wallet create "
                    "--password <pw>` in an interactive shell — or "
                    "materialize an existing wallet.json via "
                    "materialize_twak_home()."
                ) from e
            raise
        self._address = None  # invalidate cache so the new address is re-read
        return self.address

    def _ensure_wallet(self) -> None:
        """One-shot lazy auto-create (or existence check) on the first operation.

        Runs the probe at most once per instance: checks for an existing
        wallet and creates one if absent (EVM-parity) — unless
        ``auto_create=False``, in which case a missing wallet raises instead
        of silently minting a new on-chain identity (deployment mode, INV-4).
        When the wallet already exists this costs a single ``wallet status``
        probe; creation, when needed, blocks until twak finishes (or errors
        clearly if credentials / password are not configured).

        When ``expected_address`` is pinned, this also forces the first
        address lookup (which performs the identity check, see
        :meth:`_lookup_address`) so the check runs before *any*
        state-changing operation: every twak write path — ``execute()``,
        ``sign_message()``, ``x402_request()`` — calls ``_ensure_wallet()``
        before touching the CLI. This re-runs (cheaply once verified) until
        the check passes, so a mismatch keeps blocking every operation.
        """
        if not self._ensured:
            self._ensured = True
            if not self.exists():
                if not self._auto_create:
                    raise RuntimeError(
                        "twak wallet not found and auto_create=False "
                        "(deployment mode): refusing to create a new on-chain "
                        "identity implicitly (INV-4). Materialize the wallet "
                        "first — materialize_twak_home(wallet_json=..., "
                        "home=...) with the TWAK_WALLET_JSON secret-bundle "
                        "value — then retry."
                    )
                self.create_wallet()
        if self._expected_address is not None and self._address is None:
            self._lookup_address()  # identity check before any operation

    def sign_message(self, message: str) -> dict[str, Any]:
        """Sign a message via ``twak wallet sign-message`` (EIP-191).

        Three adaptations over the raw CLI output (gaps S-4):
        1. the signature is normalised to a ``0x`` prefix;
        2. the EIP-191 digest is computed client-side (the CLI returns none);
        3. the signer is recovered from the digest + signature and checked
           against the wallet address — we compute the digest ourselves but
           twak produced the signature, so a recovery round-trip is the only
           runtime proof both sides agree on the message bytes.

        Chain key: ``sign-message``'s ``--chain`` selects the *key family*
        (its help says "e.g., ethereum, solana"), not the network — it
        accepts ``bsc`` but rejects ``bsctestnet`` (S-10, field-verified on
        v0.19.0 and still present on v0.19.1). EIP-191 signing is
        chain-agnostic and the wallet address is identical on both BNB
        networks, so we always pass :data:`_SIGN_MESSAGE_CHAIN`; the recovery
        self-check below guards any key drift. This pin stays correct even
        if the CLI later accepts ``bsctestnet``.

        Encoding: the message is passed verbatim — twak >= v0.19.1 signs the
        input as text always ("input is always text", upstream fix for the
        S-11 regression), matching the SDK's text semantics
        (``EVMWalletProvider`` signs ``encode_defunct(text=...)``). On the
        excluded versions (<= v0.19.0) a ``0x``-shaped message — e.g. a
        negotiation hash — was hex-decoded and signed as raw bytes; the
        self-check below catches that divergence and names it.
        """
        self._ensure_wallet()
        data = self._run(
            ["wallet", "sign-message", "--chain", _SIGN_MESSAGE_CHAIN, "--message", message]
        )
        signature = data.get("signature")
        if not signature:
            raise RuntimeError(f"twak `sign-message` returned no signature: {data!r}")
        if not signature.startswith("0x"):
            signature = "0x" + signature
        digest = "0x" + bytes(defunct_hash_message(text=message)).hex()
        try:
            recovered = Account.recover_message(encode_defunct(text=message), signature=signature)
        except Exception as e:
            raise RuntimeError(
                f"twak `sign-message` returned a malformed signature ({signature[:20]}...): {e}"
            ) from e
        if recovered.lower() != self.address.lower():
            raise RuntimeError(
                f"twak sign-message self-check failed: signature recovers to "
                f"{recovered}, expected the wallet address {self.address}. "
                "The SDK computes the EIP-191 digest client-side while twak "
                "signs out of process — a recovery mismatch means the two "
                "sides encoded the message bytes differently, and using this "
                "signature would fail verification later. Refusing to return it. "
                "Known cause: twak <= v0.19.0 hex-decodes a 0x-shaped message "
                "and signs the raw bytes (fixed in v0.19.1). Fix: upgrade "
                "twak to >= v0.20.0 (`npm install -g @trustwallet/cli`), or "
                "switch this agent to WALLET_KIND=evm (see docs/twak.md)."
            )
        return {
            "messageHash": digest,
            "signature": signature,
            **self._split_signature(signature),
        }

    # sign_transaction / sign_typed_data are deliberately NOT overridden:
    # twak exposes no raw-tx or generic EIP-712 primitive (v0.18.0,
    # field-verified — design decision P0: no CLI call is ever attempted).
    # The base-class defaults raise a descriptive UnsupportedWalletOperation,
    # and not overriding keeps sign.transaction / sign.typed_data out of
    # capabilities() (overriding only to raise would falsely claim them).

    # ── x402 raw transport (policy lives in TwakX402Payer) ──

    def x402_quote(
        self, url: str, *, method: str = "GET", body: str | None = None
    ) -> dict[str, Any]:
        """Fetch the x402 payment challenge for ``url`` (read-only, no payment).

        Runs ``twak x402 quote <url> [--method M] [--body B] --json`` and
        returns the parsed JSON challenge verbatim.
        """
        _reject_sensitive_argv_url(url)
        # F-3: deliberately NO _ensure_wallet() here. A quote is a read-only
        # challenge fetch that needs no wallet — calling _ensure_wallet would
        # let a mere price check silently auto-create a wallet (INV-4).
        args = ["x402", "quote", url]
        if method != "GET":
            args += ["--method", method]
        if body is not None:
            args += ["--body", body]
        return self._run(args)

    def x402_request(
        self,
        url: str,
        *,
        max_payment: int | str,
        method: str = "GET",
        body: str | None = None,
        prefer_network: str | None = None,
        prefer_method: str | None = None,
        prefer_asset: str | None = None,
        auto_approve: bool = False,
        expected_terms: TwakPaymentTerms | None = None,
        on_confirm: Callable[[TwakPaymentTerms], None] | None = None,
    ) -> Any:
        """Make a paid x402 request via ``twak x402 request`` (twak pays).

        ``max_payment`` is required: it is the hard per-payment cap twak
        itself enforces (the wallet-layer backstop under the payer's policy
        checks). The ``--prefer-*`` flags narrow which challenge entry twak
        picks (TOCTOU backstop between a prior quote and this request).

        With ``expected_terms`` (what :class:`~bnbagent.x402.TwakX402Payer`
        always passes), ``--yes`` is omitted: twak re-fetches the challenge
        and shows the final amount, token contract, network and recipient
        before signing, and the provider confirms only if the recipient,
        token contract and network equal ``expected_terms`` and the amount is
        not above it (raising :class:`TwakPaymentTermsMismatchError`
        otherwise). ``on_confirm`` receives the terms twak is about to sign,
        with the final amount, just before the provider answers ``y``.
        Without ``expected_terms``, twak auto-confirms whatever the re-fetched
        challenge asks for, up to ``max_payment``.

        Exit 0 returns the endpoint body verbatim (any JSON, including bodies
        with their own ``error`` field). With ``expected_terms``, failures
        raise :class:`TwakPaymentNotSentError` (wrapping the cause) when no
        payment can have been sent, or :class:`TwakPaymentStateUnknownError`
        once the payment was confirmed.
        """
        _reject_sensitive_argv_url(url)
        if expected_terms is None:
            self._ensure_wallet()
        else:
            try:
                self._ensure_wallet()
                self._require_verified_cli()
            except Exception as e:
                raise TwakPaymentNotSentError(e) from e
        args = ["x402", "request", url, "--max-payment", str(max_payment)]
        if expected_terms is None:
            args.append("--yes")
        if method != "GET":
            args += ["--method", method]
        if body is not None:
            args += ["--body", body]
        if prefer_network:
            args += ["--prefer-network", prefer_network]
        if prefer_method:
            args += ["--prefer-method", prefer_method]
        if prefer_asset:
            args += ["--prefer-asset", prefer_asset]
        if auto_approve:
            args.append("--auto-approve")
        # stdout is pure JSON; the human-readable "x402: paying ..." banner
        # goes to stderr (field-verified) — _run parses stdout only, never a
        # merged stream. On success the JSON is the paid endpoint's response
        # body verbatim, with NO payment-receipt metadata (gaps S-7).
        if expected_terms is None:
            return self._run(args, paid_body=True)
        return self._run_x402_confirmed(args, expected_terms, on_confirm)

    def make_x402_payer(self, **payer_kwargs: Any):
        """Delegated x402 payer: twak builds/signs/settles the payment.

        ``payer_kwargs`` are forwarded verbatim to ``TwakX402Payer``
        (same pass-through convention as ``create_wallet_provider``).
        """
        # Lazy import: keeps the wallet layer import-independent of x402.
        from ..x402.twak import TwakX402Payer

        return TwakX402Payer(self, **payer_kwargs)

    # ── IntentExecutor ──

    def make_executor(self, context: ExecutionContext) -> IntentExecutor:
        """This wallet broadcasts its own transactions, so it *is* its own
        executor. The web3 connection is kept only to reconcile sponsored
        receipt timeouts. A paymaster IS honoured (twak v0.20.0, gaps REQ-2
        shipped): its URL is captured here and forwarded to every write
        command as ``--paymaster-url``, so twak routes the broadcast through
        the SDK-configured sponsorship endpoint.
        Because the executor is the provider itself, the latest
        ``make_executor`` context wins — the supported shape is one client
        per provider (which is how the facades construct them)."""
        paymaster = context.paymaster
        url = getattr(paymaster, "paymaster_url", None) if paymaster else None
        if paymaster is not None and not url:
            logger.warning(
                "TWAKProvider: context.paymaster has no paymaster_url — "
                "ignoring it. twak sponsorship needs a URL to forward as "
                "--paymaster-url (v0.20.0)."
            )
        self._paymaster_url = url
        self._web3 = context.web3
        return self

    def execute(self, intent: Intent) -> dict[str, Any]:
        """Execute a high-level intent by delegating to the twak CLI."""
        # Validate first so an unsupported intent never triggers wallet
        # creation or any CLI call.
        handler = self._INTENT_HANDLERS.get(intent.name)
        if handler is None:
            supported = ", ".join(sorted(self._INTENT_HANDLERS))
            raise UnsupportedWalletOperation(
                f"intent {intent.name!r}",
                reason=(
                    "twak cannot execute arbitrary contract calls — it only "
                    f"speaks a fixed command menu (supported intents: {supported})"
                ),
                alternative="use an EVM wallet for arbitrary contract calls",
            )
        actual_target = getattr(intent.call, "address", None)
        contract_field = _CONTRACT_FIELD_BY_INTENT.get(intent.name)
        if isinstance(actual_target, str) and actual_target and contract_field:
            network_name = _NETWORK_FOR_TWAK_CHAIN[self._chain]
            canonical_target = getattr(NETWORKS[network_name], contract_field)
            registry_override = (
                os.environ.get("ERC8004_REGISTRY_ADDRESS", "").strip()
                if contract_field == "registry_contract"
                else ""
            )
            twak_target = registry_override or canonical_target
            if actual_target.lower() != twak_target.lower():
                if contract_field == "registry_contract":
                    source = (
                        "ERC8004_REGISTRY_ADDRESS override"
                        if registry_override
                        else "canonical registry"
                    )
                    reason = (
                        f"twak v0.20.0 will target {twak_target} on "
                        f"{network_name} ({source}); the SDK intent targets "
                        f"{actual_target}"
                    )
                    alternative = (
                        "set ERC8004_REGISTRY_ADDRESS to the intended registry, "
                        "or make the SDK intent use the configured registry"
                    )
                else:
                    reason = (
                        f"twak v0.20.0 targets only the canonical "
                        f"{contract_field} {canonical_target} on {network_name}; "
                        "silently using it would execute against the wrong "
                        "deployment"
                    )
                    alternative = (
                        "use an EVM wallet for custom ERC-8183 contracts, or "
                        "use the canonical contract stack"
                    )
                raise UnsupportedWalletOperation(
                    f"intent {intent.name!r} on custom contract {actual_target}",
                    reason=reason,
                    alternative=alternative,
                )
        self._ensure_wallet()
        return handler(self, intent.kwargs)

    @staticmethod
    def _opt_params(kwargs: dict[str, Any]) -> list[str]:
        """``--opt-params 0x<hex>`` when the caller sent non-empty optParams.

        v0.19.0 added raw optParams passthrough on every erc8183 write
        (REQ-1 for submit, S-1 for the rest), so the pre-v0.19 fail-fast
        guards are retired — the bytes now ride the transaction verbatim.
        On an older CLI the unknown flag fails loudly with the upgrade hint.
        """
        opt_params: bytes = kwargs.get("opt_params") or b""
        return ["--opt-params", "0x" + opt_params.hex()] if opt_params else []

    def _paymaster_args(self) -> list[str]:
        """``--paymaster-url <url>`` when the execution context carried one.

        v0.20.0 added the flag on every erc8004/erc8183 write (REQ-2): twak
        uses the URL verbatim as its MegaFuel endpoint, on bsc and bsctestnet
        alike. Omitted when no paymaster was configured, so twak keeps its
        own defaults (mainnet: auto-sponsored; testnet: self-pay). On an
        older CLI the unknown flag fails loudly with the upgrade hint.
        """
        url = self._paymaster_url
        return ["--paymaster-url", url] if url else []

    def _tx_result(self, data: dict[str, Any], **extra: Any) -> dict[str, Any]:
        """Canonical executor result envelope for a twak write command."""
        return {
            "success": True,
            "transactionHash": self._extract_tx_hash(data),
            "receipt": None,
            **extra,
        }

    # ── erc8004 handlers ──

    def _register(self, kwargs: dict[str, Any]) -> dict[str, Any]:
        agent_uri = kwargs["agent_uri"]
        metadata: list[dict[str, str]] = kwargs.get("metadata") or []
        # v0.18.0: --metadata is repeatable and atomic with the mint, so all
        # entries (including the injected built_with) ride the register tx.
        args = ["erc8004", "register", "--uri", agent_uri]
        for entry in metadata:
            args += ["--metadata", f"{entry['key']}={entry['value']}"]
        data = self._run([*args, *self._paymaster_args(), "--chain", self._chain])
        return self._tx_result(data, agentId=_as_int(data.get("agentId")), owner=data.get("owner"))

    def _set_metadata(self, kwargs: dict[str, Any]) -> dict[str, Any]:
        data = self._run(
            [
                "erc8004",
                "set-metadata",
                str(kwargs["agent_id"]),
                "--key",
                kwargs["key"],
                "--value",
                kwargs["value"],
                *self._paymaster_args(),
                "--chain",
                self._chain,
            ]
        )
        return self._tx_result(data)

    def _set_agent_uri(self, kwargs: dict[str, Any]) -> dict[str, Any]:
        data = self._run(
            [
                "erc8004",
                "set-uri",
                str(kwargs["agent_id"]),
                "--uri",
                kwargs["agent_uri"],
                *self._paymaster_args(),
                "--chain",
                self._chain,
            ]
        )
        return self._tx_result(data)

    # ── erc8183 handlers ──

    def _erc8183(self, command: str, job_id: Any, *extra: str) -> dict[str, Any]:
        """Run ``twak erc8183 <command> <jobId> [extra...] --chain <chain>``."""
        data = self._run(
            [
                "erc8183",
                command,
                str(job_id),
                *extra,
                *self._paymaster_args(),
                "--chain",
                self._chain,
            ]
        )
        return self._tx_result(data)

    def _create_job(self, kwargs: dict[str, Any]) -> dict[str, Any]:
        args = [
            "erc8183",
            "create-job",
            "--provider",
            kwargs["provider"],
            "--evaluator",
            kwargs["evaluator"],
            "--expires-at",
            str(kwargs["expired_at"]),
            "--description",
            kwargs["description"],
        ]
        hook = kwargs.get("hook")
        if hook and hook.lower() != _ZERO_ADDRESS:
            args += ["--hook", hook]
        data = self._run([*args, *self._paymaster_args(), "--chain", self._chain])
        # twak emits numeric ids as JSON strings ("150" — field-verified); the
        # local executor path yields ints from event logs. Normalize so both
        # backends honour the same envelope and downstream web3 calls
        # (uint256 args) don't blow up on a str.
        return self._tx_result(data, jobId=_as_int(data.get("jobId")))

    def _create_job_with_token(self, kwargs: dict[str, Any]) -> dict[str, Any]:
        """Create a token-bound job without degrading to legacy ``createJob``.

        ``--payment-token`` is intentionally a high-level CLI argument: TWAK
        remains the builder/signer/broadcaster and the already checksummed
        catalog address stays visible at its semantic boundary. Older CLIs
        fail the flag explicitly and are mapped to a typed capability error.
        """
        args = [
            "erc8183",
            "create-job",
            "--provider",
            kwargs["provider"],
            "--evaluator",
            kwargs["evaluator"],
            "--expires-at",
            str(kwargs["expired_at"]),
            "--description",
            kwargs["description"],
        ]
        hook = kwargs.get("hook")
        if hook and hook.lower() != _ZERO_ADDRESS:
            args += ["--hook", hook]
        args += ["--payment-token", kwargs["token"]]
        try:
            data = self._run([*args, *self._paymaster_args(), "--chain", self._chain])
        except RuntimeError as exc:
            message = str(exc)
            if _is_cli_parser_error(message, flag="--payment-token"):
                raise UnsupportedWalletOperation(
                    "erc8183.create_job_with_token",
                    reason=(
                        "upgrade twak to a version that supports the "
                        "--payment-token capability; the installed CLI cannot "
                        "safely create a token-bound job"
                    ),
                    alternative="upgrade TWAK or use an EVM/Turnkey wallet",
                    ref="docs/twak.md",
                ) from exc
            raise
        return self._tx_result(data, jobId=_as_int(data.get("jobId")))

    def _set_provider(self, kwargs: dict[str, Any]) -> dict[str, Any]:
        return self._erc8183(
            "set-provider",
            kwargs["job_id"],
            "--provider",
            kwargs["provider"],
            *self._opt_params(kwargs),
        )

    def _set_budget(self, kwargs: dict[str, Any]) -> dict[str, Any]:
        return self._erc8183(
            "set-budget",
            kwargs["job_id"],
            "--amount",
            str(kwargs["amount"]),
            *self._opt_params(kwargs),
        )

    def _fund(self, kwargs: dict[str, Any]) -> dict[str, Any]:
        # v0.19.0: --expected-budget pins the amount atomically — the contract
        # reverts with BudgetMismatch() if the on-chain budget differs, which
        # closes the check-then-fund race the old client-side `status`
        # pre-check could not (gaps S-2, shipped).
        data = self._run(
            [
                "erc8183",
                "fund",
                str(kwargs["job_id"]),
                "--expected-budget",
                str(kwargs["expected_budget"]),
                *self._opt_params(kwargs),
                *self._paymaster_args(),
                "--chain",
                self._chain,
            ]
        )
        result = self._tx_result(data)
        if data.get("approveHash"):
            result["approveHash"] = data["approveHash"]
        return result

    def _submit(self, kwargs: dict[str, Any]) -> dict[str, Any]:
        # v0.19.0 (REQ-1): optParams pass through raw, so the deliverable_url
        # JSON the SDK facade encodes there reaches OptimisticPolicy's
        # JobInitialised event — the seller role works end-to-end.
        deliverable: bytes = kwargs["deliverable"]
        return self._erc8183(
            "submit",
            kwargs["job_id"],
            "--deliverable",
            "0x" + deliverable.hex(),
            *self._opt_params(kwargs),
        )

    def _complete(self, kwargs: dict[str, Any]) -> dict[str, Any]:
        return self._reason_op("complete", kwargs)

    def _reject(self, kwargs: dict[str, Any]) -> dict[str, Any]:
        return self._reason_op("reject", kwargs)

    def _reason_op(self, command: str, kwargs: dict[str, Any]) -> dict[str, Any]:
        extra: list[str] = []
        reason: bytes = kwargs.get("reason") or b""
        if reason and reason != _ZERO_REASON:  # twak defaults --reason to zero
            extra = ["--reason", "0x" + reason.hex()]
        return self._erc8183(command, kwargs["job_id"], *extra, *self._opt_params(kwargs))

    def _claim_refund(self, kwargs: dict[str, Any]) -> dict[str, Any]:
        return self._erc8183("claim-refund", kwargs["job_id"])

    def _register_job(self, kwargs: dict[str, Any]) -> dict[str, Any]:
        return self._erc8183("register-job", kwargs["job_id"], "--policy", kwargs["policy"])

    def _settle(self, kwargs: dict[str, Any]) -> dict[str, Any]:
        extra: list[str] = []
        evidence: bytes = kwargs.get("evidence") or b""
        if evidence:  # twak defaults --evidence to 0x
            extra = ["--evidence", "0x" + evidence.hex()]
        return self._erc8183("settle", kwargs["job_id"], *extra)

    def _mark_expired(self, kwargs: dict[str, Any]) -> dict[str, Any]:
        return self._erc8183("mark-expired", kwargs["job_id"])

    def _dispute(self, kwargs: dict[str, Any]) -> dict[str, Any]:
        return self._erc8183("dispute", kwargs["job_id"])

    def _vote_reject(self, kwargs: dict[str, Any]) -> dict[str, Any]:
        return self._erc8183("vote-reject", kwargs["job_id"])

    # Dispatch table: intent name → handler. Values are the plain functions
    # from the class body, so they are called as handler(self, kwargs).
    _INTENT_HANDLERS = {
        ERC8004_REGISTER: _register,
        ERC8004_SET_METADATA: _set_metadata,
        ERC8004_SET_AGENT_URI: _set_agent_uri,
        ERC8183_CREATE_JOB: _create_job,
        ERC8183_CREATE_JOB_WITH_TOKEN: _create_job_with_token,
        ERC8183_SET_PROVIDER: _set_provider,
        ERC8183_SET_BUDGET: _set_budget,
        ERC8183_FUND: _fund,
        ERC8183_SUBMIT: _submit,
        ERC8183_COMPLETE: _complete,
        ERC8183_REJECT: _reject,
        ERC8183_CLAIM_REFUND: _claim_refund,
        ERC8183_REGISTER_JOB: _register_job,
        ERC8183_SETTLE: _settle,
        ERC8183_MARK_EXPIRED: _mark_expired,
        ERC8183_DISPUTE: _dispute,
        ERC8183_VOTE_REJECT: _vote_reject,
    }


def _as_int(value: Any) -> int | None:
    """Coerce twak's stringly-typed numeric ids ("150") to int; None stays None."""
    return None if value is None else int(value)


def _redact(cmd: list[str]) -> str:
    """Render a command for logs (no secrets are passed as args, but be safe)."""
    return " ".join(cmd)
