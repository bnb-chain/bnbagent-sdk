"""The ``jsonrpc`` port world: the read-only adapter against an in-process fake JSON-RPC node.

``FakeNode`` is an ``httpx.MockTransport`` handler. It decodes ``eth_call`` calldata by function
selector (using the adapter's vendored ABIs) and answers with ABI-encoded values from in-memory
state; it also serves receipts, transactions, balances, blocks, logs and HTTP documents.
Needs ``eth-abi`` (``[evm]``); without it ``WORLDS`` is empty so the fakes world still runs.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

import httpx

from bnbagent_eval._optional import available
from bnbagent_eval.chain.manifest import manifest_hash
from bnbagent_eval.ports import JobView
from bnbagent_eval.testing.fakes import encode_data_uri

RPC_URL = "https://rpc.bsc-testnet.example/"
CHAIN_ID = 97
ADDRESSES = {
    "identity_registry": "0x" + "8004" * 10,
    "commerce": "0x" + "8183" * 10,
    "policy": "0x" + "bb" * 20,
}
GENESIS_TS = 1_700_000_000
BLOCK_TIME = 3
PUBLIC_IP = "93.184.216.34"
ZERO = "0x" + "00" * 20
QUANTITIES = {"status", "gasUsed", "blockNumber", "cumulativeGasUsed", "transactionIndex"}
STATUSES = ("open", "funded", "submitted", "completed", "rejected", "expired")


async def public_resolver(host: str) -> list[str]:
    """Test resolver: every DNS name is public (IP literals are checked directly)."""
    return [PUBLIC_IP]


def _hexify(obj: Mapping[str, Any]) -> dict[str, Any]:
    out = {}
    for key, value in obj.items():
        if key in QUANTITIES and isinstance(value, int):
            out[key] = hex(value)
        elif key == "logs" and isinstance(value, list):
            out[key] = [_hexify(v) for v in value]
        else:
            out[key] = value
    return out


class Revert(Exception):
    pass


class FakeNode:
    """In-memory chain answering JSON-RPC (and plain HTTP document GETs)."""

    def __init__(self, *, head: int = 1_000, addresses: Mapping[str, str] = ADDRESSES):
        from bnbagent_eval.integrations.jsonrpc import abi

        self.abi = abi
        self.head = head
        self.addresses = {k: v.lower() for k, v in addresses.items()}
        self.uris: dict[int, str] = {}
        self.owners: dict[int, str] = {}
        self.metadata: dict[tuple[int, str], bytes] = {}
        self.jobs: dict[int, tuple[Any, ...]] = {}
        self.receipts: dict[str, dict[str, Any]] = {}
        self.transactions: dict[str, dict[str, Any]] = {}
        self.balances: dict[tuple[str, str | None, int | None], int] = {}
        self.dispute_window = 3600
        self.voters: set[str] = set()
        self.disputed: set[int] = set()
        self.votes: dict[int, set[str]] = {}
        self.job_policies: dict[int, str] = {}
        self.logs: list[dict[str, Any]] = []
        self.documents: dict[str, bytes] = {}
        self.requests: list[tuple[str, list[Any]]] = []
        self.http_gets: list[str] = []
        self._routes: dict[tuple[str, str], tuple[Any, str]] = {}
        for key, name in (
            ("identity_registry", abi.IDENTITY),
            ("commerce", abi.COMMERCE),
            ("policy", abi.POLICY),
            ("router", abi.ROUTER),
        ):
            if key in self.addresses:
                contract = abi.load(name)
                for fn in contract.functions:
                    self._routes[(self.addresses[key], contract.selector(fn))] = (contract, fn)
        self._erc20 = abi.load(abi.ERC20)

    # ------------------------------------------------------------------ state helpers

    def timestamp(self, block: int) -> int:
        return GENESIS_TS + BLOCK_TIME * block

    def set_job(self, job: JobView, *, hook: str = ZERO) -> None:
        deliverable = bytes.fromhex(job.deliverable[2:]) if job.deliverable else bytes(32)
        self.jobs[job.job_id] = (
            job.job_id,
            job.client,
            job.provider,
            job.evaluator or ZERO,
            job.description or "",
            job.budget or 0,
            job.expired_at or 0,
            STATUSES.index(job.status),
            hook,
            job.submitted_at or 0,
            deliverable,
        )

    def submit(
        self,
        job_id: int,
        manifest: Mapping[str, Any],
        url: str,
        *,
        block: int = 900,
        opt_params: bytes | None = None,
        policy: str | None = None,
    ) -> str:
        """Mark a job submitted (hash of ``manifest``) and emit ``JobInitialised``."""
        digest = manifest_hash(manifest)
        job = list(self.jobs[job_id])
        job[7], job[9], job[10] = 2, self.timestamp(block), bytes.fromhex(digest[2:])
        self.jobs[job_id] = tuple(job)
        if opt_params is None:
            opt_params = json.dumps({"deliverable_url": url}, separators=(",", ":")).encode()
        policy_abi = self.abi.load(self.abi.POLICY)
        import eth_abi

        self.logs.append(
            {
                "address": (policy or self.addresses["policy"]),
                "topics": [policy_abi.event_topic("JobInitialised"), self.abi.topic_uint(job_id)],
                "data": "0x"
                + eth_abi.encode(
                    ["bytes32", "uint64", "bytes"],
                    [bytes.fromhex(digest[2:]), self.timestamp(block), opt_params],
                ).hex(),
                "blockNumber": hex(block),
                "logIndex": "0x0",
                "transactionIndex": "0x0",
                "transactionHash": "0x" + f"{job_id:064x}",
            }
        )
        return digest

    def put_document(self, url: str, doc: Any) -> None:
        self.documents[url] = doc if isinstance(doc, bytes) else json.dumps(doc).encode()

    # ------------------------------------------------------------------ transport

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)

    def client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=self.transport())

    def handle(self, request: httpx.Request) -> httpx.Response:
        if request.method == "POST" and str(request.url) == RPC_URL:
            body = json.loads(request.content)
            method, params = body["method"], body.get("params", [])
            self.requests.append((method, params))
            try:
                result = getattr(self, "rpc_" + method)(*params)
            except Revert as exc:
                error = {"code": 3, "message": f"execution reverted: {exc}"}
                return httpx.Response(
                    200, json={"jsonrpc": "2.0", "id": body["id"], "error": error}
                )
            return httpx.Response(200, json={"jsonrpc": "2.0", "id": body["id"], "result": result})
        url = str(request.url)
        self.http_gets.append(url)
        if url in self.documents:
            return httpx.Response(200, content=self.documents[url])
        return httpx.Response(404)

    @staticmethod
    def _block(tag: str) -> int | None:
        return int(tag, 16) if tag.startswith("0x") else None

    def rpc_eth_blockNumber(self) -> str:
        return hex(self.head)

    def rpc_eth_getBlockByNumber(self, tag: str, full: bool = False) -> dict | None:
        number = self.head if tag in ("latest", "safe", "finalized", "pending") else int(tag, 16)
        if number > self.head:
            return None
        return {"number": hex(number), "timestamp": hex(self.timestamp(number))}

    def rpc_eth_getTransactionReceipt(self, tx_hash: str) -> dict | None:
        receipt = self.receipts.get(tx_hash)
        return _hexify(receipt) if receipt is not None else None

    def rpc_eth_getTransactionByHash(self, tx_hash: str) -> dict | None:
        tx = self.transactions.get(tx_hash)
        return _hexify(tx) if tx is not None else None

    def _balance(self, address: str, token: str | None, block: int | None) -> int:
        key = (address.lower(), token.lower() if token else None)
        return self.balances.get((*key, block), self.balances.get((*key, None), 0))

    def rpc_eth_getBalance(self, address: str, tag: str) -> str:
        return hex(self._balance(address, None, self._block(tag)))

    def rpc_eth_getLogs(self, query: dict[str, Any]) -> list[dict[str, Any]]:
        start, end = int(query["fromBlock"], 16), int(query["toBlock"], 16)
        out = []
        for log in self.logs:
            if "address" in query and log["address"].lower() != query["address"].lower():
                continue
            if not start <= int(log["blockNumber"], 16) <= end:
                continue
            topics = query.get("topics") or []
            if any(t is not None and t != log["topics"][i] for i, t in enumerate(topics)):
                continue
            out.append(log)
        return out

    def rpc_eth_call(self, call: dict[str, str], tag: str) -> str:
        import eth_abi

        to, data = call["to"].lower(), call["data"]
        selector, args_hex = data[:10], data[10:]
        route = self._routes.get((to, selector))
        if route is None and selector == self._erc20.selector("balanceOf"):
            route = (self._erc20, "balanceOf")
        if route is None:
            return "0x"
        contract, fn = route
        args = eth_abi.decode(contract.input_types(fn), bytes.fromhex(args_hex))
        if contract is self._erc20:
            values: tuple[Any, ...] = (self._balance(args[0], to, self._block(tag)),)
        else:
            values = getattr(self, "fn_" + fn)(*args)
        return "0x" + eth_abi.encode(contract.output_types(fn), list(values)).hex()

    # ------------------------------------------------------------------ contract functions

    def fn_tokenURI(self, agent_id: int) -> tuple:
        if agent_id not in self.uris:
            raise Revert("ERC721NonexistentToken")
        return (self.uris[agent_id],)

    def fn_ownerOf(self, agent_id: int) -> tuple:
        if agent_id not in self.owners:
            raise Revert("ERC721NonexistentToken")
        return (self.owners[agent_id],)

    def fn_getMetadata(self, agent_id: int, key: str) -> tuple:
        return (self.metadata.get((agent_id, key), b""),)

    def fn_getJob(self, job_id: int) -> tuple:
        empty = (0, ZERO, ZERO, ZERO, "", 0, 0, 0, ZERO, 0, bytes(32))
        return (self.jobs.get(job_id, empty),)

    def fn_disputeWindow(self) -> tuple:
        return (self.dispute_window,)

    def fn_disputed(self, job_id: int) -> tuple:
        return (job_id in self.disputed,)

    def fn_isVoter(self, voter: str) -> tuple:
        return (voter.lower() in self.voters,)

    def fn_hasVoted(self, job_id: int, voter: str) -> tuple:
        return (voter.lower() in self.votes.get(job_id, set()),)

    def fn_jobPolicy(self, job_id: int) -> tuple:
        return (self.job_policies.get(job_id, ZERO),)


class JsonRpcWorld:
    name = "jsonrpc"
    capabilities = {"registry.read", "commerce.read", "policy.read", "reader"}

    def __init__(self) -> None:
        from bnbagent_eval.integrations import jsonrpc

        from .port_worlds import VOTER  # lazy: port_worlds imports this module

        self.node = FakeNode()
        self.node.voters.add(VOTER.lower())
        self.ports = jsonrpc.ports(
            RPC_URL,
            chain_id=CHAIN_ID,
            addresses=ADDRESSES,
            client=self.node.client(),
            resolver=public_resolver,
        )

    def add_agent(self, agent_id, registration, owner):
        self.node.uris[agent_id] = encode_data_uri(registration)
        self.node.owners[agent_id] = owner

    def add_job(self, job):
        self.node.set_job(job)

    def set_receipt(self, tx_hash, receipt):
        self.node.receipts[tx_hash] = dict(receipt)

    def set_balance(self, address, amount, *, token=None, block=None):
        key = (address.lower(), token.lower() if token else None, block)
        self.node.balances[key] = amount


WORLDS = {"jsonrpc": JsonRpcWorld} if available("eth_abi") else {}
