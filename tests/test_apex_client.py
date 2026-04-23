"""Tests for the ``APEXClient`` facade (APEX v1).

Covers:
- Construction via ``(wallet_provider, network)``; ``NetworkConfig`` accepted directly.
- Wallet-provider requirement (raw private keys never reach the facade).
- Lazy payment-token caching.
- Fund approval floor strategy.
- create_job defaults Router as evaluator + hook.
"""

from unittest.mock import MagicMock, patch

import pytest

from bnbagent.apex import APEXClient
from bnbagent.apex.client import DEFAULT_APPROVE_FLOOR_UNITS
from bnbagent.config import NetworkConfig
from tests.conftest import FAKE_ADDRESS

FAKE_COMMERCE = "0x" + "aa" * 20
FAKE_ROUTER = "0x" + "bb" * 20
FAKE_POLICY = "0x" + "cc" * 20
FAKE_TOKEN = "0x" + "dd" * 20


def _fake_network() -> NetworkConfig:
    return NetworkConfig(
        name="test-net",
        rpc_url="https://fake-rpc.example.com",
        chain_id=12345,
        commerce_contract=FAKE_COMMERCE,
        router_contract=FAKE_ROUTER,
        policy_contract=FAKE_POLICY,
    )


def _mock_wallet() -> MagicMock:
    wallet = MagicMock()
    wallet.address = FAKE_ADDRESS
    return wallet


@pytest.fixture
def facade(mock_web3):
    """``APEXClient`` wired against mock sub-clients (no real web3 traffic)."""
    with patch("bnbagent.apex.client.create_web3", return_value=mock_web3), \
         patch("bnbagent.apex.client.CommerceClient") as mcc, \
         patch("bnbagent.apex.client.RouterClient") as mrc, \
         patch("bnbagent.apex.client.PolicyClient") as mpc:
        commerce = MagicMock()
        commerce.address = FAKE_COMMERCE
        router = MagicMock()
        router.address = FAKE_ROUTER
        policy = MagicMock()
        policy.address = FAKE_POLICY
        mcc.return_value = commerce
        mrc.return_value = router
        mpc.return_value = policy

        client = APEXClient(_mock_wallet(), network=_fake_network())
        yield client


class TestInit:
    def test_requires_wallet_provider(self):
        with pytest.raises(ValueError, match="wallet_provider is required"):
            APEXClient(None, network=_fake_network())  # type: ignore[arg-type]

    def test_rejects_network_missing_addresses(self, mock_web3):
        incomplete = NetworkConfig(
            name="broken",
            rpc_url="https://x",
            chain_id=1,
            commerce_contract="",
            router_contract=FAKE_ROUTER,
            policy_contract=FAKE_POLICY,
        )
        with patch("bnbagent.apex.client.create_web3", return_value=mock_web3):
            with pytest.raises(ValueError, match="commerce_contract"):
                APEXClient(_mock_wallet(), network=incomplete)

    def test_address_comes_from_wallet(self, facade):
        assert facade.address == FAKE_ADDRESS

    def test_accepts_network_string(self, mock_web3):
        """String preset is resolved via ``resolve_network`` under the hood."""
        fake_net = _fake_network()
        with patch("bnbagent.apex.client.create_web3", return_value=mock_web3), \
             patch(
                 "bnbagent.apex.client.resolve_network", return_value=fake_net
             ) as resolve, \
             patch("bnbagent.apex.client.CommerceClient") as mcc, \
             patch("bnbagent.apex.client.RouterClient") as mrc, \
             patch("bnbagent.apex.client.PolicyClient") as mpc:
            mcc.return_value.address = FAKE_COMMERCE
            mrc.return_value.address = FAKE_ROUTER
            mpc.return_value.address = FAKE_POLICY
            APEXClient(_mock_wallet(), network="bsc-testnet")
            resolve.assert_called_once_with("bsc-testnet")


class TestTokenCache:
    def test_payment_token_caches(self, facade):
        facade.commerce.payment_token.return_value = FAKE_TOKEN
        assert facade.payment_token == FAKE_TOKEN
        assert facade.payment_token == FAKE_TOKEN
        facade.commerce.payment_token.assert_called_once()


class TestCreateJob:
    def test_defaults_to_router_as_evaluator_and_hook(self, facade):
        facade.commerce.create_job.return_value = {"jobId": 1}
        facade.create_job(expired_at=123, description="d")
        facade.commerce.create_job.assert_called_once()
        _, kwargs = facade.commerce.create_job.call_args
        assert kwargs["evaluator"] == FAKE_ROUTER
        assert kwargs["hook"] == FAKE_ROUTER

    def test_allows_overriding_hook(self, facade):
        facade.commerce.create_job.return_value = {"jobId": 1}
        custom_hook = "0x" + "11" * 20
        facade.create_job(expired_at=123, description="d", hook=custom_hook)
        _, kwargs = facade.commerce.create_job.call_args
        assert kwargs["evaluator"] == FAKE_ROUTER
        assert kwargs["hook"] == custom_hook


class TestRegisterJob:
    def test_binds_configured_policy_by_default(self, facade):
        facade.register_job(1)
        facade.router.register_job.assert_called_once_with(1, FAKE_POLICY)

    def test_policy_override(self, facade):
        other_policy = "0x" + "ee" * 20
        facade.register_job(1, other_policy)
        facade.router.register_job.assert_called_once_with(1, other_policy)


class TestFund:
    """Approval-floor strategy in ``APEXClient.fund``."""

    def _prime(self, facade, current_allowance=0, decimals=18):
        facade.commerce.payment_token.return_value = FAKE_TOKEN
        facade._payment_token_address = FAKE_TOKEN

        erc20 = MagicMock()
        erc20.allowance.return_value = current_allowance
        erc20.decimals.return_value = decimals
        erc20.symbol.return_value = "USDT"
        erc20.approve.return_value = {"status": 1}
        facade._erc20 = erc20
        facade.commerce.fund.return_value = {"status": 1}
        return erc20

    def test_skips_approve_when_allowance_sufficient(self, facade):
        erc20 = self._prime(facade, current_allowance=10_000)
        facade.fund(job_id=1, amount=5_000)
        erc20.approve.assert_not_called()
        facade.commerce.fund.assert_called_once_with(1, 5_000)

    def test_approves_default_floor_when_amount_below_floor(self, facade):
        erc20 = self._prime(facade, current_allowance=0, decimals=6)
        facade.fund(job_id=1, amount=1 * 10**6)
        erc20.approve.assert_called_once_with(
            FAKE_COMMERCE, DEFAULT_APPROVE_FLOOR_UNITS * 10**6
        )

    def test_approves_exact_amount_when_above_default_floor(self, facade):
        erc20 = self._prime(facade, current_allowance=0, decimals=6)
        big = 500 * 10**6
        facade.fund(job_id=1, amount=big)
        erc20.approve.assert_called_once_with(FAKE_COMMERCE, big)

    def test_approve_floor_zero_means_exact(self, facade):
        erc20 = self._prime(facade, current_allowance=0, decimals=6)
        facade.fund(job_id=1, amount=5, approve_floor=0)
        erc20.approve.assert_called_once_with(FAKE_COMMERCE, 5)

    def test_approve_floor_custom(self, facade):
        erc20 = self._prime(facade, current_allowance=0, decimals=6)
        facade.fund(job_id=1, amount=5, approve_floor=1_000)
        erc20.approve.assert_called_once_with(FAKE_COMMERCE, 1_000)

    def test_approve_floor_negative_rejected(self, facade):
        self._prime(facade, current_allowance=0)
        with pytest.raises(ValueError, match="approve_floor must be >= 0"):
            facade.fund(job_id=1, amount=5, approve_floor=-1)


class TestWriteDelegation:
    def test_settle_delegates_to_router(self, facade):
        facade.settle(7, b"\x01")
        facade.router.settle.assert_called_once_with(7, b"\x01")

    def test_dispute_delegates_to_policy(self, facade):
        facade.dispute(7)
        facade.policy.dispute.assert_called_once_with(7)

    def test_vote_reject_delegates_to_policy(self, facade):
        facade.vote_reject(7)
        facade.policy.vote_reject.assert_called_once_with(7)

    def test_claim_refund_delegates_to_commerce(self, facade):
        facade.claim_refund(7)
        facade.commerce.claim_refund.assert_called_once_with(7)

    def test_cancel_open_delegates_to_commerce_reject(self, facade):
        facade.cancel_open(7)
        facade.commerce.reject.assert_called_once()

    def test_submit_encodes_deliverable_url_as_opt_params(self, facade):
        facade.submit(7, b"\x00" * 32, deliverable_url="https://example.com/job.json")
        facade.commerce.submit.assert_called_once_with(
            7, b"\x00" * 32, b"https://example.com/job.json"
        )

    def test_submit_empty_opt_params_when_no_url(self, facade):
        facade.submit(7, b"\x00" * 32)
        facade.commerce.submit.assert_called_once_with(7, b"\x00" * 32, b"")


class TestReads:
    def test_get_job_status(self, facade):
        from bnbagent.apex.types import Job, JobStatus

        facade.commerce.get_job.return_value = Job(
            id=1,
            client="0x" + "01" * 20,
            provider="0x" + "02" * 20,
            evaluator=FAKE_ROUTER,
            description="d",
            budget=100,
            expired_at=0,
            status=JobStatus.FUNDED,
            hook=FAKE_ROUTER,
        )
        assert facade.get_job_status(1) == JobStatus.FUNDED

    def test_get_verdict_delegates_to_policy(self, facade):
        from bnbagent.apex.types import Verdict

        facade.policy.check.return_value = (Verdict.APPROVE, b"\x00" * 32)
        verdict, _ = facade.get_verdict(1)
        assert verdict == Verdict.APPROVE
