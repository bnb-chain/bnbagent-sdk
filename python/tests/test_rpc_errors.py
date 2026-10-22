"""Audit M10: a failure in verify_job's catch-all is retryable unless it is a revert,
so the funded-job watcher keeps a paid job (within max_attempts); the quote content
checks still return a permanent quote_invalid (audit M04)."""

import asyncio
import concurrent.futures
import time

import httpx
import pytest
import requests
from eth_keys.exceptions import BadSignature
from web3.exceptions import (
    BadFunctionCallOutput,
    BadResponseFormat,
    ContractLogicError,
    ProviderConnectionError,
    RequestTimedOut,
    TimeExhausted,
    TooManyRequests,
    Web3RPCError,
)

from bnbagent.core.rpc_errors import is_revert_error, is_transient_rpc_error
from bnbagent.erc8183.job_ops import funded_job_watcher
from bnbagent.exceptions import NetworkError, RpcRangeLimitError
from tests.test_erc8183_job_ops import (
    COMMERCE,
    _inject_client,
    _job,
    _make_ops,
    _make_wallet,
    _signed_description,
)

_REQUEST = httpx.Request("POST", "https://bsc-dataseed.binance.org/")


def _wrapped(cause: BaseException) -> BaseException:
    try:
        raise cause
    except BaseException as exc:
        try:
            raise RuntimeError("get_job_funded_block failed") from exc
        except RuntimeError as wrapped:
            return wrapped


TRANSIENT = {
    "Web3RPCError header not found": lambda: Web3RPCError("header not found"),
    "Web3RPCError missing trie node": lambda: Web3RPCError(
        "missing trie node",
        rpc_response={"jsonrpc": "2.0", "id": 1, "error": {"code": -32000, "message": "x"}},
    ),
    "RequestTimedOut": lambda: RequestTimedOut("request timed out"),
    "TooManyRequests": lambda: TooManyRequests("too many"),
    "ProviderConnectionError": lambda: ProviderConnectionError("cannot connect"),
    "TimeExhausted": lambda: TimeExhausted("exhausted"),
    "BadResponseFormat": lambda: BadResponseFormat("not JSON"),
    "httpx.ConnectError": lambda: httpx.ConnectError("refused", request=_REQUEST),
    "httpx.ReadTimeout": lambda: httpx.ReadTimeout("slow", request=_REQUEST),
    "httpx 502": lambda: httpx.HTTPStatusError(
        "502", request=_REQUEST, response=httpx.Response(502, request=_REQUEST)
    ),
    "requests.ConnectionError": lambda: requests.ConnectionError("refused"),
    "requests.HTTPError": lambda: requests.HTTPError("502 Server Error"),
    "ConnectionResetError": lambda: ConnectionResetError(104, "Connection reset by peer"),
    "asyncio.TimeoutError": lambda: asyncio.TimeoutError(),
    "futures.TimeoutError": lambda: concurrent.futures.TimeoutError(),
    "NetworkError": lambda: NetworkError("no route"),
    "RpcRangeLimitError": lambda: RpcRangeLimitError("-32005 limit exceeded"),
    "web3 6 payload": lambda: ValueError({"code": -32005, "message": "limit exceeded"}),
    "wrapped Web3RPCError": lambda: _wrapped(Web3RPCError("header not found")),
}

PERMANENT = {
    "ContractLogicError": lambda: ContractLogicError("execution reverted"),
    "revert raised from an RPC error": lambda: _wrapped_revert(),
    "BadFunctionCallOutput": lambda: BadFunctionCallOutput("empty"),
    "BadSignature": lambda: BadSignature(),
    "AttributeError": lambda: AttributeError("'str' object has no attribute 'get'"),
    "TypeError": lambda: TypeError("unsupported operand"),
    "ValueError": lambda: ValueError("signature has the wrong length"),
    "KeyError": lambda: KeyError("terms"),
}


def _wrapped_revert() -> BaseException:
    try:
        raise Web3RPCError("execution reverted")
    except Web3RPCError as exc:
        try:
            raise ContractLogicError("execution reverted") from exc
        except ContractLogicError as revert:
            return revert


@pytest.mark.parametrize("label", list(TRANSIENT))
def test_transport_and_rpc_errors_are_transient(label):
    assert is_transient_rpc_error(TRANSIENT[label]())


@pytest.mark.parametrize("label", list(PERMANENT))
def test_reverts_and_content_errors_are_not_transient(label):
    assert not is_transient_rpc_error(PERMANENT[label]())


def _signed_ops():
    now = int(time.time()) - 60
    description, provider = _signed_description(now)
    ops = _make_ops(wallet=_make_wallet(provider), allow_unsigned_jobs=False)
    client = _inject_client(ops)
    client.policy.dispute_window.return_value = 0
    client.commerce.address = COMMERCE
    client.w3.eth.chain_id = 97
    client.w3.eth.get_block.return_value = {"timestamp": now + 30}
    client.w3.eth.get_code.return_value = b""
    client.get_job.return_value = _job(provider=provider, description=description)
    return ops, client


@pytest.mark.parametrize("label", list(TRANSIENT))
async def test_verify_job_is_retryable_when_the_funded_block_scan_fails_transiently(label):
    ops, client = _signed_ops()
    client.get_job_funded_block.side_effect = TRANSIENT[label]()

    verdict = await ops.verify_job(1)

    assert verdict["valid"] is False
    assert verdict["error_code"] == "chain_unavailable"
    assert verdict["retryable"] is True


@pytest.mark.parametrize(
    "label", ["ContractLogicError", "revert raised from an RPC error", "BadFunctionCallOutput"]
)
def test_reverts_are_revert_errors(label):
    assert is_revert_error(PERMANENT[label]())


@pytest.mark.parametrize("label", ["AttributeError", "BadSignature", "ValueError"])
def test_other_errors_are_not_revert_errors(label):
    assert not is_revert_error(PERMANENT[label]())
    assert not is_revert_error(Web3RPCError("header not found"))


@pytest.mark.parametrize(
    "label", ["ContractLogicError", "revert raised from an RPC error", "BadFunctionCallOutput"]
)
async def test_verify_job_stays_permanent_for_a_revert(label):
    ops, client = _signed_ops()
    client.get_job_funded_block.side_effect = PERMANENT[label]()

    verdict = await ops.verify_job(1)

    assert verdict["error_code"] == "internal_error"
    assert not verdict.get("retryable")


@pytest.mark.parametrize("label", ["AttributeError", "BadSignature", "TypeError"])
async def test_verify_job_retries_an_unexpected_error(label):
    # The quote content checks return quote_invalid without raising; anything
    # else reaching the catch-all may pass on a retry, capped by the watcher.
    ops, client = _signed_ops()
    client.get_job_funded_block.side_effect = PERMANENT[label]()

    verdict = await ops.verify_job(1)

    assert verdict["error_code"] == "internal_error"
    assert verdict["retryable"] is True


async def test_watcher_caps_retries_of_an_unexpected_verification_error():
    ops, client = _signed_ops()
    client.get_job_funded_block.side_effect = AttributeError("boom")
    job = await ops.get_job(1)
    polls = 0
    stop = asyncio.Event()

    async def pending():
        nonlocal polls
        polls += 1
        if polls == 8:
            stop.set()
        return {"success": True, "jobs": [job] if polls == 1 else []}

    ops.get_pending_jobs = pending
    delivered = []

    async def work(verified_job):
        delivered.append(verified_job["jobId"])

    await asyncio.wait_for(
        funded_job_watcher(ops, work, interval=0.001, stop=stop, max_attempts=3), 2
    )

    assert client.get_job_funded_block.call_count == 3
    assert delivered == []


async def test_get_job_classifies_a_transport_error_as_chain_unavailable():
    ops, client = _signed_ops()
    client.get_job.side_effect = httpx.ConnectError("refused", request=_REQUEST)

    result = await ops.get_job(1)

    assert result["error_code"] == "chain_unavailable"
    assert result["retryable"] is True


async def test_watcher_keeps_a_paid_job_after_a_transient_failure_and_delivers_it():
    ops, client = _signed_ops()
    client.get_job_funded_block.side_effect = [Web3RPCError("header not found"), 123]
    job = await ops.get_job(1)
    polls = 0
    stop = asyncio.Event()

    async def pending():
        nonlocal polls
        polls += 1
        if polls == 3:
            stop.set()
        return {"success": True, "jobs": [job] if polls == 1 else []}

    ops.get_pending_jobs = pending
    delivered = []

    async def work(verified_job):
        delivered.append(verified_job["jobId"])

    await asyncio.wait_for(funded_job_watcher(ops, work, interval=0.001, stop=stop), 2)

    assert client.get_job_funded_block.call_count == 2
    assert delivered == [1]
