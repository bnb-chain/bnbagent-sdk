"""Shared loader for the contract ABIs.

The ABI JSONs are language-neutral (shared with the TypeScript SDK), so the
single source of truth lives at the repo-root ``abis/``. At build time
``python/hatch_build.py`` vendors a copy into ``bnbagent/abis/`` so an installed
wheel/sdist ships them. This loader reads that in-package copy when present and
falls back to the repo-root ``abis/`` for an editable / source checkout.
"""

from __future__ import annotations

import json
from pathlib import Path

#: In-package copy shipped in the wheel/sdist (``bnbagent/abis/``).
_PKG_ABIS = Path(__file__).resolve().parent.parent / "abis"
#: Repo-root single source of truth (``<repo>/abis/``), used from a source tree.
_REPO_ABIS = Path(__file__).resolve().parents[3] / "abis"


def _abis_dir() -> Path:
    return _PKG_ABIS if _PKG_ABIS.is_dir() else _REPO_ABIS


def load_abi(name: str) -> list:
    """Load a contract ABI JSON by file name, e.g. ``load_abi("ERC20.json")``."""
    return json.loads((_abis_dir() / name).read_text())
