"""Opt-in ``.env`` loading for applications built on the SDK.

The SDK never calls :func:`load_env` at import time (library discipline —
a library must not mutate the process environment as a side effect of being
imported). Applications and examples opt in explicitly, typically as the
first line of their entrypoint.
"""

from __future__ import annotations

from pathlib import Path

from dotenv import load_dotenv

__all__ = ["load_env"]


def load_env(root: str | Path | None = None) -> list[Path]:
    """Load ``.env.local`` then ``.env`` from ``root``, never overriding.

    Both files are loaded with ``load_dotenv(path, override=False)``, in
    that exact order. The ordering is the whole trick: with
    ``override=False`` the *first* loader to set a key wins, so loading the
    local file first yields the precedence

        real environment  >  .env.local  >  .env

    (Next.js semantics). The naive alternative — ``.env`` first with
    ``override=True`` on the local file — would let a stale dev
    ``.env.local`` left in an image stomp a deployment-injected secret such
    as ``TWAK_WALLET_PASSWORD``: an incident path, not a style choice.

    ``root`` defaults to ``Path.cwd()``; there is deliberately no upward
    directory search (the SDK has no project-root marker — callers anchor
    the lookup explicitly).

    Returns:
        The list of files actually loaded (existing files, in load order).
    """
    base = Path(root) if root is not None else Path.cwd()
    loaded: list[Path] = []
    for name in (".env.local", ".env"):
        path = base / name
        if path.is_file():
            load_dotenv(path, override=False)
            loaded.append(path)
    return loaded
