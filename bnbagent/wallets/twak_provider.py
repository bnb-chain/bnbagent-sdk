"""TWAKProvider — a self-broadcasting wallet backed by the ``twak`` CLI.

The Trust Wallet Agent Kit (``twak``) CLI owns the full build + sign +
broadcast lifecycle and exposes only high-level *intent* commands
(``erc8004 register``, ``erc8004 set-metadata``, ...) plus message/typed-data
signing. It therefore integrates at the **intent layer**: ``TWAKProvider``
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

Compatibility caveats against the current ``twak`` surface (tracked for the
TWAK team):
- ``erc8004 register`` has no inline metadata parameter, so registration
  metadata (including the SDK's injected ``built_with``) is replayed as
  follow-up ``set-metadata`` transactions — non-atomic, best-effort.
- ``erc8004`` / ``erc8183`` are deployed on BNB Smart Chain (both ``bsc``
  mainnet and ``bsc-testnet``); this provider rejects non-BSC chains.
"""

from __future__ import annotations

import json
import logging
import subprocess
from typing import Any

from ..signing import infer_primary_type
from .intents import (
    ERC8004_REGISTER,
    ERC8004_SET_AGENT_URI,
    ERC8004_SET_METADATA,
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
# mainnet ("bsc") and testnet ("bsc-testnet"). Both are accepted and passed
# through to twak verbatim — this is forward-compatible: "bsc-testnet" works
# the moment twak ships testnet support (in progress on the twak side); until
# then twak rejects it at runtime and we surface that error.
_DEFAULT_CHAIN = "bsc"
_ALLOWED_CHAINS = {"bsc", "bsc-testnet"}


class TWAKProvider(WalletProvider, IntentExecutor):
    """Wallet + execution backend delegating to the ``twak`` CLI.

    Args:
        chain: twak chain key for BNB Smart Chain — ``"bsc"`` (mainnet) or
            ``"bsc-testnet"``. ERC-8004/8183 are deployed on both. The value
            is passed through to twak verbatim. (``bsc-testnet`` support is
            being rolled out on the twak side; until it lands, twak rejects it
            at runtime.)
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
        ValueError: If ``chain`` is not ``"bsc"`` / ``"bsc-testnet"``.
    """

    kind = "twak"

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
                "ERC-8004/8183 are deployed on bsc (mainnet) and bsc-testnet."
            )
        self._chain = chain.lower()
        self._twak_bin = twak_bin
        self._timeout = timeout
        self._address: str | None = None
        self._ensured = False  # guards the one-shot lazy auto-create

    # ── subprocess plumbing ──

    def _run(self, args: list[str]) -> dict[str, Any]:
        """Run ``twak <args> --json`` and return the parsed JSON object.

        Raises:
            RuntimeError: If the binary is missing, the command exits
                non-zero, the output is not JSON, or ``success`` is false.
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

        if data.get("success") is False:
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
        return (
            f"twak command failed ({_redact(cmd)}): {detail or '<no output>'}. "
            f"{_SETUP_HINT}"
        )

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
        """Sign a message via ``twak wallet sign-message`` (EIP-191)."""
        self._ensure_wallet()
        data = self._run(
            ["wallet", "sign-message", "--chain", self._chain, "--message", message]
        )
        signature = data.get("signature")
        if not signature:
            raise RuntimeError(f"twak `sign-message` returned no signature: {data!r}")
        return {
            "messageHash": data.get("digest") or data.get("messageHash"),
            "signature": signature,
            **self._split_signature(signature),
        }

    def sign_transaction(self, transaction: dict[str, Any]) -> dict[str, Any]:
        """Not supported: twak exposes no raw-transaction signing primitive.

        twak owns build + broadcast for its high-level commands and offers no
        way to sign an arbitrary transaction and hand back the raw bytes.
        Use the intent path (:meth:`execute`) instead.
        """
        raise NotImplementedError(
            "TWAKProvider cannot sign arbitrary transactions: the twak CLI has "
            "no raw-tx signing primitive. Route high-level operations through "
            "execute(Intent(...)) instead."
        )

    def sign_typed_data(
        self,
        domain: dict[str, Any],
        types: dict[str, list[dict[str, str]]],
        message: dict[str, Any],
    ) -> dict[str, Any]:
        """Sign EIP-712 typed data via ``twak wallet sign-typed-data``.

        Builds the canonical ``eth_signTypedData_v4`` payload and pipes it to
        twak. Note: policy enforcement is delegated to twak's own
        domain-confirmation flow — the SDK's :class:`SigningPolicy` does not
        gate this path because signing happens out of process.
        """
        self._ensure_wallet()
        primary_type = infer_primary_type(types)
        type_defs = dict(types)
        if "EIP712Domain" not in type_defs:
            type_defs["EIP712Domain"] = _domain_type_fields(domain)
        typed_data = {
            "types": type_defs,
            "primaryType": primary_type,
            "domain": domain,
            "message": message,
        }
        cmd = [self._twak_bin, "wallet", "sign-typed-data", "--stdin", "--chain", self._chain, "--json"]
        try:
            proc = subprocess.run(
                cmd,
                input=json.dumps(typed_data),
                capture_output=True,
                text=True,
                timeout=self._timeout,
            )
        except FileNotFoundError as e:
            raise RuntimeError(
                f"twak CLI not found (looked for {self._twak_bin!r})."
            ) from e
        except subprocess.TimeoutExpired as e:
            raise RuntimeError(f"twak sign-typed-data timed out after {self._timeout}s") from e
        if proc.returncode != 0:
            raise RuntimeError(self._format_error(cmd, proc.stdout, proc.stderr))
        data = json.loads(proc.stdout)
        signature = data["signature"]
        return {
            "messageHash": data.get("digest"),
            "signature": signature,
            **self._split_signature(signature),
        }

    # ── IntentExecutor ──

    def make_executor(self, context: ExecutionContext) -> IntentExecutor:
        """This wallet broadcasts its own transactions, so it *is* its own
        executor. The web3/paymaster ``context`` is not needed."""
        return self

    def execute(self, intent: Intent) -> dict[str, Any]:
        """Execute a high-level intent by delegating to the twak CLI."""
        # Validate first so an unsupported intent never triggers wallet
        # creation or any CLI call.
        if intent.name not in (
            ERC8004_REGISTER,
            ERC8004_SET_METADATA,
            ERC8004_SET_AGENT_URI,
        ):
            raise NotImplementedError(
                f"TWAKProvider does not support intent {intent.name!r}. "
                "Supported: erc8004.register / erc8004.set_metadata / erc8004.set_agent_uri."
            )
        self._ensure_wallet()
        if intent.name == ERC8004_REGISTER:
            return self._register(intent.kwargs)
        if intent.name == ERC8004_SET_METADATA:
            return self._set_metadata(intent.kwargs)
        return self._set_agent_uri(intent.kwargs)

    def _register(self, kwargs: dict[str, Any]) -> dict[str, Any]:
        agent_uri = kwargs["agent_uri"]
        metadata: list[dict[str, str]] = kwargs.get("metadata") or []

        data = self._run(["erc8004", "register", "--uri", agent_uri, "--chain", self._chain])
        agent_id = data.get("agentId")
        tx_hash = data.get("txHash") or data.get("transactionHash")

        # twak's register carries no metadata; replay each entry (including
        # the injected built_with) as a follow-up set-metadata. Best-effort:
        # the agent is already registered, so a failed entry warns rather than
        # unwinding the registration (mirrors the local set-agent-uri follow-up).
        metadata_txs: list[str | None] = []
        if metadata and agent_id is not None:
            for entry in metadata:
                try:
                    m = self._run(
                        [
                            "erc8004", "set-metadata", str(agent_id),
                            "--key", entry["key"], "--value", entry["value"],
                            "--chain", self._chain,
                        ]
                    )
                    metadata_txs.append(m.get("txHash") or m.get("transactionHash"))
                except Exception as e:  # noqa: BLE001 - best-effort, surfaced as warning
                    logger.warning(
                        "TWAKProvider: agent %s registered but set-metadata for key=%r "
                        "failed (metadata partially applied): %s",
                        agent_id, entry.get("key"), e,
                    )

        return {
            "success": True,
            "transactionHash": tx_hash,
            "agentId": agent_id,
            "owner": data.get("owner"),
            "receipt": None,
            "metadataTxs": metadata_txs,
        }

    def _set_metadata(self, kwargs: dict[str, Any]) -> dict[str, Any]:
        data = self._run(
            [
                "erc8004", "set-metadata", str(kwargs["agent_id"]),
                "--key", kwargs["key"], "--value", kwargs["value"],
                "--chain", self._chain,
            ]
        )
        return {
            "success": True,
            "transactionHash": data.get("txHash") or data.get("transactionHash"),
            "receipt": None,
        }

    def _set_agent_uri(self, kwargs: dict[str, Any]) -> dict[str, Any]:
        data = self._run(
            [
                "erc8004", "set-uri", str(kwargs["agent_id"]),
                "--uri", kwargs["agent_uri"], "--chain", self._chain,
            ]
        )
        return {
            "success": True,
            "transactionHash": data.get("txHash") or data.get("transactionHash"),
            "receipt": None,
        }


def _domain_type_fields(domain: dict[str, Any]) -> list[dict[str, str]]:
    """Derive the EIP712Domain type field list from the domain values present."""
    spec = [
        ("name", "string"),
        ("version", "string"),
        ("chainId", "uint256"),
        ("verifyingContract", "address"),
        ("salt", "bytes32"),
    ]
    return [{"name": n, "type": t} for n, t in spec if n in domain]


def _redact(cmd: list[str]) -> str:
    """Render a command for logs (no secrets are passed as args, but be safe)."""
    return " ".join(cmd)
