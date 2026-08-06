"""Build hook: vendor the shared contract ABIs into the package.

The ABIs are language-neutral (shared with the TypeScript SDK), so the single
source of truth lives at the repo root ``abis/`` — OUTSIDE this Python project.
`pip install` must still ship them, so at build time we copy ``../abis/*.json``
into ``bnbagent/abis/`` (git-ignored; pulled in via ``artifacts`` in
pyproject.toml).

When building a wheel FROM an sdist, the root ``../abis`` no longer exists, but
the sdist already carries ``bnbagent/abis/`` — so the copy is a no-op and the
files ship regardless. Idempotent and safe to run on every build.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from hatchling.builders.hooks.plugin.interface import BuildHookInterface


class VendorAbisHook(BuildHookInterface):
    PLUGIN_NAME = "vendor-abis"

    def initialize(self, version: str, build_data: dict) -> None:
        root = Path(self.root)  # the python/ project dir (where pyproject.toml is)
        source = root.parent / "abis"  # repo-root/abis — single source of truth
        dest = root / "bnbagent" / "abis"
        if not source.is_dir():
            return  # building from sdist: bnbagent/abis/ is already populated
        dest.mkdir(parents=True, exist_ok=True)
        for abi in source.glob("*.json"):
            shutil.copy2(abi, dest / abi.name)
