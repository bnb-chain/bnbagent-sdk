"""Shared fixtures."""

from __future__ import annotations

import socket

import pytest

from bnbagent_eval.types import EvalCase, MetricConfig, Suite

_real_connect = socket.socket.connect


def _guarded_connect(self, address):  # type: ignore[no-untyped-def]
    host = address[0] if isinstance(address, tuple) else address
    if isinstance(host, str) and (host in ("localhost", "::1") or host.startswith("127.")):
        return _real_connect(self, address)
    raise RuntimeError(f"network access is disabled in tests (tried {address!r})")


@pytest.fixture(autouse=True)
def no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Only loopback connections are allowed in the default test run."""
    monkeypatch.setattr(socket.socket, "connect", _guarded_connect)


@pytest.fixture
def suite_factory():
    def make(*metrics: MetricConfig, **kwargs) -> Suite:
        return Suite(
            name=kwargs.pop("name", "test"),
            version=kwargs.pop("version", "1"),
            metrics=tuple(metrics),
            **kwargs,
        )

    return make


@pytest.fixture
def case_factory():
    def make(i: int | str = 1, **kwargs) -> EvalCase:
        return EvalCase(id=str(i), **kwargs)

    return make
