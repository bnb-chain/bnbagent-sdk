"""Port implementations under contract.

A *world* arranges state for one implementation and exposes its ports. Adapters add their own
worlds in ``port_worlds_<adapter>.py`` (merged automatically, like the metric catalogs).
Capabilities gate which contract checks apply (a read-only adapter skips write checks).
"""

from __future__ import annotations

import importlib
import pkgutil
from collections.abc import Callable
from pathlib import Path
from typing import Any, Protocol

from bnbagent_eval.ports import ChainPorts, JobView
from bnbagent_eval.testing.fakes import (
    FakeChainReader,
    FakeCommerce,
    FakePolicy,
    FakeRegistry,
    MemoryStorage,
)

VOTER = "0x" + "77" * 20
CAPABILITIES = {
    "registry.read",
    "registry.write",
    "commerce.read",
    "commerce.write",
    "policy.read",
    "policy.write",
    "storage",
    "reader",
}


class PortWorld(Protocol):
    name: str
    ports: ChainPorts
    capabilities: set[str]

    def add_agent(self, agent_id: int, registration: dict[str, Any], owner: str) -> None: ...

    def add_job(self, job: JobView) -> None: ...

    def set_receipt(self, tx_hash: str, receipt: dict[str, Any]) -> None: ...

    def set_balance(
        self, address: str, amount: int, *, token: str | None = None, block: int | None = None
    ) -> None: ...


class FakeWorld:
    name = "fakes"
    capabilities = set(CAPABILITIES)

    def __init__(self) -> None:
        storage = MemoryStorage()
        self.registry = FakeRegistry(storage=storage)
        self.commerce = FakeCommerce(storage=storage)
        self.policy = FakePolicy(voters=[VOTER], caller=VOTER)
        self.reader = FakeChainReader()
        self.ports = ChainPorts(
            registry=self.registry,
            commerce=self.commerce,
            policy=self.policy,
            storage=storage,
            reader=self.reader,
        )

    def add_agent(self, agent_id, registration, owner):
        self.registry.register(agent_id, registration, owner=owner)

    def add_job(self, job):
        self.commerce.add_job(job)

    def set_receipt(self, tx_hash, receipt):
        self.reader.receipts[tx_hash] = receipt

    def set_balance(self, address, amount, *, token=None, block=None):
        self.reader.balances[(address.lower(), token.lower() if token else None, block)] = amount


WORLDS: dict[str, Callable[[], PortWorld]] = {"fakes": FakeWorld}


def _merge() -> None:
    for info in pkgutil.iter_modules([str(Path(__file__).parent)]):
        if info.name.startswith("port_worlds_"):
            module = importlib.import_module(f"{__package__}.{info.name}")
            WORLDS.update(module.WORLDS)


_merge()
