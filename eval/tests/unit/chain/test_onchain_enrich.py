"""chain.onchain.enrich: receipts from a FakeChainReader, selector and ABI decoding."""

from __future__ import annotations

import pytest

from bnbagent_eval.chain.onchain import abi_decode, enrich
from bnbagent_eval.testing.fakes import FakeChainReader
from bnbagent_eval.types import DecodedCall, LlmStep, Session, Trace, TxStep

AGENT = "0x" + "a1" * 20
TOKEN = "0x" + "70" * 20
ROUTER = "0x" + "e0" * 20
H1, H2 = "0x" + "01" * 32, "0x" + "02" * 32
APPROVE = "0x095ea7b3" + ROUTER[2:].rjust(64, "0") + f"{1000:064x}"
LOG = {"address": TOKEN, "topics": ["0x" + "11" * 32], "data": "0x"}

SWAP_ABI = [
    {"type": "event", "name": "Swapped", "inputs": []},
    {
        "type": "function",
        "name": "swap",
        "inputs": [
            {"name": "amountIn", "type": "uint256"},
            {"name": "path", "type": "address[]"},
            {"name": "", "type": "bytes32"},
            {
                "name": "opts",
                "type": "tuple",
                "components": [{"name": "a", "type": "uint8"}, {"name": "b", "type": "bool"}],
            },
        ],
    },
]


def session(*steps) -> Session:
    return Session(id="s", traces=(Trace(id="t", steps=(LlmStep(id="l"), *steps)),))


def reader() -> FakeChainReader:
    r = FakeChainReader(block=200)
    r.receipts[H1] = {
        "status": "0x1",
        "gasUsed": "0xb411",
        "blockNumber": "0x64",
        "from": AGENT.upper().replace("0X", "0x"),
        "logs": [LOG],
    }
    r.receipts[H2] = {"status": 0, "gasUsed": 30_000, "blockNumber": 150, "from": AGENT}
    r.transactions[H2] = {"input": APPROVE, "from": AGENT}
    return r


async def test_enrich_fills_receipts_and_decodes_erc20() -> None:
    s = session(
        TxStep(id="a", chain_id=97, to=TOKEN, calldata=APPROVE, tx_hash=H1),
        TxStep(id="b", chain_id=97, to=TOKEN, tx_hash=H2),  # calldata from get_transaction
        TxStep(id="c", chain_id=97, to=ROUTER, value=5),  # proposed: no hash
    )
    out = await enrich(s, reader())
    a, b, c = out.tx_steps()
    assert (a.status, a.gas_used, a.block_number, a.from_address) == (1, 46_097, 100, AGENT)
    assert a.logs == (LOG,)
    assert a.decoded == DecodedCall(function="approve", args={"spender": ROUTER, "amount": 1000})
    assert (b.status, b.block_number, b.calldata) == (0, 150, APPROVE)
    assert b.decoded.function == "approve"
    assert c == s.tx_steps()[2]
    assert s.tx_steps()[0].status is None  # input untouched (new Session)
    assert isinstance(next(out.steps()), LlmStep)


async def test_enrich_keeps_existing_decoded_and_missing_receipt() -> None:
    mine = DecodedCall(contract="Token", function="approve", args={"x": 1})
    s = session(
        TxStep(id="a", chain_id=97, to=TOKEN, calldata=APPROVE, tx_hash=H1, decoded=mine),
        TxStep(id="z", chain_id=97, to=TOKEN, tx_hash="0x" + "ff" * 32),
    )
    a, z = (await enrich(s, reader())).tx_steps()
    assert a.decoded is mine and a.status == 1
    assert z.status is None and z.decoded is None


async def test_enrich_pinned_block_is_deterministic() -> None:
    s = session(
        TxStep(id="a", chain_id=97, to=TOKEN, tx_hash=H1),
        TxStep(id="b", chain_id=97, to=TOKEN, tx_hash=H2),
    )
    r = reader()
    pinned = await enrich(s, r, block=120)
    a, b = pinned.tx_steps()
    assert a.status == 1 and b.status is None  # mined at 150 > pinned 120
    r.block = 10_000  # chain moves on: same result at the same pin
    assert await enrich(s, r, block=120) == pinned
    assert (await enrich(s, r)).tx_steps()[1].status == 0


async def test_enrich_without_reader_only_decodes() -> None:
    s = session(TxStep(id="a", chain_id=97, to=TOKEN, calldata=APPROVE, tx_hash=H1))
    (a,) = (await enrich(s, None)).tx_steps()
    assert a.status is None and a.decoded.function == "approve"


async def test_enrich_abi_decoding() -> None:
    eth_abi = pytest.importorskip("eth_abi")
    from bnbagent_eval.hashing import keccak_hex

    sel = keccak_hex("swap(uint256,address[],bytes32,(uint8,bool))")[:10]
    args = [7, [TOKEN, ROUTER], b"\x01" * 32, (3, True)]
    data = sel + eth_abi.encode(["uint256", "address[]", "bytes32", "(uint8,bool)"], args).hex()
    s = session(TxStep(id="a", chain_id=97, to=ROUTER, calldata=data))
    (a,) = (await enrich(s, None, abis={ROUTER.upper().replace("0X", "0x"): SWAP_ABI})).tx_steps()
    assert a.decoded == DecodedCall(
        function="swap",
        args={
            "amountIn": 7,
            "path": [TOKEN, ROUTER],
            "arg2": "0x" + "01" * 32,
            "opts": [3, True],
        },
    )
    assert abi_decode("0xdeadbeef", SWAP_ABI) is None
    # An ABI for another address does not apply; ERC-20 selectors still decode.
    s2 = session(TxStep(id="b", chain_id=97, to=TOKEN, calldata=APPROVE))
    (b,) = (await enrich(s2, None, abis={ROUTER: SWAP_ABI})).tx_steps()
    assert b.decoded.function == "approve"
