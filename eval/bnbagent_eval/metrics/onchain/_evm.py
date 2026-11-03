"""EVM helpers shared by onchain.* metrics and ``chain.onchain``: ints, receipts, ERC-20 calls.

Pure Python (no eth-abi): ERC-20 ``approve`` / ``transfer`` / ``transferFrom`` (and
``increaseAllowance`` / ``setApprovalForAll``) are decoded from their well-known selectors.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ...types.trajectory import DecodedCall, TxStep

MAX_UINT256 = 2**256 - 1

# selector -> (function name, [(arg name, abi type)])
ERC20_SELECTORS: dict[str, tuple[str, list[tuple[str, str]]]] = {
    "0x095ea7b3": ("approve", [("spender", "address"), ("amount", "uint256")]),
    "0xa9059cbb": ("transfer", [("to", "address"), ("amount", "uint256")]),
    "0x23b872dd": (
        "transferFrom",
        [("from", "address"), ("to", "address"), ("amount", "uint256")],
    ),
    "0x39509351": ("increaseAllowance", [("spender", "address"), ("amount", "uint256")]),
    "0xa22cb465": ("setApprovalForAll", [("operator", "address"), ("approved", "bool")]),
}

TOPIC_APPROVAL = "0x8c5be1e5ebec7d5bd14f71427d1e84f3dd0314c0f7b2291e5b200ac8c7c3b925"
TOPIC_TRANSFER = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"

# Argument aliases used by common ABIs (OpenZeppelin names the amount ``value``).
_ALIASES = {
    "spender": ("spender", "_spender", "operator", "guy"),
    "to": ("to", "_to", "recipient", "dst"),
    "from": ("from", "_from", "sender", "src"),
    "amount": ("amount", "value", "_value", "_amount", "wad", "addedValue"),
}


def to_int(value: Any) -> int | None:
    """An int from an int, a decimal string or a 0x-hex string; None if not numeric."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str):
        text = value.strip()
        try:
            return int(text, 16) if text.lower().startswith(("0x", "-0x")) else int(text)
        except ValueError:
            return None
    return None


def same_address(a: Any, b: Any) -> bool:
    return isinstance(a, str) and isinstance(b, str) and a.lower() == b.lower()


def decode_erc20(calldata: str | None) -> DecodedCall | None:
    """Decode a well-known ERC-20 call from its selector, or None."""
    if not calldata or len(calldata) < 10:
        return None
    data = calldata.lower().removeprefix("0x")
    entry = ERC20_SELECTORS.get("0x" + data[:8])
    if entry is None:
        return None
    name, params = entry
    body = data[8:]
    if len(body) < 64 * len(params):
        return None
    args: dict[str, Any] = {}
    for i, (arg, typ) in enumerate(params):
        word = body[64 * i : 64 * (i + 1)]
        try:
            n = int(word, 16)
        except ValueError:
            return None
        if typ == "address":
            args[arg] = "0x" + word[24:]
        elif typ == "bool":
            args[arg] = n != 0
        else:
            args[arg] = n
    return DecodedCall(function=name, args=args)


def decoded_call(step: TxStep) -> DecodedCall | None:
    """The step's decoded call: ``step.decoded`` if it names a function, else ERC-20 selectors."""
    if step.decoded is not None and step.decoded.function:
        return step.decoded
    return decode_erc20(step.calldata)


def function_name(call: DecodedCall | None) -> str | None:
    if call is None or not call.function:
        return None
    return call.function.split("(")[0]


def arg(call: DecodedCall, name: str) -> Any:
    """An argument by canonical name (``spender`` / ``to`` / ``from`` / ``amount``)."""
    for key in _ALIASES.get(name, (name,)):
        if key in call.args:
            return call.args[key]
    return None


def approvals(step: TxStep) -> list[dict[str, Any]]:
    """Approvals made by a step: from its decoded call, else from ERC-20 Approval logs."""
    call = decoded_call(step)
    fn = function_name(call)
    if fn in ("approve", "increaseAllowance"):
        return [{"token": step.to, "spender": arg(call, "spender"), "amount": arg(call, "amount")}]
    if fn == "setApprovalForAll":
        on = call.args.get("approved", call.args.get("_approved"))
        return [
            {"token": step.to, "spender": arg(call, "spender"), "amount": bool(on), "all": True}
        ]
    found = []
    for log in step.logs:
        topics = [str(t).lower() for t in log.get("topics", ())]
        if len(topics) == 3 and topics[0] == TOPIC_APPROVAL:
            found.append(
                {
                    "token": log.get("address"),
                    "spender": "0x" + topics[2][-40:],
                    "amount": to_int(log.get("data") or "0x0"),
                    "source": "log",
                }
            )
    return found


def token_transfer_amount(step: TxStep, token: str) -> int:
    """Amount of ``token`` moved by an ERC-20 ``transfer`` / ``transferFrom`` call."""
    if not same_address(step.to, token):
        return 0
    call = decoded_call(step)
    if function_name(call) not in ("transfer", "transferFrom"):
        return 0
    return to_int(arg(call, "amount")) or 0


def receipt_status(receipt: Mapping[str, Any] | None) -> int | None:
    return None if receipt is None else to_int(receipt.get("status"))


def receipt_gas(receipt: Mapping[str, Any] | None) -> int | None:
    return None if receipt is None else to_int(receipt.get("gasUsed", receipt.get("gas_used")))
