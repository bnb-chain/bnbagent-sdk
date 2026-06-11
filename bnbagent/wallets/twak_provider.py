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
- ``--json`` is always appended (it implies ``--yes``, skipping interactive
  confirmation, per the twak spec).

Compatibility caveats against ``twak`` v0.18.0 (the authoritative command
surface is ``docs/twak-cli-gaps-v0.18.0.md``):
- ``erc8004 register`` takes repeatable ``--metadata key=value`` flags, so
  registration metadata (including the SDK's injected ``built_with``) is
  atomic with the mint. (The pre-v0.18.0 ``set-metadata`` replay workaround
  is resolved and removed.)
- Supported chain keys are ``bsc`` (mainnet) and ``bsctestnet``; the spec's
  ``bsc-testnet`` is rejected by the real CLI with ``CHAIN_UNSUPPORTED``
  (field-verified). This provider rejects non-BSC chains.
- ``sign_typed_data`` raises :class:`UnsupportedWalletOperation` by design
  decision (P0): twak signs ERC-8004/8183/x402 payloads internally via its
  dedicated commands and provides no generic EIP-712 primitive.
- ``erc8183 submit`` cannot carry ``optParams`` (gaps REQ-1, pending):
  submitting with non-empty ``opt_params`` fails fast instead of silently
  producing an unevaluable job — see ``docs/twak-cli-gaps-v0.18.0.md``.
"""

from __future__ import annotations

import json
import logging
import subprocess
from typing import Any

from eth_account import Account
from eth_account.messages import defunct_hash_message, encode_defunct

from .capabilities import (
    BROADCAST_SELF,
    INTENTS_ERC8004,
    INTENTS_ERC8183,
    X402_PAY,
)
from .errors import UnsupportedWalletOperation
from .intents import (
    ERC8004_REGISTER,
    ERC8004_SET_AGENT_URI,
    ERC8004_SET_METADATA,
    ERC8183_CLAIM_REFUND,
    ERC8183_COMPLETE,
    ERC8183_CREATE_JOB,
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

DEFAULT_TWAK_BIN = "twak"
DEFAULT_TIMEOUT = 120  # seconds per CLI invocation

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

_ZERO_ADDRESS = "0x" + "00" * 20
_ZERO_REASON = b"\x00" * 32


class TWAKProvider(WalletProvider, IntentExecutor):
    """Wallet + execution backend delegating to the ``twak`` CLI.

    Args:
        chain: twak chain key for BNB Smart Chain — ``"bsc"`` (mainnet) or
            ``"bsctestnet"``. ERC-8004/8183 are deployed on both. The value
            is passed through to twak verbatim. (Note: the spec's
            ``bsc-testnet`` spelling is rejected by the real CLI.)
        twak_bin: Path to (or name of) the ``twak`` executable.
        timeout: Per-command timeout in seconds.

    Like ``EVMWalletProvider``, this provider auto-creates a wallet when none
    exists — see :meth:`_ensure_wallet` / :meth:`create_wallet`. The check (and
    any creation) happens lazily on the **first** operation rather than at
    construction, so building the provider stays side-effect-free (constructing
    it, reading ``kind``, etc. never shell out). Creation needs the API
    credentials and wallet password already reachable by ``twak`` (``twak
    init`` / ``TWAK_ACCESS_ID`` + ``TWAK_HMAC_SECRET``; ``TWAK_WALLET_PASSWORD``
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
    # x402.pay: the delegated TwakX402Payer class arrives in Phase 1c.
    _extra_capabilities = frozenset(
        {BROADCAST_SELF, INTENTS_ERC8004, INTENTS_ERC8183, X402_PAY}
    )

    def __init__(
        self,
        *,
        chain: str = _DEFAULT_CHAIN,
        twak_bin: str = DEFAULT_TWAK_BIN,
        timeout: int = DEFAULT_TIMEOUT,
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
        self._address: str | None = None
        self._ensured = False  # guards the one-shot lazy auto-create

    # ── subprocess plumbing ──

    def _run(self, args: list[str]) -> dict[str, Any]:
        """Run ``twak <args> --json`` and return the parsed JSON object.

        Failure = non-zero exit, a truthy ``error`` field, or ``success`` set
        to false. The *absence* of ``success`` is never trusted as success —
        the real CLI omits the field inconsistently across error envelopes
        (field-verified on v0.18.0).

        Raises:
            RuntimeError: If the binary is missing, the command exits
                non-zero, the output is not JSON, or the envelope reports
                an error.
        """
        cmd = [self._twak_bin, *args, "--json"]
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=self._timeout,
            )
        except FileNotFoundError as e:
            raise RuntimeError(
                f"twak CLI not found (looked for {self._twak_bin!r}). "
                "Install it with `npm install -g @trustwallet/cli`, then "
                "configure it (see TWAKProvider prerequisites)."
            ) from e
        except subprocess.TimeoutExpired as e:
            raise RuntimeError(f"twak command timed out after {self._timeout}s: {_redact(cmd)}") from e

        if proc.returncode != 0:
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
        combined = f"{stderr} {stdout}"
        if "unknown command" in combined or "unknown option" in combined:
            hint = (
                "The installed twak CLI does not recognise this command/option "
                "— upgrade twak to >= v0.18.0 (`npm install -g @trustwallet/cli`)."
            )
        else:
            hint = _SETUP_HINT
        return (
            f"twak command failed ({_redact(cmd)}): {detail or '<no output>'}. "
            f"{hint}"
        )

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
            data = self._run(["wallet", "address", "--chain", self._chain])
            addr = data.get("address") or data.get("wallet")
            if not addr:
                raise RuntimeError(
                    f"twak `wallet address` did not return an address: {data!r}"
                )
            self._address = addr
        return self._address

    @property
    def key_location(self) -> str | None:
        """twak owns custody; the key never lives in the SDK's store.

        The encrypted BIP39 mnemonic lives in ``~/.twak/wallet.json``; the
        signing password is resolved from ``TWAK_WALLET_PASSWORD`` or the OS
        keychain (API credentials are separate, in ``~/.twak/credentials.json``).
        """
        return "~/.twak/wallet.json (encrypted by the twak CLI) + OS keychain/TWAK_WALLET_PASSWORD"

    def exists(self) -> bool:
        """True if twak reports a configured wallet (best-effort).

        Probes ``twak wallet status``; any failure (missing binary, no wallet,
        non-zero exit) is treated as "does not exist" rather than raising.
        """
        try:
            self._run(["wallet", "status"])
            return True
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
        self._run(args)
        self._address = None  # invalidate cache so the new address is re-read
        return self.address

    def _ensure_wallet(self) -> None:
        """One-shot lazy auto-create on the first operation.

        Runs at most once per instance: probes for an existing wallet and
        creates one if absent (EVM-parity). When the wallet already exists this
        costs a single ``wallet status`` probe; creation, when needed, blocks
        until twak finishes (or errors clearly if credentials / password are
        not configured).
        """
        if self._ensured:
            return
        self._ensured = True
        if not self.exists():
            self.create_wallet()

    def sign_message(self, message: str) -> dict[str, Any]:
        """Sign a message via ``twak wallet sign-message`` (EIP-191).

        Three adaptations over the raw CLI output (gaps S-4):
        1. the signature is normalised to a ``0x`` prefix;
        2. the EIP-191 digest is computed client-side (the CLI returns none);
        3. the signer is recovered from the digest + signature and checked
           against the wallet address — we compute the digest ourselves but
           twak produced the signature, so a recovery round-trip is the only
           runtime proof both sides agree on the message bytes.
        """
        self._ensure_wallet()
        data = self._run(
            ["wallet", "sign-message", "--chain", self._chain, "--message", message]
        )
        signature = data.get("signature")
        if not signature:
            raise RuntimeError(f"twak `sign-message` returned no signature: {data!r}")
        if not signature.startswith("0x"):
            signature = "0x" + signature
        digest = "0x" + bytes(defunct_hash_message(text=message)).hex()
        try:
            recovered = Account.recover_message(
                encode_defunct(text=message), signature=signature
            )
        except Exception as e:
            raise RuntimeError(
                f"twak `sign-message` returned a malformed signature "
                f"({signature[:20]}...): {e}"
            ) from e
        if recovered.lower() != self.address.lower():
            raise RuntimeError(
                f"twak sign-message self-check failed: signature recovers to "
                f"{recovered}, expected the wallet address {self.address}. "
                "The SDK computes the EIP-191 digest client-side while twak "
                "signs out of process — a recovery mismatch means the two "
                "sides encoded the message bytes differently, and using this "
                "signature would fail verification later. Refusing to return it."
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

    # ── IntentExecutor ──

    def make_executor(self, context: ExecutionContext) -> IntentExecutor:
        """This wallet broadcasts its own transactions, so it *is* its own
        executor. The web3 ``context`` is not needed; a paymaster cannot be
        honoured (gaps REQ-2) and triggers a warning."""
        if context.paymaster is not None:
            logger.warning(
                "TWAKProvider: twak has no paymaster support (gaps REQ-2) — "
                "the paymaster is ignored and gas is paid from the twak "
                "wallet's BNB balance."
            )
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
        if intent.kwargs.get("opt_params"):
            # The twak CLI has no --opt-params on any erc8183 write; dropping
            # caller data silently is never acceptable (P4).
            if intent.name == ERC8183_SUBMIT:
                raise UnsupportedWalletOperation(
                    "erc8183.submit with non-empty opt_params",
                    reason=(
                        "the twak CLI always submits empty optParams, so the "
                        "deliverable_url carried there would be dropped and "
                        "the job would become unevaluable — protocol-breaking, "
                        "so this fails fast instead of submitting silently"
                    ),
                    alternative=(
                        "use an EVM wallet for the provider (seller) role "
                        "until twak ships --opt-params"
                    ),
                    ref="REQ-1",
                )
            raise UnsupportedWalletOperation(
                f"{intent.name} with non-empty opt_params",
                reason=(
                    "the twak CLI has no --opt-params flag; the caller's data "
                    "would be silently dropped"
                ),
                alternative="send empty opt_params or use an EVM wallet",
                ref="S-1",
            )
        self._ensure_wallet()
        return handler(self, intent.kwargs)

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
        data = self._run([*args, "--chain", self._chain])
        return self._tx_result(
            data, agentId=data.get("agentId"), owner=data.get("owner")
        )

    def _set_metadata(self, kwargs: dict[str, Any]) -> dict[str, Any]:
        data = self._run(
            [
                "erc8004", "set-metadata", str(kwargs["agent_id"]),
                "--key", kwargs["key"], "--value", kwargs["value"],
                "--chain", self._chain,
            ]
        )
        return self._tx_result(data)

    def _set_agent_uri(self, kwargs: dict[str, Any]) -> dict[str, Any]:
        data = self._run(
            [
                "erc8004", "set-uri", str(kwargs["agent_id"]),
                "--uri", kwargs["agent_uri"], "--chain", self._chain,
            ]
        )
        return self._tx_result(data)

    # ── erc8183 handlers ──

    def _erc8183(self, command: str, job_id: Any, *extra: str) -> dict[str, Any]:
        """Run ``twak erc8183 <command> <jobId> [extra...] --chain <chain>``."""
        data = self._run(
            ["erc8183", command, str(job_id), *extra, "--chain", self._chain]
        )
        return self._tx_result(data)

    def _create_job(self, kwargs: dict[str, Any]) -> dict[str, Any]:
        args = [
            "erc8183", "create-job",
            "--provider", kwargs["provider"],
            "--evaluator", kwargs["evaluator"],
            "--expires-at", str(kwargs["expired_at"]),
            "--description", kwargs["description"],
        ]
        hook = kwargs.get("hook")
        if hook and hook.lower() != _ZERO_ADDRESS:
            args += ["--hook", hook]
        data = self._run([*args, "--chain", self._chain])
        return self._tx_result(data, jobId=data.get("jobId"))

    def _set_provider(self, kwargs: dict[str, Any]) -> dict[str, Any]:
        return self._erc8183(
            "set-provider", kwargs["job_id"], "--provider", kwargs["provider"]
        )

    def _set_budget(self, kwargs: dict[str, Any]) -> dict[str, Any]:
        return self._erc8183(
            "set-budget", kwargs["job_id"], "--amount", str(kwargs["amount"])
        )

    def _fund(self, kwargs: dict[str, Any]) -> dict[str, Any]:
        # twak's `fund` has no amount flag: it approves + deposits whatever
        # the on-chain budget is, not the caller's expected_budget. Pre-check
        # via `status` so a budget changed underneath the caller fails here
        # instead of escrowing the wrong amount (gaps S-2 client-side guard;
        # `budget` is a flat string in the real status output, field-verified).
        job_id = kwargs["job_id"]
        expected_budget = kwargs["expected_budget"]
        status = self._run(["erc8183", "status", str(job_id), "--chain", self._chain])
        on_chain_budget = status.get("budget")
        if on_chain_budget is None:
            raise RuntimeError(
                f"TWAKProvider cannot pre-check job {job_id} before funding: "
                f"twak `erc8183 status` returned no budget field: {status!r}"
            )
        if int(on_chain_budget) != int(expected_budget):
            raise RuntimeError(
                f"TWAKProvider refuses to fund job {job_id}: twak's `fund` "
                f"deposits the on-chain budget, not the caller's amount, and "
                f"the on-chain budget ({on_chain_budget!r}) does not match "
                f"expected_budget ({expected_budget!r}). Set the budget first "
                "or pass the on-chain value. (Client-side pre-check for gaps "
                "S-2 in docs/twak-cli-gaps-v0.18.0.md.)"
            )
        data = self._run(["erc8183", "fund", str(job_id), "--chain", self._chain])
        result = self._tx_result(data)
        if data.get("approveHash"):
            result["approveHash"] = data["approveHash"]
        return result

    def _submit(self, kwargs: dict[str, Any]) -> dict[str, Any]:
        # Non-empty opt_params is rejected up front in execute() (REQ-1).
        deliverable: bytes = kwargs["deliverable"]
        return self._erc8183(
            "submit", kwargs["job_id"], "--deliverable", "0x" + deliverable.hex()
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
        return self._erc8183(command, kwargs["job_id"], *extra)

    def _claim_refund(self, kwargs: dict[str, Any]) -> dict[str, Any]:
        return self._erc8183("claim-refund", kwargs["job_id"])

    def _register_job(self, kwargs: dict[str, Any]) -> dict[str, Any]:
        return self._erc8183(
            "register-job", kwargs["job_id"], "--policy", kwargs["policy"]
        )

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


def _redact(cmd: list[str]) -> str:
    """Render a command for logs (no secrets are passed as args, but be safe)."""
    return " ".join(cmd)
