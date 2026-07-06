"""Cold-start materialization of twak key material from a secrets manager.

In deployment the encrypted twak wallet does not live on disk ahead of time:
the runtime pulls it from the secret bundle (``TWAK_WALLET_JSON``, optionally
``TWAK_CREDENTIALS_JSON``) and writes it under a writable ``home`` before
constructing ``TWAKProvider(home=..., auto_create=False)``. This mirrors studio's
``ensure_keystore_materialized`` for the EVM keystore; the twak wallet is
simply the second wallet kind fed by the same pattern.

``wallet.json`` is portable: an AES-256-GCM-encrypted mnemonic with no
machine-binding fields (field-verified against the twak source), so twak
itself treats the file as the backup unit. It is only ever handled in this
encrypted form and lands with 0700 dirs / 0600 files (INV-3); materialization
is idempotent and never overwrites, so a wallet can only come from the bundle
— never be silently replaced or re-minted (INV-4).
"""

from __future__ import annotations

import os
from pathlib import Path

__all__ = ["materialize_twak_home"]


def materialize_twak_home(
    *,
    wallet_json: str,
    credentials_json: str | None = None,
    home: str | Path,
) -> Path:
    """Write twak key material under ``<home>/.twak/`` (idempotent).

    Writes ``wallet.json`` (and ``credentials.json`` when given) with 0600
    permissions inside a 0700 ``.twak`` directory. Existing files are left
    untouched — materialization never overwrites (INV-4). Returns the
    ``home`` path, ready to pass as ``TWAKProvider(home=...)``.

    Args:
        wallet_json: Content of the encrypted wallet file (the
            ``TWAK_WALLET_JSON`` bundle value), written verbatim.
        credentials_json: Optional content of the API-credentials file (the
            ``TWAK_CREDENTIALS_JSON`` bundle value).
        home: Writable directory to act as twak's ``$HOME``.
    """
    home = Path(home)
    twak_dir = home / ".twak"
    twak_dir.mkdir(parents=True, exist_ok=True)
    twak_dir.chmod(0o700)  # explicit: mkdir's mode is subject to the umask
    _write_secret(twak_dir / "wallet.json", wallet_json)
    if credentials_json is not None:
        _write_secret(twak_dir / "credentials.json", credentials_json)
    return home


def _write_secret(path: Path, content: str) -> None:
    """Create ``path`` with 0600 and write ``content``; skip if it exists."""
    if path.exists():
        return  # never overwrite materialized key material
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        os.fchmod(fd, 0o600)  # explicit: os.open's mode is subject to the umask
        with os.fdopen(fd, "w") as f:
            fd = -1  # fdopen owns the descriptor now
            f.write(content)
    finally:
        if fd != -1:
            os.close(fd)
