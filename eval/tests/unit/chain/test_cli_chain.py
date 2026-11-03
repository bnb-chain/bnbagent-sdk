"""CLI chain commands with in-memory ports (no SDK, no chain)."""

from __future__ import annotations

import asyncio
import json

import pytest

from bnbagent_eval import cli
from bnbagent_eval.ports import ChainPorts
from bnbagent_eval.testing.fakes import FakePolicy, FakeRegistry, MemoryStorage

from .test_workflows import REGISTRATION, _registration_response, _submitted

pytest.importorskip("eth_account", reason="needs the [evm] extra")


def test_cli_publish(tmp_path, monkeypatch, capsys) -> None:
    storage = MemoryStorage()
    registry = FakeRegistry(storage=storage)
    registry.register(5, REGISTRATION)
    resp = asyncio.run(_registration_response(registry))
    path = tmp_path / "resp.json"
    path.write_text(json.dumps(resp.to_dict()))
    monkeypatch.setattr(
        cli, "_chain_ports", lambda args: ChainPorts(registry=registry, storage=storage)
    )
    assert cli.main(["publish", str(path), "--agent-id", "5"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["success"] and out["agent_uri"].startswith("data:")
    assert cli.main(["publish", str(path), "--agent-id", "6"]) == 1  # subject mismatch


def test_cli_review_client_and_voter(monkeypatch, capsys, signer) -> None:
    import time

    storage, commerce, _, _, _ = asyncio.run(
        _submitted("Ethereum is a blockchain", clock_value=time.time())
    )
    policy = FakePolicy(voters=[signer.address], caller=signer.address)
    ports = ChainPorts(commerce=commerce, policy=policy, storage=storage, signer=signer)
    monkeypatch.setattr(cli, "_chain_ports", lambda args: ports)

    class Client:  # the suite reference ("fees") is injected like in the workflow tests
        def __init__(self, *a, **k):
            from .test_workflows import _gate

            self._inner = _gate(commerce, storage, "x").client

        async def evaluate(self, request):
            return await self._inner.evaluate(request)

    monkeypatch.setattr(cli, "EvalClient", Client)
    assert cli.main(["review", "1"]) == 0
    dry = json.loads(capsys.readouterr().out)
    assert dry["action"] == "dispute" and dry["tx_hash"] is None and 1 not in policy.disputed
    assert cli.main(["review", "1", "--act"]) == 0
    assert json.loads(capsys.readouterr().out)["tx_hash"] and 1 in policy.disputed
    assert cli.main(["review", "1", "--as", "voter", "--act"]) == 0
    voted = json.loads(capsys.readouterr().out)
    assert voted["action"] == "vote_reject" and voted["tx_hash"]


def test_cli_watch_needs_wallet(monkeypatch, capsys) -> None:
    for key in ("PRIVATE_KEY", "WALLET_PASSWORD", "WALLET_KIND", "WALLET_ADDRESS"):
        monkeypatch.delenv(key, raising=False)
    code = cli.main(["watch", "--work", "json:dumps"])
    assert code == 64 and "error:" in capsys.readouterr().err


def test_cli_run_with_subject_then_publish(tmp_path, monkeypatch, capsys) -> None:
    storage = MemoryStorage()
    registry = FakeRegistry(storage=storage)
    registry.register(5, REGISTRATION)
    suite = tmp_path / "suite.yaml"
    suite.write_text(
        "name: reg\nversion: '1'\nmetrics:\n  - id: text.contains\n    required: true\n"
    )
    cases = tmp_path / "cases.json"
    cases.write_text(json.dumps([{"id": "1", "output": "BNB fees are low", "expected": "fees"}]))
    out = tmp_path / "resp.json"
    subject = f"agent:{registry.chain_id}:{registry.registry_address}:5:v1.2"
    argv = ["run", "--suite", str(suite), "--cases", str(cases), "--out", str(out)]
    assert cli.main([*argv, "--stage", "registration", "--subject", subject]) == 0
    body = json.loads(out.read_text())
    assert body["subject"]["agent_id"] == 5 and body["subject"]["agent_version"] == "v1.2"
    monkeypatch.setattr(
        cli, "_chain_ports", lambda args: ChainPorts(registry=registry, storage=storage)
    )
    assert cli.main(["publish", str(out), "--agent-id", "5"]) == 0
    published = json.loads(capsys.readouterr().out)
    assert published["success"] and published["error"] is None
    # without --subject the same output is refused
    assert cli.main([*argv, "--stage", "registration"]) == 0
    assert cli.main(["publish", str(out), "--agent-id", "5"]) == 1
    assert "subject" in json.loads(capsys.readouterr().out)["error"]


def test_cli_run_job_subject(tmp_path, capsys) -> None:
    from bnbagent_eval.chain.manifest import content_hash

    suite = tmp_path / "suite.yaml"
    suite.write_text("name: s\nversion: '1'\nmetrics:\n  - id: text.contains\n")
    one = tmp_path / "one.json"
    one.write_text(json.dumps([{"id": "1", "output": "BNB fees are low", "expected": "fees"}]))
    two = tmp_path / "two.json"
    two.write_text(json.dumps([{"id": str(i), "output": "x"} for i in range(2)]))
    out = tmp_path / "resp.json"
    provider = "0x" + "ab" * 20
    subject = f"job:97:0x{'8183' * 10}:7:{provider}"
    argv = ["run", "--suite", str(suite), "--out", str(out), "--subject", subject]
    assert cli.main([*argv, "--cases", str(one)]) == 0
    s = json.loads(out.read_text())["subject"]
    assert s["type"] == "job" and s["job_id"] == 7 and s["provider"] == provider
    assert s["output_hash"] == content_hash("BNB fees are low")
    assert cli.main([*argv, "--cases", str(two)]) == 64  # ambiguous output_hash
    assert "exactly one case" in capsys.readouterr().err
    bad = ["run", "--suite", str(suite), "--cases", str(one), "--subject", "agent:97:0xab"]
    assert cli.main(bad) == 64
    assert cli.main([*bad[:-1], "wallet:1"]) == 64


_PORTS_FOR_FACTORY: dict = {}


def make_test_ports():
    return _PORTS_FOR_FACTORY["ports"]


def test_cli_publish_with_ports_factory(tmp_path, capsys) -> None:
    storage = MemoryStorage()
    registry = FakeRegistry(storage=storage)
    registry.register(5, REGISTRATION)
    resp = asyncio.run(_registration_response(registry))
    path = tmp_path / "resp.json"
    path.write_text(json.dumps(resp.to_dict()))
    _PORTS_FOR_FACTORY["ports"] = ChainPorts(registry=registry, storage=storage)
    factory = f"{__name__}:make_test_ports"
    assert cli.main(["publish", str(path), "--agent-id", "5", "--ports", factory]) == 0
    assert json.loads(capsys.readouterr().out)["success"]
    assert cli.main(["publish", str(path), "--agent-id", "5", "--ports", "no.such:thing"]) == 64
