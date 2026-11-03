"""Packaging and isolation."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

FORBIDDEN = [
    "bnbagent",
    "web3",
    "eth_account",
    "boto3",
    "ragas",
    "deepeval",
    "fmeval",
    "pyrit",
    "fastapi",
    "langfuse",
    "torch",
    "lxml",
    "jsonschema",
    "httpx",
]
EVAL_DIR = Path(__file__).resolve().parents[2]


def test_import_pulls_in_no_extras_and_no_network() -> None:
    code = (
        "import socket, sys, json\n"
        "def deny(*a, **k): raise RuntimeError('network used at import')\n"
        "socket.socket.connect = deny\n"
        "import bnbagent_eval\n"
        f"print(json.dumps([m for m in {FORBIDDEN!r} if m in sys.modules]))\n"
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    assert json.loads(out.stdout) == []


def test_import_linter_contracts() -> None:
    exe = shutil.which("lint-imports", path=str(Path(sys.executable).parent))
    if exe is None:
        pytest.skip("import-linter not installed")
    result = subprocess.run([exe], cwd=EVAL_DIR, capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
