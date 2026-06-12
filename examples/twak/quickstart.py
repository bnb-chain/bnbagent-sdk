"""TWAK wallet quickstart — custody, capabilities, guard rails, deployment mode.

A guided tour of ``TWAKProvider`` that costs nothing: zero funds move, zero
chain RPC calls are made. Every wallet touched here is a throwaway created
inside a tempdir ``home`` (the provider runs each twak subprocess with
``HOME=<home>``, so your real ``~/.twak`` wallet is never read or written).

The four stops:

    (a) custody          — create an isolated throwaway wallet, inspect describe()
    (b) capabilities     — what twak can do, contrasted with an EVM wallet
    (c) guard rails LIVE — the UnsupportedWalletOperation gates, plus a real
                           sign_message round-trip (EIP-191 + ecrecover)
    (d) deployment mode  — materialize_twak_home + expected_address pinning
                           + auto_create=False (the INV-4 recipe)

Prerequisites:
    - The twak CLI on PATH (or point TWAK_BIN at one, e.g.
      ``TWAK_BIN=./node_modules/.bin/twak``), >= v0.19.0.
    - twak API credentials — the one thing this script cannot fabricate:
      the CLI refuses *every* command (even ``wallet create``)
      without them. Either ``~/.twak/credentials.json`` (from ``twak init``
      / ``twak setup``) or the TWAK_ACCESS_ID / TWAK_HMAC_SECRET env vars.
      The script copies them into each throwaway home; they are API-access
      credentials, not key material.

Usage:
    python examples/twak/quickstart.py

Exit code 0 + "QUICKSTART COMPLETE" means the custody story, the capability
gates and the deployment pinning all behaved as documented in
bnbagent/wallets/README.md.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from eth_account import Account
from eth_account.messages import encode_defunct

from bnbagent.wallets import (
    EVMWalletProvider,
    Intent,
    TWAKProvider,
    UnsupportedWalletOperation,
    WalletIdentityMismatch,
    materialize_twak_home,
)
from bnbagent.wallets.capabilities import (
    CALLS_ARBITRARY,
    INTENTS_ERC8183,
    SIGN_TYPED_DATA,
)
from bnbagent.x402 import X402Signer

# Pin a specific CLI when several are installed (e.g. the repo-local
# node_modules/.bin/twak vs a global install).
TWAK_BIN = os.environ.get("TWAK_BIN", "twak")


def banner(msg: str) -> None:
    print()
    print("=" * 64)
    print(f" {msg}")
    print("=" * 64)


def provision_credentials(home: Path) -> str:
    """Make twak API credentials reachable inside the throwaway ``home``.

    twak requires API credentials for every command, so a truly
    empty home cannot even create a wallet. Credentials are read by twak
    from ``<home>/.twak/credentials.json`` or from the TWAK_ACCESS_ID /
    TWAK_HMAC_SECRET env vars (which the provider's subprocesses inherit
    automatically — only HOME is overridden). Returns a human-readable
    description of the source, or exits with setup pointers.
    """
    if os.environ.get("TWAK_ACCESS_ID") and os.environ.get("TWAK_HMAC_SECRET"):
        return "env vars (TWAK_ACCESS_ID / TWAK_HMAC_SECRET, inherited)"
    src = Path.home() / ".twak" / "credentials.json"
    if src.is_file():
        # Reuse materialize_twak_home's sibling behavior by hand: 0700 dir,
        # 0600 file (INV-3). We cannot call materialize_twak_home here —
        # it requires wallet_json, and this home has no wallet yet.
        twak_dir = home / ".twak"
        twak_dir.mkdir(mode=0o700, exist_ok=True)
        dst = twak_dir / "credentials.json"
        dst.write_text(src.read_text())
        dst.chmod(0o600)
        return f"copied from {src}"
    print(
        "twak API credentials not found. Run `twak init --api-key <id> "
        "--api-secret <secret>` (writes ~/.twak/credentials.json) or export "
        "TWAK_ACCESS_ID / TWAK_HMAC_SECRET, then rerun."
    )
    sys.exit(1)


def create_throwaway_wallet(provider: TWAKProvider, home: Path) -> str:
    """Create the tempdir wallet, preferring the SDK path.

    ``create_wallet()`` keeps the password OFF argv (INV-1) and expects twak
    to resolve it from TWAK_WALLET_PASSWORD / the keychain. The shipped
    CLI, however, hard-requires ``--password`` on argv for ``wallet create``
    (env resolution only covers *unlock*; gaps S-8, re-verified unchanged on
    v0.19.0) — so the SDK call currently fails. We try it first (so this script self-heals when either
    side fixes the mismatch) and fall back to driving the CLI directly.
    The fallback puts the throwaway password on argv — acceptable for a
    demo wallet in a tempdir, never for a real one.
    """
    try:
        return provider.create_wallet(no_keychain=True, skip_password_check=True)
    except RuntimeError as e:
        print("  [note] SDK create_wallet() failed against this CLI build:")
        print(f"         {str(e)[:120]}...")
        print("         (twak requires --password on argv for create — gaps S-8;")
        print("          falling back to a direct CLI create — demo wallet only)")
    proc = subprocess.run(  # noqa: S603 - fixed arg list, no shell
        [
            TWAK_BIN, "wallet", "create",
            "--password", os.environ["TWAK_WALLET_PASSWORD"],
            "--no-keychain", "--skip-password-check", "--json",
        ],
        capture_output=True,
        text=True,
        env={**os.environ, "HOME": str(home)},
        timeout=120,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"twak wallet create failed: {proc.stderr or proc.stdout}")
    return provider.address  # resolved via `twak wallet address` (env password)


# ── (a) custody ────────────────────────────────────────────────────────────


def step_a_custody(home: Path) -> TWAKProvider:
    banner("(a) custody — throwaway wallet in an isolated home")

    # INV-1: the wallet password reaches twak by ENVIRONMENT INHERITANCE
    # into the subprocess — never argv (argv is world-readable via `ps`).
    # On a dev machine this would live in .env.local + bnbagent.load_env().
    if not os.environ.get("TWAK_WALLET_PASSWORD"):
        os.environ["TWAK_WALLET_PASSWORD"] = "Quickstart-Demo-Pw-1"  # demo value
        print("TWAK_WALLET_PASSWORD not set — using a demo value (env, never argv)")

    creds_source = provision_credentials(home)
    print(f"API credentials: {creds_source}")

    # home=<tempdir> reroutes every twak subprocess to <tempdir>/.twak —
    # multi-wallet isolation on one OS user (design gaps S-5). Your real
    # ~/.twak is untouched.
    provider = TWAKProvider(home=home, twak_bin=TWAK_BIN)
    address = create_throwaway_wallet(provider, home)
    print(f"throwaway wallet created: {address}")

    # describe() is the uniform, non-sensitive introspection surface every
    # WalletProvider shares: kind / address / key_location / exists /
    # capabilities. Note key_location points INSIDE the tempdir home.
    print("describe():")
    print(json.dumps(provider.describe(), indent=2))
    return provider


# ── (b) capabilities ───────────────────────────────────────────────────────


def step_b_capabilities(twak: TWAKProvider) -> None:
    banner("(b) capabilities — twak vs an in-memory EVM wallet")

    # Capabilities answer "can this wallet do X" BEFORE trying: consumers
    # route on them instead of probing by calling-and-catching. sign.* values
    # are auto-derived from method overrides; the rest is declared.
    print(f"twak capabilities: {sorted(twak.capabilities())}")
    print(f"  supports('{INTENTS_ERC8183}')  = {twak.supports(INTENTS_ERC8183)}")
    print(f"  supports('{SIGN_TYPED_DATA}')  = {twak.supports(SIGN_TYPED_DATA)}")
    print(f"  supports('{CALLS_ARBITRARY}') = {twak.supports(CALLS_ARBITRARY)}")

    # An EVM wallet is the mirror image: a pure signer with no execution
    # menu of its own (the SDK broadcasts for it), but full signing power.
    evm = EVMWalletProvider(
        password="quickstart-demo",
        private_key=Account.create().key.hex(),  # ephemeral, never broadcast
        persist=False,                           # in-memory only, no keystore file
    )
    print(f"evm capabilities:  {sorted(evm.capabilities())}")
    print(
        "  evm-only:  "
        f"{sorted(evm.capabilities() - twak.capabilities())}"
    )
    print(
        "  twak-only: "
        f"{sorted(twak.capabilities() - evm.capabilities())}"
    )
    print(
        "Reading: twak signs nothing raw (no sign.transaction/typed_data) but\n"
        "broadcasts its own fixed intent menu; EVM signs anything but needs\n"
        "the SDK's LocalExecutor to broadcast. Absence == unsupported."
    )


# ── (c) guard rails, live ──────────────────────────────────────────────────


def step_c_guard_rails(twak: TWAKProvider) -> None:
    banner("(c) guard rails LIVE — every gate raises before any CLI call")

    # Gate 1 (runtime): sign_typed_data is deliberately NOT implemented for
    # twak — the CLI has no generic EIP-712 primitive, and overriding the
    # method just to raise would falsely claim the capability. The base
    # default raises descriptively, without ever shelling out.
    try:
        twak.sign_typed_data({}, {}, {})
        raise AssertionError("expected UnsupportedWalletOperation")
    except UnsupportedWalletOperation as e:
        print(f"1. sign_typed_data -> UnsupportedWalletOperation:\n   {e}")

    # Gate 2 (composition): X402Signer checks supports('sign.typed_data') in
    # its constructor — a twak wallet is rejected before any payment flow
    # exists, with a pointer at the correct path: the delegated payer
    # (wallet.make_x402_payer()), where twak signs the payment internally.
    try:
        X402Signer(twak)
        raise AssertionError("expected UnsupportedWalletOperation")
    except UnsupportedWalletOperation as e:
        print(f"2. X402Signer(twak) -> UnsupportedWalletOperation:\n   {e}")

    # Gate 3 (dispatch): twak speaks a FIXED command menu. An unknown intent
    # is refused up front — listing what IS supported — before _ensure_wallet
    # or any subprocess runs.
    try:
        twak.execute(Intent(name="erc20.transfer", kwargs={}))
        raise AssertionError("expected UnsupportedWalletOperation")
    except UnsupportedWalletOperation as e:
        print(f"3. execute(erc20.transfer) -> UnsupportedWalletOperation:\n   {e}")

    # And the one signing primitive twak DOES have, end-to-end. Three
    # adaptations happen under the hood (gaps S-4): 0x prefix normalization
    # (the CLI returns a bare hex signature), a client-side EIP-191 digest
    # (the CLI returns none), and an ecrecover self-check — the SDK computed
    # the digest but twak produced the signature out of process, so
    # recovering the signer is the only runtime proof both sides agree on
    # the message bytes.
    msg = "hello from the bnbagent twak quickstart"
    signed = twak.sign_message(msg)
    recovered = Account.recover_message(
        encode_defunct(text=msg), signature=signed["signature"]
    )
    assert recovered.lower() == twak.address.lower(), (recovered, twak.address)
    print("4. sign_message round-trip:")
    print(f"   messageHash = {signed['messageHash']}")
    print(f"   signature   = {signed['signature'][:20]}...{signed['signature'][-8:]}")
    print(f"   ecrecover -> {recovered} == wallet address (self-check passed)")


# ── (d) deployment mode ────────────────────────────────────────────────────


def step_d_deployment(twak: TWAKProvider, home1: Path, home2: Path, home3: Path) -> None:
    banner("(d) deployment mode — materialize + pin + never auto-create")

    address = twak.address

    # In deployment the encrypted wallet file arrives from a secrets manager
    # (the TWAK_WALLET_JSON bundle key), not from disk. Here we play the
    # secrets manager: read the throwaway wallet's encrypted JSON and
    # materialize it into a SECOND home — 0700/0600, idempotent, never
    # overwrites (INV-3/INV-4). wallet.json is portable by design (an
    # encrypted mnemonic with no machine-binding fields).
    wallet_json = (home1 / ".twak" / "wallet.json").read_text()
    creds_path = home1 / ".twak" / "credentials.json"
    materialized = materialize_twak_home(
        wallet_json=wallet_json,
        credentials_json=creds_path.read_text() if creds_path.is_file() else None,
        home=home2,
    )
    print(f"1. materialized secret bundle into {materialized}/.twak/")

    # Deployment construction: PIN the identity and FORBID creation. A
    # deployed agent's address is an on-chain identity — it may only come
    # from the bundle, never be silently minted (INV-4).
    pinned = TWAKProvider(
        home=materialized,
        twak_bin=TWAK_BIN,
        expected_address=address,
        auto_create=False,
    )
    resolved = pinned.address  # first lookup runs the identity check
    assert resolved.lower() == address.lower(), (resolved, address)
    print(f"2. pinned provider resolved {resolved} — identity check passed")

    # Wrong pin == stale or wrong-environment secret bundle. The provider
    # refuses before any state-changing operation; fix the bundle, never
    # the pin.
    imposter = TWAKProvider(
        home=materialized,
        twak_bin=TWAK_BIN,
        expected_address="0x" + "42" * 20,  # not this wallet
        auto_create=False,
    )
    try:
        _ = imposter.address
        raise AssertionError("expected WalletIdentityMismatch")
    except WalletIdentityMismatch as e:
        print(f"3. wrong expected_address -> WalletIdentityMismatch:\n   {e}")

    # auto_create=False against an EMPTY home: fail closed. The intended
    # error points at materialize_twak_home(); against the real CLI the
    # exists() probe is fooled (`twak wallet status` exits 0 with
    # "not configured"), so today you get the CLI's own "No wallet found"
    # instead — the operation still fails closed, and crucially no wallet
    # is ever silently minted. (Tracked as SDK DX feedback.)
    provision_credentials(home3)  # so the failure is about the wallet, not creds
    empty = TWAKProvider(home=home3, twak_bin=TWAK_BIN, auto_create=False)
    try:
        _ = empty.address
        raise AssertionError("expected a missing-wallet error")
    except RuntimeError as e:  # WalletIdentityMismatch is also a RuntimeError
        print(f"4. empty home + auto_create=False -> {type(e).__name__}:")
        print(f"   {str(e)[:160]}...")


# ── main ───────────────────────────────────────────────────────────────────


def main() -> int:
    print("TWAK quickstart — hermetic walkthrough (no funds, no chain RPC)")
    print(f"twak binary: {TWAK_BIN}")

    home1 = Path(tempfile.mkdtemp(prefix="twak-quickstart-a-"))
    home2 = Path(tempfile.mkdtemp(prefix="twak-quickstart-d-"))
    home3 = Path(tempfile.mkdtemp(prefix="twak-quickstart-empty-"))
    try:
        twak = step_a_custody(home1)
        step_b_capabilities(twak)
        step_c_guard_rails(twak)
        step_d_deployment(twak, home1, home2, home3)
    finally:
        # Throwaway homes hold a throwaway wallet — burn them.
        for home in (home1, home2, home3):
            shutil.rmtree(home, ignore_errors=True)

    banner("QUICKSTART COMPLETE ✓")
    return 0


if __name__ == "__main__":
    sys.exit(main())
