"""bnbagent SDK adapter: mapping, errors, threads, URIs, parity, compat, watch.

Everything runs against mocked SDK objects or pure SDK code; no RPC, no network.
"""

from __future__ import annotations

import asyncio
import importlib
import json
import random
import threading
import time
from unittest.mock import AsyncMock, MagicMock

import pytest

pytest.importorskip("bnbagent")

import bnbagent  # noqa: E402
from bnbagent.config import NetworkConfig  # noqa: E402
from bnbagent.core.contract_mixin import READ_ONLY_MESSAGE  # noqa: E402
from bnbagent.erc8004.agent_uri import AgentURIGenerator  # noqa: E402
from bnbagent.erc8183.job_ops import _exc_error_fields  # noqa: E402
from bnbagent.erc8183.policy import PolicyClient  # noqa: E402
from bnbagent.erc8183.schema import DeliverableManifest  # noqa: E402
from bnbagent.erc8183.types import Job, JobStatus  # noqa: E402
from bnbagent.exceptions import (  # noqa: E402
    ContractError,
    NetworkError,
    RelaySubmissionUnverifiedError,
    RpcRangeLimitError,
    StorageError,
    TransactionPendingError,
)
from bnbagent.signing import PolicyViolation, SigningPolicy  # noqa: E402
from bnbagent.storage import LocalStorageProvider  # noqa: E402
from bnbagent.utils.public_http import PublicHttpError, fetch_public_json  # noqa: E402
from bnbagent.wallets import EVMWalletProvider  # noqa: E402
from bnbagent.wallets.errors import UnsupportedWalletOperation  # noqa: E402
from eth_account import Account  # noqa: E402
from eth_account.messages import encode_defunct, encode_typed_data  # noqa: E402
from web3.exceptions import ContractLogicError  # noqa: E402

import bnbagent_eval.integrations.bnbagent as integ  # noqa: E402
from bnbagent_eval.chain.attestation import TYPES  # noqa: E402
from bnbagent_eval.chain.manifest import manifest_hash  # noqa: E402
from bnbagent_eval.exceptions import ChainIntegrationError  # noqa: E402
from bnbagent_eval.integrations.bnbagent import (  # noqa: E402
    BnbagentChainReader,
    BnbagentCommerce,
    BnbagentPolicy,
    BnbagentRegistry,
    BnbagentStorage,
    ReadOnlyIdentityRegistry,
    SdkAssetCatalog,
    UriResolver,
    WalletSigner,
    compat,
    from_error_dict,
    job_view,
    plain_job_dict,
    to_chain_error,
    watch,
)
from bnbagent_eval.integrations.bnbagent import _factory as factory  # noqa: E402
from bnbagent_eval.ports import (  # noqa: E402
    AssetCatalogPort,
    CommercePort,
    PolicyPort,
    RegistryPort,
    SignerPort,
    StoragePort,
)
from bnbagent_eval.testing.fakes import encode_data_uri  # noqa: E402

# Hardhat / Anvil dev account 0 (public test key).
ANVIL_KEY = (
    "0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80"  # secret-scan: allow
)
CLIENT = "0x" + "c1" * 20
PROVIDER = "0x" + "d2" * 20
ROUTER = "0x" + "82" * 20
CID = "Qm" + "a" * 44
NETWORK = NetworkConfig(
    name="mock",
    chain_id=97,
    rpc_url="http://localhost:1",
    registry_contract="0x" + "80" * 20,
    commerce_contract="0x" + "81" * 20,
    router_contract=ROUTER,
    policy_contract="0x" + "83" * 20,
)


def sdk_job(**kw) -> Job:
    base = dict(
        id=7,
        client=CLIENT,
        provider=PROVIDER,
        evaluator=ROUTER,
        description='{"task": "t"}',
        budget=10**18,
        expired_at=2_000_000_000,
        status=JobStatus.FUNDED,
        hook=ROUTER,
    )
    return Job(**{**base, **kw})


def mock_client() -> MagicMock:
    client = MagicMock()
    client.network = NETWORK
    client.commerce.address = NETWORK.commerce_contract
    return client


def revert(message: str = "execution reverted") -> RuntimeError:
    try:
        raise ContractLogicError(message)
    except ContractLogicError as exc:
        try:
            raise RuntimeError(f"Failed: {exc}") from exc
        except RuntimeError as wrapped:
            return wrapped


# --------------------------------------------------------------------------- mapping


def test_job_view_from_sdk_job_dataclass() -> None:
    deliverable = bytes.fromhex("ab" * 32)
    view = job_view(sdk_job(status=JobStatus.SUBMITTED, deliverable=deliverable, submitted_at=5))
    assert (view.job_id, view.status, view.client, view.provider) == (
        7,
        "submitted",
        CLIENT,
        PROVIDER,
    )
    assert view.deliverable == "0x" + "ab" * 32 and view.submitted_at == 5
    assert (view.budget, view.evaluator, view.expired_at) == (10**18, ROUTER, 2_000_000_000)


def test_job_view_from_camelcase_job_ops_dict() -> None:
    raw = {  # the shape of ERC8183JobOps.get_job / funded_job_watcher jobs
        "success": True,
        "jobId": 3,
        "client": CLIENT,
        "provider": PROVIDER,
        "evaluator": ROUTER,
        "description": "plain",
        "budget": 5,
        "expiredAt": 1_900_000_000,
        "submittedAt": 0,
        "status": JobStatus.FUNDED,
        "hook": ROUTER,
        "deliverable": "0x" + "00" * 32,
    }
    view = job_view(raw)
    assert (view.job_id, view.status, view.expired_at) == (3, "funded", 1_900_000_000)
    assert view.deliverable is None and view.submitted_at is None  # zero values → None
    assert type(view.status) is str
    plain = plain_job_dict(raw)
    assert plain["status"] == "funded" and plain["jobId"] == 3 and plain["expiredAt"]


@pytest.mark.parametrize(
    "status,name",
    [(JobStatus.OPEN, "open"), (2, "submitted"), ("REJECTED", "rejected"), ("5", "expired")],
)
def test_status_names(status, name) -> None:
    assert integ.status_name(status) == name


def test_status_unknown_rejected() -> None:
    with pytest.raises(ValueError):
        integ.status_name(9)


def test_asset_catalog_maps_sdk_assets() -> None:
    catalog = SdkAssetCatalog()
    assert isinstance(catalog, AssetCatalogPort)
    specs = catalog.assets(56)
    u = next(s for s in specs if s.symbol == "U")
    assert u.methods == ("eip3009", "permit2-exact")
    assert u.eip712_domain == {"name": "United Stables", "version": "1"} and u.decimals == 18
    assert all(s.chain_id == 56 for s in specs)
    assert catalog.assets(12345) == []  # no catalog for this chain


# --------------------------------------------------------------------------- errors


@pytest.mark.parametrize(
    "exc,code,retryable",
    [
        (TransactionPendingError("0x" + "aa" * 32, 300), "tx_pending", False),
        (RelaySubmissionUnverifiedError("0x" + "aa" * 32, 300), "relay_unverified", False),
        (PolicyViolation("unknown domain"), "signing_policy_violation", False),
        (UnsupportedWalletOperation("sign.typed_data"), "unsupported_wallet_operation", False),
        (RuntimeError(READ_ONLY_MESSAGE), "read_only", False),
        (PublicHttpError("Public URL resolves to a disallowed address"), "fetch_refused", False),
        (PublicHttpError("Public download timed out"), "fetch_failed", True),
        (StorageError("Invalid IPFS CID format"), "storage_error", False),
        (ContractError("reverted"), "contract_reverted", False),
        (revert(), "contract_reverted", False),
        (NetworkError("rpc down"), "chain_unavailable", True),
        (RpcRangeLimitError("-32005"), "chain_unavailable", True),
        (ConnectionError("refused"), "chain_unavailable", True),
        (RuntimeError("429 Too Many Requests"), "chain_unavailable", True),
        (ValueError("RPC chain_id mismatch"), "invalid_request", False),
        (RuntimeError("boom"), "internal_error", True),
    ],
)
def test_exception_mapping(exc, code, retryable) -> None:
    err = to_chain_error(exc, op="op")
    assert isinstance(err, ChainIntegrationError)
    assert (err.code, err.retryable) == (code, retryable)


def test_exception_mapping_keeps_tx_hash_and_redacts_urls() -> None:
    err = to_chain_error(TransactionPendingError("0x" + "ab" * 32, 60), op="submit")
    assert err.tx_hash == "0x" + "ab" * 32
    err = to_chain_error(RuntimeError("failed at https://rpc.example/KEY123 now"), op="x")
    assert "KEY123" not in str(err) and "<redacted>" in str(err)


@pytest.mark.parametrize(
    "exc,code,retryable",
    [
        (TransactionPendingError("0x" + "ab" * 32, 60), "tx_pending", False),
        (RuntimeError("429 too many requests"), "chain_unavailable", True),
        (RuntimeError("weird"), "internal_error", True),
    ],
)
def test_sdk_error_dicts_map(exc, code, retryable) -> None:
    fields = {"success": False, **_exc_error_fields(exc)}  # the SDK's own error dict builder
    err = from_error_dict(fields, op="submit")
    assert (err.code, err.retryable) == (code, retryable)


async def test_submit_result_maps_sdk_error_dict() -> None:
    ops = MagicMock()
    ops.submit_result = AsyncMock(
        return_value={
            "success": False,
            "error": "Job verification failed: Job status is OPEN, expected FUNDED",
            "error_code": "wrong_status",
        }
    )
    result = await BnbagentCommerce(mock_client(), job_ops=ops).submit_result(1, "x", {"a": 1})
    assert not result.success and result.error_code == "wrong_status" and not result.retryable
    ops.submit_result.assert_awaited_once_with(1, "x", {"a": 1})

    ops.submit_result = AsyncMock(return_value={"success": False, **_exc_error_fields(
        TransactionPendingError("0x" + "cd" * 32, 60)
    )})  # fmt: skip
    result = await BnbagentCommerce(mock_client(), job_ops=ops).submit_result(1, "x")
    assert result.error_code == "tx_pending" and result.tx_hash == "0x" + "cd" * 32

    ops.submit_result = AsyncMock(
        return_value={
            "success": True,
            "txHash": bytes.fromhex("ee" * 32),
            "deliverableUrl": "ipfs://x",
            "deliverable": "0x" + "11" * 32,
        }
    )
    result = await BnbagentCommerce(mock_client(), job_ops=ops).submit_result(1, "x")
    assert result.success and result.tx_hash == "0x" + "ee" * 32
    assert result.deliverable_url == "ipfs://x"


async def test_submit_result_maps_raised_errors_and_read_only() -> None:
    ops = MagicMock()
    ops.submit_result = AsyncMock(side_effect=NetworkError("down"))
    result = await BnbagentCommerce(mock_client(), job_ops=ops).submit_result(1, "x")
    assert (result.success, result.error_code, result.retryable) == (
        False,
        "chain_unavailable",
        True,
    )
    result = await BnbagentCommerce(mock_client()).submit_result(1, "x")
    assert (result.success, result.error_code) == (False, "read_only")


async def test_registry_write_errors_raise_chain_integration_error() -> None:
    identity = MagicMock()
    identity.set_metadata.side_effect = TransactionPendingError("0x" + "aa" * 32, 300)
    identity.set_agent_uri.return_value = {"success": False, "error": "nope", "error_code": "x"}
    reg = BnbagentRegistry(identity, chain_id=97, registry_address="0x" + "80" * 20)
    with pytest.raises(ChainIntegrationError) as info:
        await reg.set_metadata(1, "k", "v")
    assert info.value.code == "tx_pending" and not info.value.retryable
    with pytest.raises(ChainIntegrationError) as info:
        await reg.set_agent_uri(1, "data:,{}")
    assert info.value.code == "x"


async def test_read_only_policy_client_write_maps_to_read_only() -> None:
    """A real wallet-less PolicyClient refuses writes; the adapter reports ``read_only``."""
    client = MagicMock()
    client.policy = PolicyClient(MagicMock(), NETWORK.policy_contract, None)
    client.dispute.side_effect = client.policy.dispute
    with pytest.raises(ChainIntegrationError) as info:
        await BnbagentPolicy(client).dispute(1)
    assert (info.value.code, info.value.retryable) == ("read_only", False)


async def test_get_job_unknown_is_not_found() -> None:
    client = mock_client()
    zero = "0x" + "00" * 20
    client.get_job.return_value = Job(0, zero, zero, zero, "", 0, 0, JobStatus.OPEN, zero)
    with pytest.raises(ChainIntegrationError) as info:
        await BnbagentCommerce(client).get_job(99)
    assert info.value.code == "not_found"


# --------------------------------------------------------------------------- threads


def _threaded(record: list, value=None):
    def side_effect(*args, **kwargs):
        record.append(threading.get_ident())
        return value

    return side_effect


async def test_sync_sdk_calls_run_in_worker_threads() -> None:
    main = threading.get_ident()
    seen: list[int] = []
    client = mock_client()
    client.get_job.side_effect = _threaded(seen, sdk_job())
    client.get_deliverable_url.side_effect = _threaded(seen, None)
    client.dispute.side_effect = _threaded(seen, {"transactionHash": "0x" + "aa" * 32})
    policy = MagicMock(spec=PolicyClient)
    policy.dispute_window.side_effect = _threaded(seen, 60)
    policy.disputed.side_effect = _threaded(seen, False)
    client.policy = policy
    identity = MagicMock()
    identity.get_agent_info.side_effect = _threaded(seen, {"agentURI": "", "owner": CLIENT})
    identity.get_metadata.side_effect = _threaded(seen, "v")
    w3 = MagicMock()
    w3.eth.get_balance.side_effect = _threaded(seen, 1)

    commerce = BnbagentCommerce(client)
    policy_port = BnbagentPolicy(client)
    registry = BnbagentRegistry(identity, chain_id=97, registry_address="0x" + "80" * 20)
    reader = BnbagentChainReader(w3, chain_id=97)
    await commerce.get_job(7)
    await commerce.get_deliverable(7)
    await policy_port.dispute_window()
    await policy_port.is_disputed(7)
    await policy_port.dispute(7)
    await registry.owner_of(1)
    await registry.get_metadata(1, "k")
    await reader.get_balance(CLIENT)
    assert len(seen) == 8 and main not in seen


async def test_to_thread_is_the_mechanism(monkeypatch) -> None:
    from bnbagent_eval.integrations.bnbagent import _errors

    calls = []
    real = asyncio.to_thread

    async def spy(fn, *args, **kwargs):
        calls.append(fn)
        return await real(fn, *args, **kwargs)

    monkeypatch.setattr(_errors.asyncio, "to_thread", spy)
    client = mock_client()
    client.get_job.return_value = sdk_job()
    await BnbagentCommerce(client).get_job(7)
    assert calls == [client.get_job]


# --------------------------------------------------------------------------- registration reads


def registry_with(uri: str | None, **resolver_kw) -> BnbagentRegistry:
    identity = MagicMock()
    if uri is None:
        identity.get_agent_info.side_effect = revert("ERC721NonexistentToken")
    else:
        identity.get_agent_info.return_value = {"agentURI": uri, "owner": CLIENT}
    return BnbagentRegistry(
        identity,
        chain_id=97,
        registry_address="0x" + "80" * 20,
        resolver=UriResolver(**resolver_kw),
    )


async def test_read_registration_data_uri_from_sdk_generator() -> None:
    uri = AgentURIGenerator.generate_agent_uri(
        name="demo", description="d", endpoints=[{"name": "web", "endpoint": "https://a.example"}]
    )
    reg = registry_with(uri)
    assert isinstance(reg, RegistryPort)
    assert (await reg.read_registration(1))["name"] == "demo"
    assert (await registry_with(encode_data_uri({"name": "x"})).read_registration(1)) == {
        "name": "x"
    }
    plain = "data:application/json," + "%7B%22name%22%3A%22p%22%7D"
    assert (await registry_with(plain).read_registration(1)) == {"name": "p"}


async def test_read_registration_missing_agent_is_none() -> None:
    reg = registry_with(None)
    assert await reg.read_registration(404) is None
    assert await reg.owner_of(404) is None


async def test_read_registration_bad_data_uri() -> None:
    with pytest.raises(ChainIntegrationError) as info:
        await registry_with("data:application/json;base64,!!!!").read_registration(1)
    assert info.value.code == "invalid_request"


async def test_read_registration_https_uses_guarded_fetch_in_thread() -> None:
    seen: list[int] = []
    fetch = MagicMock(side_effect=lambda url: seen.append(threading.get_ident()) or {"name": "h"})
    reg = registry_with("https://agent.example/reg.json", fetch_json=fetch)
    assert (await reg.read_registration(1)) == {"name": "h"}
    fetch.assert_called_once_with("https://agent.example/reg.json")
    assert seen and seen[0] != threading.get_ident()
    assert UriResolver()._fetch_json is fetch_public_json  # the SDK's SSRF-guarded fetch


async def test_read_registration_https_private_address_refused() -> None:
    """The real SDK fetch refuses a loopback target before connecting."""
    with pytest.raises(ChainIntegrationError) as info:
        await registry_with("https://127.0.0.1/reg.json").read_registration(1)
    assert (info.value.code, info.value.retryable) == ("fetch_refused", False)


async def test_read_registration_ipfs_through_storage() -> None:
    provider = AsyncMock()
    provider.uses_file_url = False
    provider.download.return_value = {"name": "ipfs-agent"}
    reg = registry_with(f"ipfs://{CID}", provider=provider)
    assert (await reg.read_registration(1)) == {"name": "ipfs-agent"}
    provider.download.assert_awaited_once_with(f"ipfs://{CID}")


async def test_read_registration_ipfs_through_gateway(tmp_path) -> None:
    fetch = MagicMock(return_value={"name": "gw"})
    # No storage, or a file:// provider that cannot serve ipfs: → the public gateway.
    for provider in (None, LocalStorageProvider(str(tmp_path))):
        fetch.reset_mock()
        reg = registry_with(
            f"ipfs://{CID}",
            provider=provider,
            fetch_json=fetch,
            ipfs_gateway="https://gw.example/ipfs/",
        )
        assert (await reg.read_registration(1)) == {"name": "gw"}
        fetch.assert_called_once_with(f"https://gw.example/ipfs/{CID}")
    with pytest.raises(ChainIntegrationError) as info:
        await registry_with("ipfs://not-a-cid/../x", fetch_json=fetch).read_registration(1)
    assert info.value.code == "fetch_refused"


async def test_read_registration_unknown_scheme_without_storage() -> None:
    with pytest.raises(ChainIntegrationError) as info:
        await registry_with("ar://abc").read_registration(1)
    assert info.value.code == "storage_error"


async def test_storage_port_over_real_local_provider(tmp_path) -> None:
    storage = BnbagentStorage(LocalStorageProvider(str(tmp_path)))
    assert isinstance(storage, StoragePort)
    uri = await storage.upload({"a": "ü", "b": 1.5}, "doc.json")
    assert uri.startswith("file://") and await storage.exists(uri)
    assert await storage.download(uri) == {"a": "ü", "b": 1.5}
    with pytest.raises(ChainIntegrationError) as info:
        await storage.upload({"a": 1}, "../escape.json")
    assert info.value.code == "storage_error"


def test_read_only_identity_registry_reads_contract() -> None:
    web3 = MagicMock()
    fns = web3.eth.contract.return_value.functions
    fns.ownerOf.return_value.call.return_value = CLIENT
    fns.tokenURI.return_value.call.return_value = "data:,{}"
    fns.getAgentWallet.return_value.call.return_value = PROVIDER
    fns.getMetadata.return_value.call.return_value = b"v"
    reg = ReadOnlyIdentityRegistry(web3, "0x" + "80" * 20)
    info = reg.get_agent_info(3)
    assert (info["owner"], info["agentURI"]) == (CLIENT, "data:,{}")
    assert reg.get_metadata(3, "k") == "v"
    with pytest.raises(RuntimeError, match="read-only"):
        reg.set_metadata(3, "k", "v")


# --------------------------------------------------------------------------- deliverables


async def test_get_deliverable_verifies_sdk_manifest_and_detects_tamper() -> None:
    manifest = DeliverableManifest(
        version=1,
        job_id=7,
        chain_id=97,
        contracts={"commerce": "0x1", "router": "0x2", "policy": "0x3"},
        response={"content": "résumé ✓", "content_type": "text/plain"},
        metadata={"eval": {"score": 0.875}},
    )
    stored = {"doc": manifest.to_dict()}
    provider = AsyncMock()
    provider.uses_file_url = False
    provider.download.side_effect = lambda url: json.loads(json.dumps(stored["doc"]))
    client = mock_client()
    client.get_deliverable_url.return_value = "ipfs://" + CID
    client.get_job.return_value = sdk_job(
        status=JobStatus.SUBMITTED, deliverable=manifest.manifest_hash(), submitted_at=10
    )
    commerce = BnbagentCommerce(client, resolver=UriResolver(provider))
    assert isinstance(commerce, CommercePort)
    view = await commerce.get_deliverable(7)
    assert view.verified and view.content == "résumé ✓"
    assert view.manifest_hash == "0x" + manifest.manifest_hash().hex()
    assert view.metadata == {"eval": {"score": 0.875}}

    stored["doc"]["response"]["content"] = "tampered"
    assert not (await commerce.get_deliverable(7)).verified
    stored["doc"] = {"version": 2, "response": {"content": "x"}}
    assert not (await commerce.get_deliverable(7)).verified


# --------------------------------------------------------------------------- † parity


def _value(rng: random.Random, depth: int = 0):
    kinds = ["int", "float", "str", "bool", "none"] + (["list", "dict"] if depth < 3 else [])
    kind = rng.choice(kinds)
    if kind == "int":
        return rng.randint(-(10**20), 10**20)
    if kind == "float":
        return rng.choice([0.1, 1e-7, 1e21, -0.0, 3.14159, 2.5, 1 / 3, rng.uniform(-1e6, 1e6)])
    if kind == "str":
        alphabet = 'abcXYZ019 "\\\n\t/éüßØ漢字テスト🙂 \x00'
        return "".join(rng.choice(alphabet) for _ in range(rng.randint(0, 12)))
    if kind == "bool":
        return rng.random() < 0.5
    if kind == "none":
        return None
    if kind == "list":
        return [_value(rng, depth + 1) for _ in range(rng.randint(0, 4))]
    return {_value_key(rng): _value(rng, depth + 1) for _ in range(rng.randint(0, 4))}


def _value_key(rng: random.Random) -> str:
    return "".join(rng.choice("kéy_漢🙂Z09") for _ in range(rng.randint(1, 6)))


def test_manifest_hash_parity_with_sdk() -> None:
    rng = random.Random(8183)
    for i in range(50):
        data = {
            "version": 1,
            "job_id": rng.randint(0, 10**12),
            "chain_id": rng.choice([56, 97, 31337]),
            "contracts": {"commerce": "0x" + f"{rng.getrandbits(160):040x}", "router": "0x2"},
            "response": {"content": str(_value(rng)), "content_type": "text/plain"},
            "metadata": {_value_key(rng): _value(rng) for _ in range(rng.randint(0, 5))},
        }
        if i % 7 == 0:
            data["metadata"]["score"] = rng.random()
        sdk = DeliverableManifest.from_dict(json.loads(json.dumps(data))).manifest_hash()
        assert manifest_hash(data) == "0x" + sdk.hex(), data


# --------------------------------------------------------------------------- signer


def wallet(**kw) -> EVMWalletProvider:
    return EVMWalletProvider(password="test", private_key=ANVIL_KEY, persist=False, **kw)


def attestation_typed_data(verifying: str) -> dict:
    return {
        "types": TYPES,
        "primaryType": "Attestation",
        "domain": {
            "name": "bnbagent-eval",
            "version": "1",
            "chainId": 97,
            "verifyingContract": verifying,
        },
        "message": {
            "resultHash": "0x" + "11" * 32,
            "subjectHash": "0x" + "22" * 32,
            "suiteHash": "0x" + "33" * 32,
            "status": 1,
            "scoreBps": 9000,
            "evaluator": Account.from_key(ANVIL_KEY).address,
            "issuedAt": 1,
            "expiresAt": 2,
        },
    }


def test_wallet_signer_typed_data_recovers_with_allowlisted_policy() -> None:
    verifying = "0x" + "81" * 20
    from web3 import Web3

    policy = SigningPolicy.strict_default().extend(
        domain_allowlist={(97, Web3.to_checksum_address(verifying))},
        primary_type_allowlist={"Attestation"},
    )
    signer = WalletSigner(wallet(signing_policy=policy))
    assert isinstance(signer, SignerPort)
    assert signer.supports("sign.typed_data") and signer.supports("sign.message")
    td = attestation_typed_data(verifying)
    sig = signer.sign_typed_data(td)
    assert Account.recover_message(encode_typed_data(full_message=td), signature=sig) == (
        signer.address
    )


def test_wallet_signer_strict_policy_refuses_unknown_domain() -> None:
    signer = WalletSigner(wallet())
    with pytest.raises(ChainIntegrationError) as info:
        signer.sign_typed_data(attestation_typed_data("0x" + "81" * 20))
    assert info.value.code == "signing_policy_violation"


def test_wallet_signer_message_limits() -> None:
    signer = WalletSigner(wallet())
    sig = signer.sign_message(b"hello")
    assert Account.recover_message(encode_defunct(text="hello"), signature=sig) == signer.address
    with pytest.raises(ChainIntegrationError) as info:
        signer.sign_message(bytes.fromhex("ff" * 32))  # a raw digest is not text
    assert info.value.code == "unsupported_message"
    td = attestation_typed_data("0x" + "81" * 20)
    td["types"] = {**td["types"], "Extra": [{"name": "x", "type": "uint256"}]}
    with pytest.raises(ChainIntegrationError) as info:
        signer.sign_typed_data(td)
    assert info.value.code == "unsupported_typed_data"


# --------------------------------------------------------------------------- compat


@pytest.mark.parametrize("version", ["0.4.9", "0.5.0", "0.7.0", "0.7.0rc1", "1.0.0", "garbage"])
def test_compat_rejects_out_of_range(version) -> None:
    with pytest.raises(compat.UnsupportedSDKVersion) as info:
        compat.check_version(version)
    assert isinstance(info.value, ImportError) and isinstance(info.value, ChainIntegrationError)
    assert info.value.code == "unsupported_sdk_version" and ">=0.5.1,<0.7" in str(info.value)


@pytest.mark.parametrize("version", ["0.5.1", "0.5.9", "0.6.0", "0.6.12.post1"])
def test_compat_accepts_in_range(version) -> None:
    assert compat.check_version(version) == version


def test_compat_checked_on_import(monkeypatch) -> None:
    monkeypatch.setattr(bnbagent, "__version__", "0.7.1")
    with pytest.raises(ImportError, match="0.7.1") as info:  # reload redefines the class
        importlib.reload(compat)
    assert info.value.code == "unsupported_sdk_version"
    monkeypatch.undo()
    importlib.reload(compat)
    assert compat.SDK_VERSION == bnbagent.__version__


def test_compat_features() -> None:
    feats = compat.features()
    assert feats.success_criteria_signed is True  # 0.5.1 embeds success_criteria in the hash
    assert feats.policy_vote_window is False and feats.job_submitted_at is True


async def test_vote_window_is_none_without_sdk_support() -> None:
    client = mock_client()
    client.policy = MagicMock(spec=PolicyClient)
    policy = BnbagentPolicy(client)
    assert isinstance(policy, PolicyPort)
    assert await policy.vote_window() is None


# --------------------------------------------------------------------------- ports()


def test_ports_read_only_without_wallet(monkeypatch) -> None:
    client = mock_client()
    client.w3 = MagicMock()
    built = MagicMock(return_value=client)
    agent = MagicMock()
    monkeypatch.setattr(factory, "ERC8183Client", built)
    monkeypatch.setattr(factory, "ERC8004Agent", agent)
    bundle = integ.ports(NETWORK)
    built.assert_called_once_with(None, NETWORK)
    agent.assert_not_called()
    assert isinstance(bundle.registry.identity, ReadOnlyIdentityRegistry)
    assert bundle.signer is None and bundle.storage is None and bundle.payer is None
    assert bundle.commerce.job_ops is None and bundle.assets is not None
    assert bundle.registry.registry_address == NETWORK.registry_contract
    assert asyncio.run(bundle.commerce.submit_result(1, "x")).error_code == "read_only"


def test_ports_with_wallet_and_storage(monkeypatch, tmp_path) -> None:
    client = mock_client()
    built, agent, ops = MagicMock(return_value=client), MagicMock(), MagicMock()
    monkeypatch.setattr(factory, "ERC8183Client", built)
    monkeypatch.setattr(factory, "ERC8004Agent", agent)
    monkeypatch.setattr(factory, "ERC8183JobOps", ops)
    w = wallet()
    provider = LocalStorageProvider(str(tmp_path))
    bundle = integ.ports(NETWORK, wallet=w, storage=provider, job_ops_kwargs={"service_price": 5})
    built.assert_called_once_with(w, NETWORK)
    agent.assert_called_once_with(w, NETWORK)
    ops.assert_called_once_with(w, NETWORK, storage_provider=provider, service_price=5)
    assert isinstance(bundle.signer, WalletSigner) and bundle.signer.address == w.address
    assert bundle.storage.provider is provider
    assert bundle.payer is None  # EVM wallets have no SDK x402 payer yet
    with pytest.raises(TypeError, match="unknown"):
        integ.ports(NETWORK, wallet=w, bogus=1)


def test_ports_construction_errors_are_mapped(monkeypatch) -> None:
    monkeypatch.setattr(
        factory, "ERC8183Client", MagicMock(side_effect=ValueError("RPC mismatch"))
    )
    with pytest.raises(ChainIntegrationError) as info:
        integ.ports(NETWORK)
    assert info.value.code == "invalid_request"


# --------------------------------------------------------------------------- watch()


def watcher_ops(job_ids: list[int]) -> MagicMock:
    """Mocked job ops for the REAL ``funded_job_watcher``."""

    def job(job_id: int) -> dict:
        return {
            "success": True,
            "jobId": job_id,
            "client": CLIENT,
            "provider": PROVIDER,
            "status": JobStatus.FUNDED,
            "expiredAt": int(time.time()) + 3600,
            "deliverable": "0x" + "00" * 32,
        }

    ops = MagicMock()
    ops.get_pending_jobs = AsyncMock(
        side_effect=[{"success": True, "jobs": [job(i) for i in job_ids]}]
        + [{"success": True, "jobs": []}] * 100
    )
    ops.get_job = AsyncMock(side_effect=lambda job_id: job(job_id))
    ops.verify_job = AsyncMock(side_effect=lambda job_id: {"valid": True, "job": job(job_id)})
    return ops


async def test_watch_drives_real_watcher_retry_and_done() -> None:
    stop = asyncio.Event()
    calls: list[tuple[int, str]] = []
    plan = {
        1: [{"retry": True}, {"retry": True}, {"submitted": True}],  # retried, then done
        2: [None],  # fire-once
        3: [False] * 10,  # always retry → capped by max_attempts
    }

    class Gate:  # async __call__, like EvalGate
        async def __call__(self, job: dict):
            calls.append((job["jobId"], job["status"]))
            result = plan[job["jobId"]].pop(0)
            if sum(1 for j, _ in calls if j == 3) >= 4:
                stop.set()
            return result

    ops = watcher_ops([1, 2, 3])
    await asyncio.wait_for(
        watch(Gate(), job_ops=ops, interval=0.01, stop=stop, max_attempts=4), timeout=10
    )
    counts = {j: sum(1 for c in calls if c[0] == j) for j in plan}
    assert counts == {1: 3, 2: 1, 3: 4}
    assert all(status == "funded" for _, status in calls)  # plain status, not the SDK IntEnum
    assert ops.verify_job.await_count == len(calls)  # verified before every callback


async def test_watch_gate_exception_retries() -> None:
    stop = asyncio.Event()
    calls = []

    async def gate(job):
        calls.append(job["jobId"])
        if len(calls) == 1:
            raise RuntimeError("transient")
        stop.set()
        return None

    await asyncio.wait_for(
        watch(gate, job_ops=watcher_ops([5]), interval=0.01, stop=stop), timeout=10
    )
    assert calls == [5, 5]


def test_wallet_env_kwargs_shim_both_paths(monkeypatch) -> None:
    from bnbagent.core.config import AgentConfig

    from bnbagent_eval.integrations.bnbagent import compat, env

    class WithReader:
        @classmethod
        def _wallet_kwargs_from_env(cls):
            return {
                "private_key": "",
                "wallet_password": "",
                "wallet_address": "",
                "wallet_kind": "x",
            }

    assert compat.wallet_env_kwargs(WithReader)["wallet_kind"] == "x"
    with pytest.raises(ChainIntegrationError, match="_wallet_kwargs_from_env") as info:
        compat.wallet_env_kwargs(type("Bare", (), {}))
    assert info.value.code == "sdk_api_missing"
    # the installed SDK still has it: nothing configured -> no wallet
    for key in ("PRIVATE_KEY", "WALLET_PASSWORD", "WALLET_ADDRESS", "WALLET_KIND"):
        monkeypatch.delenv(key, raising=False)
    assert set(compat.wallet_env_kwargs()) == {
        "private_key",
        "wallet_password",
        "wallet_address",
        "wallet_kind",
    }
    assert env.wallet_from_env() is None
    # an SDK release without the helper fails with the clear error, not AttributeError
    monkeypatch.delattr(AgentConfig, "_wallet_kwargs_from_env")
    with pytest.raises(ChainIntegrationError, match="build the wallet yourself"):
        env.wallet_from_env()
