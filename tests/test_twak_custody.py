"""Tests for materialize_twak_home (design §4: INV-3 permissions, INV-4 no overwrite).

The function is studio's ``ensure_keystore_materialized`` pattern for the
second wallet kind: the encrypted twak wallet comes out of the secret bundle
(``TWAK_WALLET_JSON`` / ``TWAK_CREDENTIALS_JSON``) and is written verbatim
under ``<home>/.twak/`` with 0700/0600, idempotently and never overwriting.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path

from bnbagent.wallets import materialize_twak_home

# Stand-ins for the (encrypted) bundle values — written verbatim, never parsed.
WALLET_JSON = '{"version":1,"crypto":{"cipher":"aes-256-gcm","ciphertext":"deadbeef"}}'
CREDENTIALS_JSON = '{"accessId":"id-123","hmacSecret":"shh"}'


def _mode(path: Path) -> int:
    return stat.S_IMODE(os.stat(path).st_mode)


def test_materialize_writes_dir_0700_and_files_0600(tmp_path):
    returned = materialize_twak_home(
        wallet_json=WALLET_JSON, credentials_json=CREDENTIALS_JSON, home=tmp_path
    )
    assert returned == tmp_path

    twak_dir = tmp_path / ".twak"
    assert _mode(twak_dir) == 0o700  # INV-3: dir is owner-only

    wallet = twak_dir / "wallet.json"
    creds = twak_dir / "credentials.json"
    assert wallet.read_text() == WALLET_JSON  # written verbatim
    assert creds.read_text() == CREDENTIALS_JSON
    assert _mode(wallet) == 0o600  # INV-3: files are owner-only
    assert _mode(creds) == 0o600


def test_materialize_idempotent_never_overwrites(tmp_path):
    # INV-4: a second materialization with DIFFERENT content must leave the
    # existing key material untouched — a wallet can never be silently
    # replaced (and thereby re-point the on-chain identity).
    materialize_twak_home(
        wallet_json=WALLET_JSON, credentials_json=CREDENTIALS_JSON, home=tmp_path
    )
    materialize_twak_home(
        wallet_json='{"version":2,"DIFFERENT":true}',
        credentials_json='{"accessId":"other"}',
        home=tmp_path,
    )
    assert (tmp_path / ".twak" / "wallet.json").read_text() == WALLET_JSON
    assert (tmp_path / ".twak" / "credentials.json").read_text() == CREDENTIALS_JSON


def test_materialize_without_credentials_writes_only_wallet(tmp_path):
    materialize_twak_home(wallet_json=WALLET_JSON, home=tmp_path)
    assert (tmp_path / ".twak" / "wallet.json").read_text() == WALLET_JSON
    assert not (tmp_path / ".twak" / "credentials.json").exists()


def test_materialize_accepts_str_home_and_returns_path(tmp_path):
    returned = materialize_twak_home(wallet_json=WALLET_JSON, home=str(tmp_path))
    assert isinstance(returned, Path)
    assert returned == tmp_path  # ready to pass as TWAKProvider(home=...)
