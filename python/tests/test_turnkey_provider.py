"""``TurnkeyWalletProvider`` (Python) — provider-specific suite.

Mirrors ``typescript/tests/turnkeyProvider.test.ts``: construction
validation, ``from_env``, policy-before-billing ordering, the EIP-712
payload contract (domain included by construction — the ``@turnkey/viem``
0.14.x stripping trap, immunized here and pinned by the fake enclave),
legacy/1559 transaction round-trips, vendor error mapping, and the
stamper's wire format. The fake enclave signs with ``eth_account``, so
every signature is real and recoverable.
"""

from __future__ import annotations

import base64
import json
import sys
import time

import pytest
from eth_account import Account
from eth_account.messages import defunct_hash_message, encode_defunct, encode_typed_data
from eth_utils import keccak, to_checksum_address

from bnbagent.signing import PolicyViolation, SigningPolicy
from bnbagent.wallets.capabilities import (
    CALLS_ARBITRARY,
    PAYMASTER_SPONSOR,
    SIGN_MESSAGE,
    SIGN_TRANSACTION,
    SIGN_TYPED_DATA,
)
from bnbagent.wallets.turnkey import (
    TURNKEY_API_BASE_URL_DEFAULT,
    TurnkeyApiError,
    TurnkeyWalletProvider,
)
from bnbagent.wallets.turnkey.stamper import ApiKeyStamper

from .turnkey_fake import FakeTurnkeyClient

# The Turnkey-hosted key the fake "enclave" signs with; SIGN_WITH is its
# address, so signatures recover to the provider address.
TEST_PK = "0x" + "c3" * 32
SIGN_WITH = Account.from_key(TEST_PK).address


def _p256_fixture() -> tuple[str, str]:
    """Deterministic P-256 key pair (private hex, compressed public hex)."""
    ec = pytest.importorskip("cryptography.hazmat.primitives.asymmetric.ec")
    from cryptography.hazmat.primitives.serialization import (
        Encoding,
        PublicFormat,
    )

    private = (
        int("d9" * 32, 16)
        % (0xFFFFFFFF00000000FFFFFFFFFFFFFFFFBCE6FAADA7179E84F3B9CAC2FC632551 - 1)
        + 1
    )
    key = ec.derive_private_key(private, ec.SECP256R1())
    public = key.public_key().public_bytes(Encoding.X962, PublicFormat.CompressedPoint)
    return format(private, "064x"), public.hex()


BASE_KWARGS = {
    "organization_id": "org-123",
    "sign_with": SIGN_WITH,
    "api_public_key": "02" + "ab" * 32,
    "api_private_key": "cd" * 32,
}

# A domain NOT in known_payment_tokens — strict_default must refuse it.
TEST_DOMAIN = {
    "name": "TestToken",
    "version": "1",
    "chainId": 97,
    "verifyingContract": to_checksum_address("0x" + "22" * 20),
}


def eip3009_fixture() -> tuple[dict, dict]:
    now = int(time.time())
    types = {
        "TransferWithAuthorization": [
            {"name": "from", "type": "address"},
            {"name": "to", "type": "address"},
            {"name": "value", "type": "uint256"},
            {"name": "validAfter", "type": "uint256"},
            {"name": "validBefore", "type": "uint256"},
            {"name": "nonce", "type": "bytes32"},
        ],
    }
    message = {
        "from": SIGN_WITH,
        "to": to_checksum_address("0x" + "33" * 20),
        # Big enough to exercise the decimal-string JSON spelling.
        "value": 10**18,
        "validAfter": now - 10,
        "validBefore": now + 580,
        "nonce": "0x" + "44" * 32,
    }
    return types, message


def extended_policy() -> SigningPolicy:
    return SigningPolicy.strict_default().extend(
        domain_allowlist={(97, TEST_DOMAIN["verifyingContract"])}
    )


@pytest.fixture
def fake_client() -> FakeTurnkeyClient:
    return FakeTurnkeyClient(TEST_PK)


def make_provider(fake_client: FakeTurnkeyClient, **overrides) -> TurnkeyWalletProvider:
    kwargs = {**BASE_KWARGS, "client": fake_client, **overrides}
    return TurnkeyWalletProvider(**kwargs)


TURNKEY_ENV = {
    "TURNKEY_API_PUBLIC_KEY": BASE_KWARGS["api_public_key"],
    "TURNKEY_API_PRIVATE_KEY": BASE_KWARGS["api_private_key"],
    "TURNKEY_ORG_ID": BASE_KWARGS["organization_id"],
    "TURNKEY_SIGN_WITH": SIGN_WITH,
}


# ── construction ──────────────────────────────────────────────────────


class TestConstruction:
    def test_capability_surface(self, fake_client):
        provider = make_provider(fake_client)
        assert provider.capabilities() == frozenset(
            {
                SIGN_MESSAGE,
                SIGN_TRANSACTION,
                SIGN_TYPED_DATA,
                CALLS_ARBITRARY,
                PAYMASTER_SPONSOR,
            }
        )
        assert provider.kind == "turnkey"

    def test_address_is_checksummed_from_lowercase(self, fake_client):
        provider = make_provider(fake_client, sign_with=SIGN_WITH.lower())
        assert provider.address == SIGN_WITH

    def test_describe_reports_remote_key_location_without_secrets(self, fake_client):
        provider = make_provider(fake_client)
        info = provider.describe()
        assert info["address"] == SIGN_WITH
        assert "remote:turnkey" in info["key_location"]
        assert TURNKEY_API_BASE_URL_DEFAULT in info["key_location"]
        assert BASE_KWARGS["api_private_key"] not in json.dumps(info)

    @pytest.mark.parametrize(
        "sign_with",
        [
            "3f2504e0-4f89-11d3-9a0c-0305e82c3301",  # Turnkey wallet id (UUID)
            "pk-12345",  # private-key id
            "0x" + "ab" * 19 + "a",  # 39 hex chars
        ],
    )
    def test_rejects_non_address_sign_with(self, fake_client, sign_with):
        with pytest.raises(ValueError, match="Ethereum\\s+.*address|Ethereum "):
            make_provider(fake_client, sign_with=sign_with)

    @pytest.mark.parametrize(
        "field", ["organization_id", "sign_with", "api_public_key", "api_private_key"]
    )
    def test_requires_non_empty_credentials(self, fake_client, field):
        with pytest.raises(ValueError, match=f"{field!r} is required"):
            make_provider(fake_client, **{field: ""})

    def test_construction_is_offline(self, fake_client):
        provider = make_provider(fake_client)
        provider.describe()
        assert fake_client.raw_payload_calls == []
        assert fake_client.transaction_calls == []


# ── from_env ──────────────────────────────────────────────────────────


class TestFromEnv:
    def test_builds_from_the_four_env_vars(self, monkeypatch):
        for key, value in TURNKEY_ENV.items():
            monkeypatch.setenv(key, value)
        monkeypatch.delenv("TURNKEY_API_BASE_URL", raising=False)
        provider = TurnkeyWalletProvider.from_env(expected_chain_id=97)
        assert provider.address == SIGN_WITH
        assert provider.expected_chain_id == 97
        assert TURNKEY_API_BASE_URL_DEFAULT in provider.key_location

    def test_honors_base_url_and_policy(self, monkeypatch):
        for key, value in TURNKEY_ENV.items():
            monkeypatch.setenv(key, value)
        monkeypatch.setenv("TURNKEY_API_BASE_URL", "https://api.turnkey.example")
        policy = extended_policy()
        provider = TurnkeyWalletProvider.from_env(signing_policy=policy)
        assert "https://api.turnkey.example" in provider.key_location
        assert provider.signing_policy is policy

    def test_names_all_missing_env_vars(self, monkeypatch):
        for key in TURNKEY_ENV:
            monkeypatch.delenv(key, raising=False)
        monkeypatch.setenv("TURNKEY_API_PUBLIC_KEY", "02" + "ab" * 32)
        with pytest.raises(
            ValueError,
            match=(
                "missing required env vars: TURNKEY_API_PRIVATE_KEY, "
                "TURNKEY_ORG_ID, TURNKEY_SIGN_WITH"
            ),
        ):
            TurnkeyWalletProvider.from_env()


# ── sign_message (EIP-191) ────────────────────────────────────────────


class TestSignMessage:
    def test_round_trip_recovers_to_provider_address(self, fake_client):
        provider = make_provider(fake_client)
        result = provider.sign_message("hello turnkey")
        assert bytes(result["messageHash"]) == bytes(defunct_hash_message(text="hello turnkey"))
        assert result["v"] in (27, 28)
        recovered = Account.recover_message(
            encode_defunct(text="hello turnkey"), signature=result["signature"]
        )
        assert recovered == provider.address
        # Blind digest path: the enclave saw HEXADECIMAL + NO_OP.
        (call,) = fake_client.raw_payload_calls
        assert call["encoding"] == "PAYLOAD_ENCODING_HEXADECIMAL"
        assert call["hash_function"] == "HASH_FUNCTION_NO_OP"


# ── sign_typed_data (EIP-712) ─────────────────────────────────────────


class TestSignTypedData:
    def test_policy_check_runs_before_any_billable_call(self, fake_client):
        provider = make_provider(fake_client)
        types, message = eip3009_fixture()
        with pytest.raises(PolicyViolation):
            provider.sign_typed_data(TEST_DOMAIN, types, message)
        assert fake_client.raw_payload_calls == []

    def test_signs_after_policy_extension(self, fake_client):
        provider = make_provider(fake_client, signing_policy=extended_policy())
        types, message = eip3009_fixture()
        result = provider.sign_typed_data(TEST_DOMAIN, types, message)
        assert result["v"] in (27, 28)

    def test_payload_carries_the_full_eip712_domain(self, fake_client):
        # The Python provider builds the enclave payload itself, so the
        # @turnkey/viem stripping trap cannot occur — pinned here.
        provider = make_provider(fake_client, signing_policy=extended_policy())
        types, message = eip3009_fixture()
        provider.sign_typed_data(TEST_DOMAIN, types, message)
        (call,) = fake_client.raw_payload_calls
        assert call["encoding"] == "PAYLOAD_ENCODING_EIP712"
        document = json.loads(call["payload"])
        assert document["types"]["EIP712Domain"] == [
            {"name": "name", "type": "string"},
            {"name": "version", "type": "string"},
            {"name": "chainId", "type": "uint256"},
            {"name": "verifyingContract", "type": "address"},
        ]
        assert document["domain"]["chainId"] == 97  # small int stays a number
        assert document["message"]["value"] == str(10**18)  # big int → string
        assert document["primaryType"] == "TransferWithAuthorization"

    def test_replaces_caller_supplied_eip712_domain(self, fake_client):
        provider = make_provider(fake_client, signing_policy=extended_policy())
        types, message = eip3009_fixture()
        with_bogus = {"EIP712Domain": [{"name": "name", "type": "string"}], **types}
        first = provider.sign_typed_data(TEST_DOMAIN, with_bogus, message)
        second = provider.sign_typed_data(TEST_DOMAIN, types, message)
        assert bytes(first["signature"]) == bytes(second["signature"])
        for call in fake_client.raw_payload_calls:
            document = json.loads(call["payload"])
            assert len(document["types"]["EIP712Domain"]) == 4

    def test_binds_the_real_domain_and_reports_the_712_digest(self, fake_client):
        provider = make_provider(fake_client, signing_policy=extended_policy())
        types, message = eip3009_fixture()
        result = provider.sign_typed_data(TEST_DOMAIN, types, message)
        signable = encode_typed_data(
            domain_data=TEST_DOMAIN,
            message_types={k: v for k, v in types.items() if k != "EIP712Domain"},
            message_data=message,
        )
        expected_digest = keccak(b"\x19" + signable.version + signable.header + signable.body)
        assert bytes(result["messageHash"]) == expected_digest
        recovered = Account.recover_message(signable, signature=result["signature"])
        assert recovered == provider.address

    def test_rejects_multi_struct_types_before_any_billable_call(self, fake_client):
        provider = make_provider(fake_client, signing_policy=extended_policy())
        types, message = eip3009_fixture()
        multi = {**types, "Extra": [{"name": "x", "type": "uint256"}]}
        with pytest.raises(PolicyViolation):
            provider.sign_typed_data(TEST_DOMAIN, multi, message)
        assert fake_client.raw_payload_calls == []


# ── sign_transaction ──────────────────────────────────────────────────


LEGACY_TX = {
    "chainId": 97,
    "to": to_checksum_address("0x" + "55" * 20),
    "value": 1,
    "nonce": 0,
    "gas": 21_000,
    "gasPrice": 10_000_000_000,
}


class TestSignTransaction:
    def test_legacy_round_trip(self, fake_client):
        provider = make_provider(fake_client)
        signed = provider.sign_transaction(dict(LEGACY_TX))
        raw = bytes(signed["rawTransaction"])
        assert bytes(signed["hash"]) == keccak(raw)
        # EIP-155 v for chainId 97: 2*97 + 35/36.
        assert signed["v"] in (229, 230)
        assert Account.recover_transaction(raw) == provider.address

    def test_eip1559_round_trip(self, fake_client):
        provider = make_provider(fake_client)
        tx = {
            "chainId": 97,
            "to": to_checksum_address("0x" + "55" * 20),
            "value": 1,
            "nonce": 0,
            "gas": 21_000,
            "maxFeePerGas": 10_000_000_000,
            "maxPriorityFeePerGas": 1_000_000_000,
        }
        signed = provider.sign_transaction(tx)
        raw = bytes(signed["rawTransaction"])
        assert raw[:1] == b"\x02"
        # eth-account semantics: typed transactions report the y-parity bit.
        assert signed["v"] in (0, 1)
        assert Account.recover_transaction(raw) == provider.address

    def test_gas_price_zero_paymaster_shape(self, fake_client):
        # MegaFuel sponsorship signs gasPrice=0 legacy transactions
        # (enclave-verified, probe 2026-07-27); the codec must not drop the
        # canonical zero.
        provider = make_provider(fake_client)
        signed = provider.sign_transaction({**LEGACY_TX, "gasPrice": 0})
        assert Account.recover_transaction(bytes(signed["rawTransaction"]))
        assert signed["v"] in (229, 230)

    def test_contract_creation_omits_to(self, fake_client):
        provider = make_provider(fake_client)
        tx = {**LEGACY_TX, "to": None, "data": "0x6001600155"}
        signed = provider.sign_transaction(tx)
        assert Account.recover_transaction(bytes(signed["rawTransaction"]))

    def test_refuses_chain_id_mismatch_before_the_billable_call(self, fake_client):
        provider = make_provider(fake_client, expected_chain_id=97)
        with pytest.raises(ValueError, match="pinned to chainId=97"):
            provider.sign_transaction({**LEGACY_TX, "chainId": 56})
        assert fake_client.transaction_calls == []
        assert provider.expected_chain_id == 97

    def test_rejects_access_list_transactions(self, fake_client):
        provider = make_provider(fake_client)
        with pytest.raises(ValueError, match="accessList"):
            provider.sign_transaction({**LEGACY_TX, "accessList": [{"address": "0x" + "11" * 20}]})


# ── vendor error mapping ──────────────────────────────────────────────


class TestVendorErrorMapping:
    def test_quota_exhaustion_hint(self, fake_client):
        provider = make_provider(fake_client)
        fake_client.failure = TurnkeyApiError(
            "SIGNING_QUOTA_EXCEEDED for organization", status_code=429
        )
        with pytest.raises(RuntimeError, match="25 billed signatures/month"):
            provider.sign_message("x")

    @pytest.mark.parametrize(
        "message",
        [
            "Turnkey error 8: RATE_LIMIT_EXCEEDED",
            "Turnkey error 8: RATE LIMIT EXCEEDED",
            "Turnkey error 8: rate-limit exceeded",
            "Turnkey error 8: ratelimit hit",
        ],
    )
    def test_rate_limit_hint(self, fake_client, message):
        provider = make_provider(fake_client)
        fake_client.failure = TurnkeyApiError(message)
        with pytest.raises(RuntimeError, match="1 request/second"):
            provider.sign_message("x")

    def test_rate_limit_status_hint(self, fake_client):
        provider = make_provider(fake_client)
        fake_client.failure = TurnkeyApiError("too many requests", status_code=429)
        with pytest.raises(RuntimeError, match="1 request/second"):
            provider.sign_message("x")

    def test_policy_denied_hint(self, fake_client):
        provider = make_provider(fake_client)
        fake_client.failure = TurnkeyApiError(
            "policy engine rejected the activity (POLICY_REJECTED)",
            status_code=403,
        )
        with pytest.raises(RuntimeError, match="explicit ALLOW policy"):
            provider.sign_message("x")

    def test_unrecognized_errors_pass_through(self, fake_client):
        provider = make_provider(fake_client)
        original = TurnkeyApiError("something else entirely")
        fake_client.failure = original
        with pytest.raises(TurnkeyApiError) as excinfo:
            provider.sign_message("x")
        assert excinfo.value is original


# ── stamper wire format ───────────────────────────────────────────────


class TestStamper:
    def test_stamp_shape_and_signature_verify(self):
        private_hex, public_hex = _p256_fixture()
        stamper = ApiKeyStamper(api_public_key=public_hex, api_private_key=private_hex)
        header_name, header_value = stamper.stamp('{"probe":true}')
        assert header_name == "X-Stamp"
        assert "=" not in header_value  # base64url padding stripped
        padded = header_value + "=" * (-len(header_value) % 4)
        stamp = json.loads(base64.urlsafe_b64decode(padded))
        assert stamp["scheme"] == "SIGNATURE_SCHEME_TK_API_P256"
        assert stamp["publicKey"] == public_hex

        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import ec

        public_key = ec.EllipticCurvePublicKey.from_encoded_point(
            ec.SECP256R1(), bytes.fromhex(public_hex)
        )
        # Raises InvalidSignature on mismatch — DER over the exact bytes.
        public_key.verify(
            bytes.fromhex(stamp["signature"]),
            b'{"probe":true}',
            ec.ECDSA(hashes.SHA256()),
        )

    def test_mismatched_key_pair_fails_fast(self):
        private_hex, _ = _p256_fixture()
        with pytest.raises(RuntimeError, match="does not match"):
            ApiKeyStamper(api_public_key="02" + "ab" * 32, api_private_key=private_hex)

    def test_missing_cryptography_yields_install_hint(self, monkeypatch):
        monkeypatch.setitem(sys.modules, "cryptography", None)
        monkeypatch.setitem(sys.modules, "cryptography.hazmat", None)
        monkeypatch.setitem(sys.modules, "cryptography.hazmat.primitives", None)
        with pytest.raises(RuntimeError, match="bnbagent\\[turnkey\\]"):
            ApiKeyStamper(api_public_key="02" + "ab" * 32, api_private_key="cd" * 32)
