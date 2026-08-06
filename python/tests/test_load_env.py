"""Tests for bnbagent.core.env.load_env (design §4.3, review fix C-1).

Precedence contract (Next.js semantics, both files override=False):

    real environment  >  .env.local  >  .env

The local-first load order is the safety property: loading ``.env`` first
with override on the local file would let a stale dev ``.env.local`` stomp a
deployment-injected secret (e.g. ``TWAK_WALLET_PASSWORD``).
"""

from __future__ import annotations

import os

import pytest

from bnbagent import load_env

KEY = "BNBAGENT_TEST_LOAD_ENV_KEY"


@pytest.fixture(autouse=True)
def sandbox_environ(monkeypatch):
    """Swap os.environ for a plain copy so load_dotenv writes never leak.

    python-dotenv mutates ``os.environ`` directly; replacing the attribute
    with a dict copy sandboxes both its writes and the test's own setenv.
    """
    monkeypatch.setattr(os, "environ", dict(os.environ))
    os.environ.pop(KEY, None)


def test_env_local_wins_over_env(tmp_path):
    (tmp_path / ".env").write_text(f"{KEY}=a\n")
    (tmp_path / ".env.local").write_text(f"{KEY}=b\n")
    loaded = load_env(tmp_path)
    # override=False + local-first: the first loader to set the key wins.
    assert os.environ[KEY] == "b"
    # Returned in load order: .env.local first, then .env.
    assert loaded == [tmp_path / ".env.local", tmp_path / ".env"]


def test_real_environment_wins_over_both_files(tmp_path):
    (tmp_path / ".env").write_text(f"{KEY}=a\n")
    (tmp_path / ".env.local").write_text(f"{KEY}=b\n")
    os.environ[KEY] = "c"  # deployment-injected value
    loaded = load_env(tmp_path)
    assert os.environ[KEY] == "c"  # survives both files (override=False)
    assert len(loaded) == 2  # the files were still loaded (for other keys)


def test_env_only_loads_and_sets(tmp_path):
    (tmp_path / ".env").write_text(f"{KEY}=a\n")
    loaded = load_env(tmp_path)
    assert os.environ[KEY] == "a"
    assert loaded == [tmp_path / ".env"]


def test_missing_files_returns_empty_list(tmp_path):
    assert load_env(tmp_path) == []
    assert KEY not in os.environ


def test_no_upward_directory_search(tmp_path):
    # A .env in the parent must NOT be picked up when loading from a child:
    # the SDK has no project-root marker, so callers anchor the root explicitly.
    (tmp_path / ".env").write_text(f"{KEY}=parent\n")
    child = tmp_path / "child"
    child.mkdir()
    assert load_env(child) == []
    assert KEY not in os.environ
