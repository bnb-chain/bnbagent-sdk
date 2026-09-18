"""Tests for the ``ERC8183Client`` facade (ERC-8183).

Covers:
- Construction via ``(wallet_provider, network)``; ``NetworkConfig`` accepted directly.
- Wallet-provider requirement (raw private keys never reach the facade).
- Lazy payment-token caching.
- Job-token-authoritative funding and exact-by-default approvals.
- create_job defaults Router as evaluator + hook.
"""

from unittest.mock import MagicMock, patch

import pytest
from web3 import Web3

from bnbagent.config import NetworkConfig
from bnbagent.erc8183 import ERC8183Client
from bnbagent.erc8183.client import DEFAULT_APPROVE_FLOOR_UNITS
from bnbagent.networks import AssetId, get_asset
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
    """``ERC8183Client`` wired against mock sub-clients (no real web3 traffic)."""
    with (
        patch("bnbagent.erc8183.client.create_web3", return_value=mock_web3),
        patch("bnbagent.erc8183.client.CommerceClient") as mcc,
        patch("bnbagent.erc8183.client.RouterClient") as mrc,
        patch("bnbagent.erc8183.client.PolicyClient") as mpc,
    ):
        commerce = MagicMock()
        commerce.address = FAKE_COMMERCE
        router = MagicMock()
        router.address = FAKE_ROUTER
        policy = MagicMock()
        policy.address = FAKE_POLICY
        mcc.return_value = commerce
        mrc.return_value = router
        mpc.return_value = policy

        client = ERC8183Client(_mock_wallet(), network=_fake_network())
        yield client


class TestInit:
    def test_allows_read_only_without_wallet(self, mock_web3):
        """wallet_provider=None builds a read-only client (writes raise later
        via _send_tx); address is None."""
        with (
            patch("bnbagent.erc8183.client.create_web3", return_value=mock_web3),
            patch("bnbagent.erc8183.client.CommerceClient"),
            patch("bnbagent.erc8183.client.RouterClient"),
            patch("bnbagent.erc8183.client.PolicyClient"),
        ):
            client = ERC8183Client(None, network=_fake_network())
            assert client.address is None
            assert client._wallet_provider is None

    def test_rejects_network_missing_addresses(self, mock_web3):
        incomplete = NetworkConfig(
            name="broken",
            rpc_url="https://x",
            chain_id=1,
            commerce_contract="",
            router_contract=FAKE_ROUTER,
            policy_contract=FAKE_POLICY,
        )
        with patch("bnbagent.erc8183.client.create_web3", return_value=mock_web3):
            with pytest.raises(ValueError, match="commerce_contract"):
                ERC8183Client(_mock_wallet(), network=incomplete)

    def test_address_comes_from_wallet(self, facade):
        assert facade.address == FAKE_ADDRESS

    def test_chain_id_mismatch_raises(self, mock_web3):
        """RPC reporting a different chain_id must hard-fail at init (audit L06)."""
        mock_web3.eth.chain_id = 99999  # not 12345 from _fake_network()
        with patch("bnbagent.erc8183.client.create_web3", return_value=mock_web3):
            with pytest.raises(ValueError, match="chain_id mismatch"):
                ERC8183Client(_mock_wallet(), network=_fake_network())

    def test_accepts_network_string(self, mock_web3):
        """String preset is resolved via ``resolve_network`` under the hood."""
        fake_net = _fake_network()
        with (
            patch("bnbagent.erc8183.client.create_web3", return_value=mock_web3),
            patch("bnbagent.erc8183.client.resolve_network", return_value=fake_net) as resolve,
            patch("bnbagent.erc8183.client.CommerceClient") as mcc,
            patch("bnbagent.erc8183.client.RouterClient") as mrc,
            patch("bnbagent.erc8183.client.PolicyClient") as mpc,
        ):
            mcc.return_value.address = FAKE_COMMERCE
            mrc.return_value.address = FAKE_ROUTER
            mpc.return_value.address = FAKE_POLICY
            ERC8183Client(_mock_wallet(), network="bsc-testnet")
            resolve.assert_called_once_with("bsc-testnet")


class TestTokenCache:
    def test_payment_token_caches(self, facade):
        facade.commerce.payment_token.return_value = FAKE_TOKEN
        expected = Web3.to_checksum_address(FAKE_TOKEN)
        assert facade.payment_token == expected
        assert facade.payment_token == expected
        facade.commerce.payment_token.assert_called_once()


class TestVerifyNegotiationQuote:
    def test_binds_provider_currency_chain_and_commerce(self, facade):
        facade.commerce.payment_token.return_value = FAKE_TOKEN
        quote = {
            "response": {
                "accepted": True,
                "terms": {"price": "1000", "currency": FAKE_TOKEN},
            },
            "chain_id": 12345,
            "negotiation_hash": "0x" + "11" * 32,
            "provider_sig": "0x" + "22" * 65,
        }
        expected = MagicMock(valid=True)
        with patch(
            "bnbagent.erc8183.client.verify_quote_signature", return_value=expected
        ) as verify:
            assert (
                facade.verify_negotiation_quote(quote, expected_provider=FAKE_ADDRESS) is expected
            )
        verify.assert_called_once_with(
            envelope=quote,
            provider=FAKE_ADDRESS,
            w3=facade.w3,
            expected_verifying_contract=FAKE_COMMERCE,
            block_number=None,
        )

    def test_rejects_currency_mismatch_before_signature_rpc(self, facade):
        facade.commerce.payment_token.return_value = FAKE_TOKEN
        quote = {
            "response": {
                "accepted": True,
                "terms": {
                    "price": "1000",
                    "currency": "0x" + "11" * 20,
                },
            },
            "chain_id": 12345,
        }
        with patch("bnbagent.erc8183.client.verify_quote_signature") as verify:
            verdict = facade.verify_negotiation_quote(quote, expected_provider=FAKE_ADDRESS)
        assert verdict.valid is False
        assert verdict.reason == "quote currency does not match payment token"
        verify.assert_not_called()

    def test_explicit_expected_currency_checks_request_and_response(self, facade):
        selected = "0x" + "12" * 20
        quote = {
            "request": {"terms": {"currency": selected}},
            "response": {
                "accepted": True,
                "terms": {"price": "0", "currency": selected},
            },
            "chain_id": 12345,
        }
        expected = MagicMock(valid=True)
        with patch(
            "bnbagent.erc8183.client.verify_quote_signature", return_value=expected
        ) as verify:
            verdict = facade.verify_negotiation_quote(
                quote,
                expected_provider=FAKE_ADDRESS,
                expected_currency=selected.lower(),
            )
        assert verdict is expected
        verify.assert_called_once()
        facade.commerce.payment_token.assert_not_called()

    def test_rejects_request_response_currency_mismatch_before_signature(self, facade):
        selected = "0x" + "12" * 20
        quote = {
            "request": {"terms": {"currency": "0x" + "13" * 20}},
            "response": {
                "accepted": True,
                "terms": {"price": "1", "currency": selected},
            },
            "chain_id": 12345,
        }
        with patch("bnbagent.erc8183.client.verify_quote_signature") as verify:
            verdict = facade.verify_negotiation_quote(
                quote,
                expected_provider=FAKE_ADDRESS,
                expected_currency=selected,
            )
        assert verdict.valid is False
        assert verdict.reason == "quote request currency mismatch"
        verify.assert_not_called()

    def test_explicit_expected_currency_requires_request_binding(self, facade):
        selected = "0x" + "12" * 20
        quote = {
            "response": {
                "accepted": True,
                "terms": {"price": "1", "currency": selected},
            },
            "chain_id": 12345,
        }

        verdict = facade.verify_negotiation_quote(
            quote,
            expected_provider=FAKE_ADDRESS,
            expected_currency=selected,
        )

        assert verdict.valid is False
        assert verdict.reason == "quote request currency is missing"

    def test_expected_asset_id_is_resolved_on_current_chain(self, facade):
        selected = get_asset(97, AssetId.TEST_USDC).address
        facade.network.chain_id = 97
        quote = {
            "request": {"terms": {"currency": selected.lower()}},
            "response": {
                "accepted": True,
                "terms": {"price": "1", "currency": selected},
            },
            "chain_id": 97,
        }
        expected = MagicMock(valid=True)
        with patch("bnbagent.erc8183.client.verify_quote_signature", return_value=expected):
            verdict = facade.verify_negotiation_quote(
                quote,
                expected_provider=FAKE_ADDRESS,
                expected_currency=AssetId.TEST_USDC,
            )

        assert verdict is expected

    @pytest.mark.parametrize("price", [True, -1, "00", "01", "1.5", "1e3", None])
    def test_rejects_invalid_price(self, facade, price):
        facade.commerce.payment_token.return_value = FAKE_TOKEN
        quote = {
            "response": {
                "accepted": True,
                "terms": {"price": price, "currency": FAKE_TOKEN},
            },
            "chain_id": 12345,
        }
        verdict = facade.verify_negotiation_quote(quote, expected_provider=FAKE_ADDRESS)
        assert verdict.valid is False
        assert verdict.reason == "quote price must be a non-negative integer"

    def test_rejects_noncanonical_top_level_accepted(self, facade):
        quote = {
            "accepted": True,
            "response": {"terms": {"price": "1000", "currency": FAKE_TOKEN}},
        }
        verdict = facade.verify_negotiation_quote(quote, expected_provider=FAKE_ADDRESS)
        assert verdict.valid is False
        assert verdict.reason == "quote is not accepted"

    @pytest.mark.parametrize("chain_id", [None, True, 56])
    def test_requires_exact_chain_binding(self, facade, chain_id):
        facade.commerce.payment_token.return_value = FAKE_TOKEN
        quote = {
            "response": {
                "accepted": True,
                "terms": {"price": "1000", "currency": FAKE_TOKEN},
            },
            "chain_id": chain_id,
        }
        verdict = facade.verify_negotiation_quote(quote, expected_provider=FAKE_ADDRESS)
        assert verdict.valid is False
        assert verdict.reason == "quote chain_id mismatch"


class TestCreateJob:
    def test_defaults_to_router_as_evaluator_and_hook(self, facade):
        facade.commerce.create_job.return_value = {"jobId": 1}
        facade.create_job(expired_at=123, description="d", skip_expiry_check=True)
        facade.commerce.create_job.assert_called_once()
        _, kwargs = facade.commerce.create_job.call_args
        assert kwargs["evaluator"] == FAKE_ROUTER
        assert kwargs["hook"] == FAKE_ROUTER

    def test_allows_overriding_hook(self, facade):
        facade.commerce.create_job.return_value = {"jobId": 1}
        custom_hook = "0x" + "11" * 20
        facade.create_job(
            expired_at=123, description="d", hook=custom_hook, skip_expiry_check=True
        )
        _, kwargs = facade.commerce.create_job.call_args
        assert kwargs["evaluator"] == FAKE_ROUTER
        assert kwargs["hook"] == custom_hook

    def test_rejects_expired_at_within_dispute_window(self, facade):
        """expired_at - now <= dispute_window MUST raise ValueError.

        Mainnet OptimisticPolicy.disputeWindow = 7 days, so a 24h job is DOA:
        submit() always reverts SubmissionTooLate(). Catch this client-side
        before the user funds an unsubmittable job.
        Regression for https://github.com/bnb-chain/bnbagent-sdk/issues/41.
        """
        import time

        facade.policy.dispute_window.return_value = 7 * 86400
        too_close = int(time.time()) + 86400  # 24h, well inside 7d window
        with pytest.raises(ValueError, match="dispute_window"):
            facade.create_job(expired_at=too_close, description="d")
        facade.commerce.create_job.assert_not_called()

    def test_accepts_expired_at_beyond_dispute_window(self, facade):
        import time

        facade.policy.dispute_window.return_value = 7 * 86400
        far_enough = int(time.time()) + 8 * 86400 + 60  # 8d + 1min
        facade.commerce.create_job.return_value = {"jobId": 99}
        facade.create_job(expired_at=far_enough, description="d")
        facade.commerce.create_job.assert_called_once()

    def test_skip_expiry_check_bypasses_validation(self, facade):
        import time

        facade.policy.dispute_window.return_value = 7 * 86400
        facade.commerce.create_job.return_value = {"jobId": 99}
        facade.create_job(
            expired_at=int(time.time()) + 60,
            description="d",
            skip_expiry_check=True,
        )
        facade.commerce.create_job.assert_called_once()


class TestRegisterJob:
    def test_binds_configured_policy_by_default(self, facade):
        facade.register_job(1)
        facade.router.register_job.assert_called_once_with(1, FAKE_POLICY)

    def test_policy_override(self, facade):
        other_policy = "0x" + "ee" * 20
        facade.register_job(1, other_policy)
        facade.router.register_job.assert_called_once_with(1, other_policy)


class TestFund:
    """Job-token-authoritative funding and bounded approval behavior."""

    def _prime(self, facade, current_allowance=0, token=FAKE_TOKEN):
        checksum_token = Web3.to_checksum_address(token)
        facade.commerce.payment_token.return_value = FAKE_TOKEN
        facade.commerce.job_payment_token.return_value = token
        facade._payment_token_address = Web3.to_checksum_address(FAKE_TOKEN)

        erc20 = MagicMock()
        erc20.allowance.return_value = current_allowance
        erc20.approve.return_value = {"status": 1}
        facade._erc20_clients[checksum_token] = erc20
        facade.commerce.fund.return_value = {"status": 1}
        return erc20

    def test_skips_approve_when_allowance_sufficient(self, facade):
        erc20 = self._prime(facade, current_allowance=10_000)
        facade.fund(job_id=1, amount=5_000)
        facade.commerce.job_payment_token.assert_called_once_with(1)
        erc20.approve.assert_not_called()
        facade.commerce.fund.assert_called_once_with(1, 5_000)

    def test_default_approval_is_exact_amount(self, facade):
        erc20 = self._prime(facade, current_allowance=0)
        facade.fund(job_id=1, amount=5)
        erc20.approve.assert_called_once_with(FAKE_COMMERCE, 5)

    def test_explicit_legacy_approve_floor_is_opt_in(self, facade):
        assert DEFAULT_APPROVE_FLOOR_UNITS == 100
        erc20 = self._prime(facade, current_allowance=0)
        facade.fund(job_id=1, amount=5, approve_floor=1_000)
        erc20.approve.assert_called_once_with(FAKE_COMMERCE, 1_000)

    def test_negative_amount_rejected_before_any_chain_or_funding_action(self, facade):
        facade._wallet_provider = None
        facade.address = None
        with pytest.raises(ValueError, match="amount must be >= 0"):
            facade.fund(job_id=1, amount=-1)
        facade.commerce.job_payment_token.assert_not_called()
        facade.commerce.fund.assert_not_called()

    @pytest.mark.parametrize("amount", [0, 5])
    @pytest.mark.parametrize("missing", ["wallet", "address"])
    def test_read_only_fund_fails_before_any_rpc_or_funding_action(self, facade, amount, missing):
        if missing == "wallet":
            facade._wallet_provider = None
        else:
            facade.address = None

        with pytest.raises(
            RuntimeError,
            match=r"wallet_provider is required for write operations \(client is read-only\)",
        ):
            facade.fund(job_id=1, amount=amount)

        facade.commerce.job_payment_token.assert_not_called()
        facade.commerce.payment_token.assert_not_called()
        facade.commerce.fund.assert_not_called()
        assert facade._erc20_clients == {}

    def test_expected_token_mismatch_fails_typed_before_erc20_or_fund(self, facade):
        erc20 = self._prime(facade, current_allowance=0)
        expected = "0x" + "11" * 20

        with pytest.raises(Exception) as exc_info:
            facade.fund(job_id=7, amount=5, expected_token=expected)

        error = exc_info.value
        assert type(error).__name__ == "JobPaymentTokenMismatchError"
        assert error.job_id == 7
        assert error.expected_token == Web3.to_checksum_address(expected)
        assert error.actual_token == Web3.to_checksum_address(FAKE_TOKEN)
        erc20.balance_of.assert_not_called()
        erc20.allowance.assert_not_called()
        erc20.approve.assert_not_called()
        facade.commerce.fund.assert_not_called()

    def test_expected_asset_id_is_resolved_for_current_chain(self, facade):
        import dataclasses

        facade.network = dataclasses.replace(facade.network, chain_id=97)
        token = get_asset(97, AssetId.TEST_USDC).address
        erc20 = self._prime(facade, current_allowance=10, token=token)

        facade.fund(job_id=3, amount=5, expected_token=AssetId.TEST_USDC)

        erc20.allowance.assert_called_once_with(facade.address, FAKE_COMMERCE)
        facade.commerce.fund.assert_called_once_with(3, 5)

    def test_job_bound_non_default_token_is_authoritative(self, facade):
        token = "0x" + "12" * 20
        erc20 = self._prime(facade, current_allowance=0, token=token)

        facade.fund(job_id=2, amount=99)

        erc20.allowance.assert_called_once_with(facade.address, FAKE_COMMERCE)
        erc20.approve.assert_called_once_with(FAKE_COMMERCE, 99)
        facade.commerce.payment_token.assert_not_called()

    def test_zero_amount_reads_job_token_but_skips_allowance_and_approve(self, facade):
        erc20 = self._prime(facade, current_allowance=0)

        facade.fund(job_id=1, amount=0)

        facade.commerce.job_payment_token.assert_called_once_with(1)
        erc20.allowance.assert_not_called()
        erc20.approve.assert_not_called()
        facade.commerce.fund.assert_called_once_with(1, 0)

    def test_bundled_approval_wallet_skips_allowance_management(self, facade):
        """fund_bundles_approval=True (literally) → straight to commerce.fund;
        the SDK never reads the allowance or sends an approve (the wallet's
        own fund operation bundles approve+deposit, e.g. twak)."""
        erc20 = self._prime(facade, current_allowance=0)
        facade._wallet_provider.fund_bundles_approval = True
        facade.fund(job_id=1, amount=5_000, expected_token=FAKE_TOKEN)
        facade.commerce.job_payment_token.assert_called_once_with(1)
        erc20.allowance.assert_not_called()
        erc20.approve.assert_not_called()
        facade.commerce.fund.assert_called_once_with(1, 5_000)

    def test_magicmock_attribute_does_not_trigger_skip(self, facade):
        """The guard is ``is True``: a plain MagicMock wallet auto-creates a
        truthy MagicMock for ``fund_bundles_approval``, which must NOT skip
        the SDK-side allowance path."""
        erc20 = self._prime(facade, current_allowance=10_000)
        assert facade._wallet_provider.fund_bundles_approval is not True
        facade.fund(job_id=1, amount=5_000)
        erc20.allowance.assert_called_once()
        facade.commerce.fund.assert_called_once_with(1, 5_000)


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

    def test_submit_encodes_opt_params_as_json_bytes(self, facade):
        facade.submit(7, b"\x00" * 32, {"deliverable_url": "https://example.com/job.json"})
        facade.commerce.submit.assert_called_once_with(
            7, b"\x00" * 32, b'{"deliverable_url":"https://example.com/job.json"}'
        )

    def test_submit_raises_without_deliverable_url(self, facade):
        with pytest.raises(ValueError, match="deliverable_url"):
            facade.submit(7, b"\x00" * 32, {})

    def test_submit_raises_on_empty_deliverable_url(self, facade):
        with pytest.raises(ValueError, match="non-empty URL"):
            facade.submit(7, b"\x00" * 32, {"deliverable_url": ""})


class TestReads:
    def test_get_job_status(self, facade):
        from bnbagent.erc8183.types import Job, JobStatus

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

    def test_get_job_funded_block_queries_signed_window(self, facade):
        facade.w3.eth.block_number = 10
        facade.w3.eth.get_block.side_effect = lambda number: {"timestamp": int(number) * 10}
        facade.commerce.get_job_funded_events.return_value = [{"blockNumber": 6}]

        block = facade.get_job_funded_block(
            7,
            negotiated_at=25,
            quote_expires_at=75,
        )

        assert block == 6
        facade.commerce.get_job_funded_events.assert_called_once_with(
            3,
            8,
            job_id=7,
        )

    def test_get_job_funded_block_fails_closed_without_event(self, facade):
        facade.w3.eth.block_number = 10
        facade.w3.eth.get_block.side_effect = lambda number: {"timestamp": int(number) * 10}
        facade.commerce.get_job_funded_events.return_value = []

        assert (
            facade.get_job_funded_block(
                7,
                negotiated_at=25,
                quote_expires_at=75,
            )
            is None
        )

    def test_get_verdict_delegates_to_policy(self, facade):
        from bnbagent.erc8183.types import Verdict

        facade.policy.check.return_value = (Verdict.APPROVE, b"\x00" * 32)
        verdict, _ = facade.get_verdict(1)
        assert verdict == Verdict.APPROVE


class TestPolicyRangeLimit:
    """Rate/range-limited log queries raise typed retryable errors (BUG-06)."""

    def _policy(self):
        from bnbagent.erc8183.policy import PolicyClient

        w3 = MagicMock()
        w3.eth.block_number = 5000
        return PolicyClient(w3, "0x" + "f8" * 20, abi=[])

    def test_rate_limit_raises_typed_error(self):
        from bnbagent.exceptions import RpcRangeLimitError

        policy = self._policy()
        policy.contract.events.JobInitialised.return_value.get_logs.side_effect = Exception(
            "{'code': -32005, 'message': 'limit exceeded'}"
        )
        with pytest.raises(RpcRangeLimitError):
            policy.get_deliverable_url(1)

    def test_other_query_error_still_returns_none(self):
        policy = self._policy()
        policy.contract.events.JobInitialised.return_value.get_logs.side_effect = Exception(
            "some other rpc problem"
        )
        assert policy.get_deliverable_url(1) is None

    def test_genuine_empty_returns_none(self):
        policy = self._policy()
        policy.contract.events.JobInitialised.return_value.get_logs.return_value = []
        assert policy.get_deliverable_url(1) is None
