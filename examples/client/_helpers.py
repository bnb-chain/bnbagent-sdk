"""Shared helpers for the APEX client flow demos."""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

from bnbagent.apex import APEXClient
from bnbagent.wallets import EVMWalletProvider

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


def make_client(pk: str, network: str = "bsc-testnet") -> APEXClient:
    return APEXClient(make_wallet(pk), network=network)


def minutes_from_now(minutes: int) -> int:
    return int(time.time()) + minutes * 60


def banner(msg: str) -> None:
    print()
    print("=" * 60)
    print(f" {msg}")
    print("=" * 60)
