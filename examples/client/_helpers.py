"""Shared helpers for the ERC-8183 client flow demos."""

from __future__ import annotations

import dataclasses
import os
import time
from dataclasses import dataclass
from pathlib import Path

from dotenv import dotenv_values

import bnbagent
from bnbagent.erc8183 import ERC8183Client
from bnbagent.wallets import EVMWalletProvider, TWAKProvider
from bnbagent.config import resolve_network

ROOT = Path(__file__).resolve().parent

# SDK network name → twak chain key. The twak CLI rejects the SDK spelling
# ("bsc-testnet") with CHAIN_UNSUPPORTED (field-verified on v0.18.0) — its
# BNB Smart Chain keys are "bsc" and "bsctestnet", no hyphen.
_TWAK_CHAIN_FOR_NETWORK = {
    "bsc-testnet": "bsctestnet",
    "bsc-mainnet": "bsc",
}


def load_env() -> None:
    # bnbagent.load_env also picks up .env.local, with the precedence
    # shell environment > .env.local > .env (a checked-in .env never
    # overrides your local overrides or real env vars).
    bnbagent.load_env(root=ROOT)


def _require_env(name: str) -> str:
    val = os.environ.get(name)
    if not val:
        raise RuntimeError(f"{name} is required in .env")
    return val


@dataclass(frozen=True)
class Settings:
    network: str
    wallet_kind: str        # "evm" (default) | "twak" — switches the CLIENT wallet only
    client_pk: str | None   # required for evm; unused when wallet_kind=twak
    provider_address: str
    provider_pk: str | None
    voter_pk: str | None


def load_settings() -> Settings:
    load_env()
    wallet_kind = os.environ.get("WALLET_KIND", "evm").lower()
    if wallet_kind not in ("evm", "twak"):
        raise RuntimeError(f"WALLET_KIND must be 'evm' or 'twak', got {wallet_kind!r}")
    # With a twak wallet, PRIVATE_KEY is optional: key custody lives inside
    # twak — the client is whatever `twak wallet address` reports, and the
    # password is resolved by twak itself (TWAK_WALLET_PASSWORD / keychain),
    # never passed by the SDK.
    if wallet_kind == "twak":
        client_pk = os.environ.get("PRIVATE_KEY") or None
    else:
        client_pk = _require_env("PRIVATE_KEY")
    return Settings(
        network=os.environ.get("NETWORK", "bsc-testnet"),
        wallet_kind=wallet_kind,
        client_pk=client_pk,
        provider_address=_require_env("PROVIDER_ADDRESS"),
        provider_pk=os.environ.get("PROVIDER_PRIVATE_KEY") or None,
        voter_pk=os.environ.get("VOTER_PRIVATE_KEY") or None,
    )


def make_wallet(pk: str) -> EVMWalletProvider:
    """Wrap a raw testnet PK into an ephemeral wallet provider.

    ``persist=False`` keeps the demo hermetic — no keystore files are
    written to ``~/.bnbagent/wallets``. Do NOT reuse this pattern for
    production keys.
    """
    return EVMWalletProvider(password="example", private_key=pk, persist=False)


def _demo_network(network: str):
    # Prefer the NodeReal RPC from voter/.env — it has a higher block-range
    # limit (5 000 blocks per get_logs) vs the public default endpoint.
    voter_env = dotenv_values(ROOT.parent / "voter" / ".env")
    rpc_url   = voter_env.get("RPC_URL")
    if rpc_url:
        return dataclasses.replace(resolve_network(network), rpc_url=rpc_url)
    return network


def make_client(pk: str, network: str = "bsc-testnet") -> ERC8183Client:
    return ERC8183Client(make_wallet(pk), network=_demo_network(network))


def make_primary_client(s: Settings) -> ERC8183Client:
    """Build the CLIENT-role client according to ``WALLET_KIND``.

    - ``evm`` (default): wrap ``PRIVATE_KEY`` exactly as before — zero
      behavior change for an existing ``.env``.
    - ``twak``: delegate signing AND broadcasting to the twak CLI. No
      private key crosses the SDK — twak owns custody, and the flow
      scripts don't change at all (wallet polymorphism: ``ERC8183Client``
      routes every write through ``wallet.make_executor()``).

    Only the CLIENT role switches. The provider/voter wallets always stay
    EVM: twak cannot submit deliverables until upstream REQ-1 lands (its
    ``submit`` drops the ``deliverable_url`` optParams — see the role
    matrix in ``bnbagent/wallets/README.md``).
    """
    if s.wallet_kind == "twak":
        try:
            chain = _TWAK_CHAIN_FOR_NETWORK[s.network]
        except KeyError:
            raise RuntimeError(
                f"WALLET_KIND=twak supports networks {sorted(_TWAK_CHAIN_FOR_NETWORK)}, "
                f"got NETWORK={s.network!r}"
            ) from None
        return ERC8183Client(TWAKProvider(chain=chain), network=_demo_network(s.network))
    return make_client(s.client_pk, s.network)


def minutes_from_now(minutes: int) -> int:
    return int(time.time()) + minutes * 60


def expiry_for(client: ERC8183Client, slack_minutes: int = 10) -> int:
    """Return an ``expiredAt`` that fits the policy's dispute window.

    The on-chain ``OptimisticPolicy`` rejects ``commerce.submit`` with
    ``SubmissionTooLate`` unless ``submit_time + disputeWindow <= expiredAt``,
    so ``expiredAt = now + disputeWindow + slack``. ``slack`` is the
    provider's window to complete poll → on_job → IPFS upload → on-chain
    submit before the deadline expires.

    The 10-minute default fits a clean happy-path run (poll cadence ~30 s,
    on_job/IPFS/submit ~10 s combined). It is **demo-grade only** — once a
    job is funded with this expiry, restarting the agent mid-flow or
    debugging for tens of minutes can push the deadline past the submit
    cutoff and the provider has to abandon the job. Production clients
    should set ``slack`` to hours or days; pass an explicit
    ``slack_minutes=`` here when iterating in a long-running session.
    """
    dispute_window = client.policy.dispute_window()
    return int(time.time()) + int(dispute_window) + slack_minutes * 60


def banner(msg: str) -> None:
    print()
    print("=" * 60)
    print(f" {msg}")
    print("=" * 60)
