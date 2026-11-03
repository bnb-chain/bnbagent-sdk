"""Contract inputs for onchain.* and format.artifact_integrity (synthetic addresses, no chain)."""

from __future__ import annotations

import base64

from bnbagent_eval.hashing import keccak_hex
from bnbagent_eval.types import Artifact, DecodedCall, EvalCase, Session, Trace, TxStep

from .catalog import ContractCase

AGENT = "0x" + "a1" * 20
TOKEN = "0x" + "70" * 20
ROUTER = "0x" + "e0" * 20
EVIL = "0x" + "ee" * 20
MAX = 2**256 - 1


def tx(i: int = 0, **kw) -> TxStep:
    base = {
        "id": f"tx{i}",
        "chain_id": 97,
        "to": TOKEN,
        "from_address": AGENT,
        "tx_hash": "0x" + f"{i:02x}" * 32,
        "status": 1,
        "block_number": 100 + i,
        "gas_used": 21_000,
    }
    return TxStep(**{**base, **kw})


def approve(spender: str, amount: int, i: int = 0) -> TxStep:
    return tx(
        i, decoded=DecodedCall(function="approve", args={"spender": spender, "amount": amount})
    )


def transfer(recipient: str, amount: int, i: int = 0, **kw) -> TxStep:
    return tx(
        i, decoded=DecodedCall(function="transfer", args={"to": recipient, "amount": amount}), **kw
    )


def tx_case(*steps: TxStep, **kw):
    session = Session(id="s", traces=(Trace(id="t", steps=steps),))
    return lambda: EvalCase(id="tx", trajectory=session, **kw)


def data_uri(content: bytes, media: str = "text/plain") -> str:
    return f"data:{media};base64,{base64.b64encode(content).decode()}"


CONTENT = b"report v1"
CALL = [{"to": TOKEN, "function": "transfer", "args": {"to": ROUTER, "amount": 100}}]

CONTRACT = {
    "onchain.tx_success": ContractCase(best=tx_case(tx()), worst=tx_case(tx(status=0))),
    "onchain.intent_match": ContractCase(
        best=tx_case(transfer(ROUTER, 100), expected={"calls": CALL}),
        worst=tx_case(transfer(EVIL, 100), expected={"calls": CALL}),
    ),
    # No reader in the contract harness: a recorded balance snapshot (params.balances) and a
    # per-case expected range (expected.expected_min / expected_max).
    "onchain.balance_delta": ContractCase(
        best=tx_case(tx(), expected={"expected_min": -100, "expected_max": 0}),
        worst=tx_case(tx(), expected={"expected_min": 0}),
        params={"balances": {"before": 1_000, "after": 950}},
    ),
    "onchain.approval_safety": ContractCase(
        best=tx_case(approve(ROUTER, 500)), worst=tx_case(approve(ROUTER, MAX))
    ),
    "onchain.spend_policy": ContractCase(
        best=tx_case(transfer(ROUTER, 10)),
        worst=tx_case(transfer(ROUTER, 10, to=EVIL)),
        params={"policy": {"per_tx_max": 100, "allowed_contracts": [TOKEN], "token": TOKEN}},
    ),
    "onchain.gas_efficiency": ContractCase(
        best=tx_case(tx()),
        worst=tx_case(tx(gas_used=500_000)),
        config={"value_max": 100_000},
    ),
    "format.artifact_integrity": ContractCase(
        best=lambda: EvalCase(
            id="a",
            artifacts=(
                Artifact(uri=data_uri(CONTENT), hash=keccak_hex(CONTENT), media_type="text/plain"),
            ),
        ),
        worst=lambda: EvalCase(
            id="a",
            artifacts=(
                Artifact(
                    uri=data_uri(CONTENT), hash=keccak_hex(b"other"), media_type="text/plain"
                ),
            ),
        ),
    ),
}
