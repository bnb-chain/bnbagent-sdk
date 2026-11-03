#!/usr/bin/env bash
# Separability check: copy eval/ to a temp directory outside the monorepo,
# install it there with no extras in a fresh venv, confirm no BNB / web3 / heavy module is
# imported, then run the test suite from the copy (extras skip).
set -euo pipefail
src="$(cd "$(dirname "$0")/.." && pwd)"  # eval/
work="$(mktemp -d)"
pkg="$work/eval"
mkdir -p "$pkg"
tar -C "$src" --exclude='__pycache__' --exclude='.pytest_cache' --exclude='*.egg-info' \
    --exclude='dist' --exclude='build' -cf - . | tar -C "$pkg" -xf -
venv="$work/core"
"${PYTHON:-python3}" -m venv "$venv"
"$venv/bin/pip" install -q "$pkg"
(cd "$(mktemp -d)" && "$venv/bin/python" -I "$pkg/scripts/core_smoke.py")
"$venv/bin/pip" install -q "$pkg[dev]"
cd "$pkg"
"$venv/bin/python" -m pytest -q -p no:cacheprovider tests
echo "core-only (out-of-repo copy at $pkg): ok"
