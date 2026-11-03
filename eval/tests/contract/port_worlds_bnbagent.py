"""The "bnbagent" port world: our adapters over mocked SDK objects (no RPC).

Follows the SDK's own test patterns (``python/tests/conftest.py``, ``_inject_client``): a
``MagicMock`` ``ERC8183Client`` / ``ERC8004Agent`` / web3 backed by in-memory chain state, and the
REAL ``ERC8183JobOps`` (job verification, manifest building, ``submit``) and a real
``StorageProvider`` subclass.

Skipped without the SDK. ``port_worlds._merge`` imports this module unconditionally, so a
module-level ``pytest.importorskip`` would skip the whole contract suite (fakes included); the
world is registered only when ``bnbagent`` is importable instead.
"""

from __future__ import annotations

import dataclasses
import importlib.util
import json
import time
from typing import Any

WORLDS: dict[str, Any] = {}

if importlib.util.find_spec("bnbagent") is not None:  # pragma: no branch
    from unittest.mock import MagicMock

    from bnbagent.config import NetworkConfig
    from bnbagent.erc8183.job_ops import ERC8183JobOps
    from bnbagent.erc8183.policy import PolicyClient
    from bnbagent.erc8183.types import Job, JobStatus
    from bnbagent.storage import StorageProvider
    from web3 import Web3
    from web3.datastructures import AttributeDict
    from web3.exceptions import ContractLogicError, TransactionNotFound

    from bnbagent_eval.hashing import hash_obj, keccak_hex
    from bnbagent_eval.integrations.bnbagent import ports
    from bnbagent_eval.ports import JobView
    from bnbagent_eval.testing.fakes import encode_data_uri

    from .port_worlds import CAPABILITIES, VOTER

    CHAIN_ID = 97
    REGISTRY = Web3.to_checksum_address("0x" + "80" * 20)
    COMMERCE = Web3.to_checksum_address("0x" + "81" * 20)
    ROUTER = Web3.to_checksum_address("0x" + "82" * 20)
    POLICY = Web3.to_checksum_address("0x" + "83" * 20)
    TOKEN = "0xc70B8741B8B07A6d61E54fd4B20f22Fa648E5565"  # TEST_U in the SDK asset catalog
    PROVIDER = "0x" + "d2" * 20  # == test_ports.PROVIDER

    NETWORK = NetworkConfig(
        name="mock",
        chain_id=CHAIN_ID,
        rpc_url="http://localhost:1",
        registry_contract=REGISTRY,
        commerce_contract=COMMERCE,
        router_contract=ROUTER,
        policy_contract=POLICY,
    )

    class MemoryStorageProvider(StorageProvider):
        """A real ``StorageProvider`` subclass holding JSON in memory (``mem://`` URLs)."""

        def __init__(self) -> None:
            self.docs: dict[str, dict] = {}

        async def upload(self, data: dict, filename: str | None = None) -> str:
            url = f"mem://{hash_obj(data)[2:18]}/{filename or 'doc.json'}"
            self.docs[url] = json.loads(json.dumps(data))
            return url

        async def download(self, url: str) -> dict:
            if url not in self.docs:
                from bnbagent.exceptions import StorageError

                raise StorageError(f"not found: {url}")
            return json.loads(json.dumps(self.docs[url]))

        async def exists(self, url: str) -> bool:
            return url in self.docs

    def _tx(label: str) -> str:
        return keccak_hex(label)

    class BnbagentWorld:
        name = "bnbagent"
        capabilities = set(CAPABILITIES)

        def __init__(self) -> None:
            self.jobs: dict[int, Job] = {}
            self.urls: dict[int, str] = {}
            self.agents: dict[int, tuple[str, str]] = {}
            self.metadata: dict[tuple[int, str], str] = {}
            self.disputed: set[int] = set()
            self.receipts: dict[str, dict] = {}
            self.balances: dict[tuple[str, str | None, int | None], int] = {}
            self.storage = MemoryStorageProvider()
            self.client = self._client()
            self.identity = self._identity()
            self.wallet = MagicMock()
            self.wallet.address = PROVIDER
            self.job_ops = ERC8183JobOps(
                self.wallet, NETWORK, storage_provider=self.storage, allow_unsigned_jobs=True
            )
            self.job_ops._client = self.client  # the SDK tests' _inject_client
            self.ports = ports(
                NETWORK,
                wallet=self.wallet,
                storage=self.storage,
                client=self.client,
                identity=self.identity,
                job_ops=self.job_ops,
                payer=MagicMock(),
            )

        # ------------------------------------------------------------ SDK mocks

        def _client(self) -> MagicMock:
            client = MagicMock()
            client.network = NETWORK
            client.commerce.address = COMMERCE
            client.router.address = ROUTER
            client.router.job_policy.return_value = POLICY
            client.router.policy_whitelist.return_value = True
            client.payment_token = TOKEN
            client.job_payment_token.return_value = TOKEN
            client.get_job.side_effect = self._get_job
            client.submit.side_effect = self._submit
            client.get_deliverable_url.side_effect = lambda job_id: self.urls.get(job_id)
            client.dispute.side_effect = self._dispute
            client.vote_reject.side_effect = lambda job_id: {"transactionHash": _tx("vote")}

            policy = MagicMock(spec=PolicyClient)
            policy.address = POLICY
            policy.dispute_window.return_value = 3600
            policy.disputed.side_effect = lambda job_id: job_id in self.disputed
            policy.is_voter.side_effect = lambda a: a.lower() == VOTER.lower()
            policy.has_voted.return_value = False
            client.policy = policy

            w3 = MagicMock()
            w3.eth.chain_id = CHAIN_ID
            w3.eth.block_number = 1000
            w3.eth.get_transaction_receipt.side_effect = self._receipt
            w3.eth.get_balance.side_effect = self._balance
            client.w3 = w3
            client.commerce.w3 = w3
            return client

        def _identity(self) -> MagicMock:
            identity = MagicMock()
            identity.get_agent_info.side_effect = self._agent_info
            identity.get_metadata.side_effect = lambda a, k: self.metadata.get((a, k), "")
            identity.set_metadata.side_effect = self._set_metadata
            identity.set_agent_uri.side_effect = self._set_uri
            return identity

        def _get_job(self, job_id: int) -> Job:
            zero = "0x" + "00" * 20
            return self.jobs.get(job_id, Job(0, zero, zero, zero, "", 0, 0, JobStatus.OPEN, zero))

        def _submit(self, job_id: int, deliverable: bytes, opt_params: dict) -> dict:
            job = self.jobs[job_id]
            if job.status != JobStatus.FUNDED:
                raise ContractLogicError("execution reverted: WrongStatus")
            self.jobs[job_id] = dataclasses.replace(
                job,
                status=JobStatus.SUBMITTED,
                deliverable=deliverable,
                submitted_at=int(time.time()),
            )
            self.urls[job_id] = opt_params["deliverable_url"]
            return {"transactionHash": bytes.fromhex(_tx(f"submit{job_id}")[2:]), "receipt": {}}

        def _dispute(self, job_id: int) -> dict:
            self.disputed.add(job_id)
            return {"transactionHash": _tx(f"dispute{job_id}"), "receipt": {}}

        def _agent_info(self, agent_id: int) -> dict:
            if agent_id not in self.agents:
                try:
                    raise ContractLogicError("execution reverted: ERC721NonexistentToken")
                except ContractLogicError as exc:
                    raise RuntimeError(f"Failed to get agent info: {exc}") from exc
            uri, owner = self.agents[agent_id]
            return {"agentId": agent_id, "owner": owner, "agentURI": uri, "agentWallet": owner}

        def _set_metadata(self, agent_id: int, key: str, value: str) -> dict:
            self.metadata[(agent_id, key)] = value
            return {"success": True, "transactionHash": _tx(f"meta{key}"), "receipt": {}}

        def _set_uri(self, agent_id: int, uri: str) -> dict:
            self.agents[agent_id] = (uri, self.agents[agent_id][1])
            return {"success": True, "transactionHash": _tx(f"uri{agent_id}"), "receipt": {}}

        def _receipt(self, tx_hash: str) -> AttributeDict:
            if tx_hash not in self.receipts:
                raise TransactionNotFound(f"Transaction {tx_hash} not found")
            return AttributeDict(self.receipts[tx_hash])

        def _balance(self, address: str, block_identifier: Any = "latest") -> int:
            block = None if block_identifier == "latest" else block_identifier
            key = (address.lower(), None, block)
            return self.balances.get(key, self.balances.get((address.lower(), None, None), 0))

        # ---------------------------------------------------------- PortWorld

        def add_agent(self, agent_id: int, registration: dict, owner: str) -> None:
            self.agents[agent_id] = (encode_data_uri(registration), owner)

        def add_job(self, job: JobView) -> None:
            self.jobs[job.job_id] = Job(
                id=job.job_id,
                client=job.client,
                provider=job.provider,
                evaluator=ROUTER,
                description=job.description or "",
                budget=job.budget or 0,
                expired_at=job.expired_at or 0,
                status=JobStatus[job.status.upper()],
                hook=ROUTER,
            )

        def set_receipt(self, tx_hash: str, receipt: dict) -> None:
            self.receipts[tx_hash] = receipt

        def set_balance(self, address, amount, *, token=None, block=None) -> None:
            key = (address.lower(), token.lower() if token else None, block)
            self.balances[key] = amount

    WORLDS["bnbagent"] = BnbagentWorld
