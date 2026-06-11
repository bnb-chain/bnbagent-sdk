"""Intent construction for ERC-8183 write methods (Phase 1a seam).

Every write on ``CommerceClient`` / ``RouterClient`` / ``PolicyClient`` now
builds a dual-representation ``Intent`` (semantic ``name`` + ``kwargs``,
mechanical ``call``) and runs it through ``ContractClientMixin._execute_intent``.
These tests capture the Intent at a stub executor and assert:

- ``name`` matches the ``ERC8183_*`` constant,
- ``kwargs`` carries the documented high-level keys,
- ``call`` is the bound ``ContractFunction`` built from the client's contract.

Also covered: ``create_job`` jobId dual-sourcing (semantic backends return it
directly; the local path parses the ``JobCreated`` event from the receipt)
and the policy admin ops, which intentionally stay on ``_send_tx``.
"""

from unittest.mock import MagicMock

from bnbagent.erc8183.commerce import CommerceClient
from bnbagent.erc8183.policy import PolicyClient
from bnbagent.erc8183.router import RouterClient
from bnbagent.erc8183.types import ZERO_ADDRESS, ZERO_REASON
from bnbagent.wallets.intents import (
    ERC8183_CLAIM_REFUND,
    ERC8183_COMPLETE,
    ERC8183_CREATE_JOB,
    ERC8183_DISPUTE,
    ERC8183_FUND,
    ERC8183_MARK_EXPIRED,
    ERC8183_REGISTER_JOB,
    ERC8183_REJECT,
    ERC8183_SET_BUDGET,
    ERC8183_SET_PROVIDER,
    ERC8183_SETTLE,
    ERC8183_SUBMIT,
    ERC8183_VOTE_REJECT,
)
from tests.conftest import FAKE_ADDRESS

FAKE_CONTRACT = "0x" + "aa" * 20
PROVIDER = "0x" + "11" * 20
EVALUATOR = "0x" + "22" * 20
HOOK = "0x" + "33" * 20
POLICY = "0x" + "44" * 20


class _RecordingExecutor:
    """Stub IntentExecutor: records every intent, returns a canonical result."""

    def __init__(self, result=None):
        self.intents = []
        self.result = result or {"transactionHash": "0xabc", "receipt": None}

    def execute(self, intent):
        self.intents.append(intent)
        return dict(self.result)


def _wallet(executor):
    wallet = MagicMock()
    wallet.address = FAKE_ADDRESS
    wallet.make_executor.return_value = executor
    return wallet


def _make(client_cls, result=None):
    """Build a client wired to a recording executor (mock web3, no traffic)."""
    executor = _RecordingExecutor(result)
    client = client_cls(MagicMock(), FAKE_CONTRACT, _wallet(executor), abi=[])
    return client, executor


class TestCommerceIntents:
    def test_create_job(self):
        client, executor = _make(CommerceClient)
        client.create_job(
            provider=PROVIDER,
            evaluator=EVALUATOR,
            expired_at=123,
            description="d",
            hook=HOOK,
        )
        (intent,) = executor.intents
        assert intent.name == ERC8183_CREATE_JOB
        assert intent.kwargs == {
            "provider": PROVIDER,
            "evaluator": EVALUATOR,
            "expired_at": 123,
            "description": "d",
            "hook": HOOK,
        }
        assert intent.call is client.contract.functions.createJob.return_value

    def test_create_job_default_hook_is_zero_address(self):
        client, executor = _make(CommerceClient)
        client.create_job(
            provider=PROVIDER, evaluator=EVALUATOR, expired_at=123, description="d"
        )
        assert executor.intents[0].kwargs["hook"] == ZERO_ADDRESS

    def test_set_provider(self):
        client, executor = _make(CommerceClient)
        client.set_provider(7, PROVIDER)
        (intent,) = executor.intents
        assert intent.name == ERC8183_SET_PROVIDER
        assert intent.kwargs == {"job_id": 7, "provider": PROVIDER, "opt_params": b""}
        assert intent.call is client.contract.functions.setProvider.return_value

    def test_set_budget(self):
        client, executor = _make(CommerceClient)
        client.set_budget(7, 500)
        (intent,) = executor.intents
        assert intent.name == ERC8183_SET_BUDGET
        assert intent.kwargs == {"job_id": 7, "amount": 500, "opt_params": b""}
        assert intent.call is client.contract.functions.setBudget.return_value

    def test_fund(self):
        client, executor = _make(CommerceClient)
        client.fund(7, 500)
        (intent,) = executor.intents
        assert intent.name == ERC8183_FUND
        assert intent.kwargs == {
            "job_id": 7,
            "expected_budget": 500,
            "opt_params": b"",
        }
        assert intent.call is client.contract.functions.fund.return_value

    def test_submit(self):
        client, executor = _make(CommerceClient)
        deliverable = b"\x11" * 32
        client.submit(7, deliverable, b'{"deliverable_url":"u"}')
        (intent,) = executor.intents
        assert intent.name == ERC8183_SUBMIT
        assert intent.kwargs == {
            "job_id": 7,
            "deliverable": deliverable,
            "opt_params": b'{"deliverable_url":"u"}',
        }
        assert intent.call is client.contract.functions.submit.return_value

    def test_complete(self):
        client, executor = _make(CommerceClient)
        client.complete(7)
        (intent,) = executor.intents
        assert intent.name == ERC8183_COMPLETE
        assert intent.kwargs == {"job_id": 7, "reason": ZERO_REASON, "opt_params": b""}
        assert intent.call is client.contract.functions.complete.return_value

    def test_reject(self):
        client, executor = _make(CommerceClient)
        reason = b"\x22" * 32
        client.reject(7, reason)
        (intent,) = executor.intents
        assert intent.name == ERC8183_REJECT
        assert intent.kwargs == {"job_id": 7, "reason": reason, "opt_params": b""}
        assert intent.call is client.contract.functions.reject.return_value

    def test_claim_refund(self):
        client, executor = _make(CommerceClient)
        client.claim_refund(7)
        (intent,) = executor.intents
        assert intent.name == ERC8183_CLAIM_REFUND
        assert intent.kwargs == {"job_id": 7}
        assert intent.call is client.contract.functions.claimRefund.return_value


class TestRouterIntents:
    def test_register_job(self):
        client, executor = _make(RouterClient)
        client.register_job(7, POLICY)
        (intent,) = executor.intents
        assert intent.name == ERC8183_REGISTER_JOB
        assert intent.kwargs == {"job_id": 7, "policy": POLICY}
        assert intent.call is client.contract.functions.registerJob.return_value

    def test_settle(self):
        client, executor = _make(RouterClient)
        client.settle(7, b"\x01")
        (intent,) = executor.intents
        assert intent.name == ERC8183_SETTLE
        assert intent.kwargs == {"job_id": 7, "evidence": b"\x01"}
        assert intent.call is client.contract.functions.settle.return_value

    def test_mark_expired(self):
        client, executor = _make(RouterClient)
        client.mark_expired(7)
        (intent,) = executor.intents
        assert intent.name == ERC8183_MARK_EXPIRED
        assert intent.kwargs == {"job_id": 7}
        assert intent.call is client.contract.functions.markExpired.return_value


class TestPolicyIntents:
    def test_dispute(self):
        client, executor = _make(PolicyClient)
        client.dispute(7)
        (intent,) = executor.intents
        assert intent.name == ERC8183_DISPUTE
        assert intent.kwargs == {"job_id": 7}
        assert intent.call is client.contract.functions.dispute.return_value

    def test_vote_reject(self):
        client, executor = _make(PolicyClient)
        client.vote_reject(7)
        (intent,) = executor.intents
        assert intent.name == ERC8183_VOTE_REJECT
        assert intent.kwargs == {"job_id": 7}
        assert intent.call is client.contract.functions.voteReject.return_value


class TestCreateJobJobIdSources:
    """jobId comes from the executor result OR the JobCreated receipt event."""

    def test_executor_supplied_job_id_skips_receipt_parsing(self):
        client, _ = _make(
            CommerceClient,
            result={"transactionHash": "0xabc", "receipt": None, "jobId": 5},
        )
        result = client.create_job(
            provider=PROVIDER, evaluator=EVALUATOR, expired_at=123, description="d"
        )
        assert result["jobId"] == 5
        client.contract.events.JobCreated.assert_not_called()

    def test_receipt_event_fills_job_id_when_executor_omits_it(self):
        receipt = {"blockNumber": 100}
        client, _ = _make(
            CommerceClient, result={"transactionHash": "0xabc", "receipt": receipt}
        )
        client.contract.events.JobCreated.return_value.process_receipt.return_value = [
            {"args": {"jobId": 7}}
        ]
        result = client.create_job(
            provider=PROVIDER, evaluator=EVALUATOR, expired_at=123, description="d"
        )
        assert result["jobId"] == 7
        client.contract.events.JobCreated.return_value.process_receipt.assert_called_once_with(
            receipt
        )

    def test_no_job_id_and_no_receipt_leaves_job_id_unset(self):
        client, _ = _make(CommerceClient)  # default result: receipt=None, no jobId
        result = client.create_job(
            provider=PROVIDER, evaluator=EVALUATOR, expired_at=123, description="d"
        )
        assert result.get("jobId") is None
        client.contract.events.JobCreated.assert_not_called()


class TestPolicyAdminOpsStayOnSendTx:
    """Owner-only admin writes were deliberately NOT migrated to the seam."""

    def _patched_policy(self):
        client, _ = _make(PolicyClient)
        client._send_tx = MagicMock(return_value={"status": 1})
        client._execute_intent = MagicMock()
        return client

    def test_add_voter_uses_send_tx(self):
        client = self._patched_policy()
        client.add_voter(PROVIDER)
        client._send_tx.assert_called_once()
        client._execute_intent.assert_not_called()

    def test_remove_voter_uses_send_tx(self):
        client = self._patched_policy()
        client.remove_voter(PROVIDER)
        client._send_tx.assert_called_once()
        client._execute_intent.assert_not_called()

    def test_set_quorum_uses_send_tx(self):
        client = self._patched_policy()
        client.set_quorum(3)
        client._send_tx.assert_called_once()
        client._execute_intent.assert_not_called()
