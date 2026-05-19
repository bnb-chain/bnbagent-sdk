"""Shared helpers for the ERC-8183 client flow demos."""

from __future__ import annotations

import dataclasses
import os
import time
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv, dotenv_values

from bnbagent.erc8183 import ERC8183Client
from bnbagent.wallets import EVMWalletProvider
from bnbagent.config import resolve_network

ROOT = Path(__file__).resolve().parent


def load_env() -> None:
    load_dotenv(ROOT / ".env")


def _require_env(name: str) -> str:
    val = os.environ.get(name)
    if not val:
        raise RuntimeError(f"{name} is required in .env")
    return val


@dataclass(frozen=True)
class Settings:
    network: str
    client_pk: str
    provider_address: str
    provider_pk: str | None
    voter_pk: str | None


def load_settings() -> Settings:
    load_env()
    return Settings(
        network=os.environ.get("NETWORK", "bsc-testnet"),
        client_pk=_require_env("PRIVATE_KEY"),
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


def make_client(pk: str, network: str = "bsc-testnet") -> ERC8183Client:
    # Prefer the NodeReal RPC from voter/.env — it has a higher block-range
    # limit (5 000 blocks per get_logs) vs the public default endpoint.
    voter_env = dotenv_values(ROOT.parent / "voter" / ".env")
    rpc_url   = voter_env.get("RPC_URL")
    wallet    = make_wallet(pk)
    if rpc_url:
        nc = dataclasses.replace(resolve_network(network), rpc_url=rpc_url)
        return ERC8183Client(wallet, network=nc)
    return ERC8183Client(wallet, network=network)


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
