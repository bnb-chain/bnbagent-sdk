"""``serve`` builds the built-in X402Gate from CLI options."""

from __future__ import annotations

import pytest

from bnbagent_eval.cli import _gate_from_args, build_parser
from bnbagent_eval.exceptions import EvalError

pytest.importorskip("eth_account", reason="needs the [evm] extra")
pytest.importorskip("bnbagent", reason="needs the [bnbagent] extra")

PAY_TO = "0x" + "a1" * 20


def _args(*extra: str):
    return build_parser().parse_args(["serve", *extra])


def test_free_service_without_pay_to(tmp_path) -> None:
    assert _gate_from_args(_args(), str(tmp_path / "s.db")) is None


def test_pay_to_needs_chain_and_facilitator(tmp_path) -> None:
    with pytest.raises(EvalError, match="facilitator"):
        _gate_from_args(_args("--pay-to", PAY_TO, "--chain-id", "97"), str(tmp_path / "s.db"))


def test_pay_to_builds_x402_gate(tmp_path) -> None:
    from bnbagent_eval.server.payment import X402Gate

    gate = _gate_from_args(
        _args(
            "--pay-to", PAY_TO, "--chain-id", "97",
            "--facilitator-url", "https://facilitator.example.com",
            "--price-base", "5", "--price-per-judge-call", "2",
        ),
        str(tmp_path / "s.db"),
    )  # fmt: skip
    assert isinstance(gate, X402Gate) and gate.chain_id == 97
    assert gate.price_fn({}, 3) == 5 + 3 * 2
    assert (tmp_path / "s.x402.db").exists()


def test_pay_to_with_assets_file_needs_no_sdk(tmp_path, monkeypatch) -> None:
    import sys

    from bnbagent_eval.server.payment import X402Gate

    monkeypatch.setitem(sys.modules, "bnbagent", None)  # as if the SDK were not installed
    assets = tmp_path / "assets.yaml"
    assets.write_text(
        "- {chain_id: 8453, address: '0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913', symbol: USDC,"
        " decimals: 6, methods: [eip3009], eip712_domain: {name: USD Coin, version: '2'}}\n"
    )
    args = ["--pay-to", PAY_TO, "--chain-id", "8453", "--facilitator-url",
            "https://facilitator.example.com", "--assets", str(assets)]  # fmt: skip
    gate = _gate_from_args(_args(*args), str(tmp_path / "s.db"))
    assert isinstance(gate, X402Gate) and gate.assets.assets(8453)[0].symbol == "USDC"
    from bnbagent_eval.exceptions import EvalError

    with pytest.raises(EvalError, match="no asset for chain"):
        _gate_from_args(_args(*args[:3], "1", *args[4:]), str(tmp_path / "s.db"))
