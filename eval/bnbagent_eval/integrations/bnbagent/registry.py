"""``RegistryPort`` over ``ERC8004Agent`` (or a wallet-less read-only view of the registry)."""

from __future__ import annotations

from typing import Any

from bnbagent.core.abis import load_abi
from bnbagent.core.contract_mixin import READ_ONLY_MESSAGE
from bnbagent.core.rpc_errors import is_revert_error
from web3 import Web3

from ._errors import call, tx_hash
from .storage import UriResolver

__all__ = ["BnbagentRegistry", "ReadOnlyIdentityRegistry"]


class ReadOnlyIdentityRegistry:
    """The read subset of ``ERC8004Agent`` without a wallet (``ERC8004Agent`` requires one).

    Same return shapes as ``bnbagent.erc8004.contract.ContractInterface``; writes raise the SDK's
    read-only error.
    """

    def __init__(self, web3: Any, contract_address: str):
        self.web3 = web3
        self.contract_address = Web3.to_checksum_address(contract_address)
        self.contract = web3.eth.contract(
            address=self.contract_address, abi=load_abi("IdentityRegistry.json")
        )

    def get_agent_info(self, agent_id: int) -> dict[str, Any]:
        fns = self.contract.functions
        try:
            owner = fns.ownerOf(agent_id).call()
            uri = fns.tokenURI(agent_id).call()
            wallet = fns.getAgentWallet(agent_id).call()
        except Exception as exc:
            raise RuntimeError(f"Failed to get agent info: {exc}") from exc
        return {
            "agentId": agent_id,
            "agentAddress": wallet,
            "agentWallet": wallet,
            "owner": owner,
            "agentURI": uri,
        }

    def get_metadata(self, agent_id: int, key: str) -> str:
        try:
            return self.contract.functions.getMetadata(agent_id, key).call().decode("utf-8")
        except Exception as exc:
            raise RuntimeError(f"Failed to get metadata: {exc}") from exc

    def set_agent_uri(self, agent_id: int, agent_uri: str) -> dict[str, Any]:
        raise RuntimeError(READ_ONLY_MESSAGE)

    def set_metadata(self, agent_id: int, key: str, value: str) -> dict[str, Any]:
        raise RuntimeError(READ_ONLY_MESSAGE)


class BnbagentRegistry:
    """``RegistryPort``. ``identity`` is an ``ERC8004Agent`` or :class:`ReadOnlyIdentityRegistry`.

    A nonexistent agent (``ownerOf`` reverts) reads as ``None``.
    """

    def __init__(
        self,
        identity: Any,
        *,
        chain_id: int,
        registry_address: str,
        resolver: UriResolver | None = None,
    ):
        self.identity = identity
        self.chain_id = int(chain_id)
        self.registry_address = str(registry_address)
        self.resolver = resolver or UriResolver()

    async def _info(self, agent_id: int) -> dict[str, Any] | None:
        try:
            return await call(self.identity.get_agent_info, agent_id, op="get_agent_info")
        except Exception as exc:
            if is_revert_error(exc):
                return None
            raise

    async def get_agent_uri(self, agent_id: int) -> str | None:
        info = await self._info(agent_id)
        return (info or {}).get("agentURI") or None

    async def read_registration(self, agent_id: int) -> dict[str, Any] | None:
        uri = await self.get_agent_uri(agent_id)
        return None if uri is None else await self.resolver.read(uri)

    async def owner_of(self, agent_id: int) -> str | None:
        info = await self._info(agent_id)
        return (info or {}).get("owner") or None

    async def get_metadata(self, agent_id: int, key: str) -> str | None:
        try:
            value = await call(self.identity.get_metadata, agent_id, key, op="get_metadata")
        except Exception as exc:
            if is_revert_error(exc):
                return None
            raise
        return value or None

    async def set_agent_uri(self, agent_id: int, uri: str) -> str:
        result = await call(self.identity.set_agent_uri, agent_id, uri, op="set_agent_uri")
        return tx_hash(result, op="set_agent_uri")

    async def set_metadata(self, agent_id: int, key: str, value: str) -> str:
        result = await call(self.identity.set_metadata, agent_id, key, value, op="set_metadata")
        return tx_hash(result, op="set_metadata")
