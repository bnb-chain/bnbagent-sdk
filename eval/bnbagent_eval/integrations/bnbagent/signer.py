"""``SignerPort`` over a ``WalletProvider``; ``AssetCatalogPort`` over ``bnbagent.networks``."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from bnbagent.networks import ASSET_CATALOG

from ...exceptions import ChainIntegrationError
from ...ports import AssetSpec
from ._errors import INVALID_REQUEST, to_chain_error, to_hex

__all__ = ["SdkAssetCatalog", "WalletSigner"]

UNSUPPORTED_MESSAGE = "unsupported_message"
UNSUPPORTED_TYPED_DATA = "unsupported_typed_data"


class WalletSigner:
    """``SignerPort`` wrapping a ``WalletProvider``. Signing stays synchronous.

    SDK limits: ``sign_message`` takes text (EIP-191 over its UTF-8), so non-UTF-8 bytes are
    refused; ``sign_typed_data`` infers the primary type, so exactly one non-domain struct is
    allowed; the wallet's ``SigningPolicy`` applies (fail-closed by default).
    """

    def __init__(self, wallet: Any):
        self.wallet = wallet
        self.address = str(wallet.address)

    def supports(self, capability: str) -> bool:
        caps = getattr(self.wallet, "capabilities", None)
        if callable(caps):
            return capability in caps()
        return callable(getattr(self.wallet, capability.replace(".", "_"), None))

    def sign_typed_data(self, typed_data: Mapping[str, Any]) -> str:
        types = {k: list(v) for k, v in dict(typed_data["types"]).items()}
        structs = [k for k in types if k != "EIP712Domain"]
        primary = typed_data.get("primaryType")
        if len(structs) != 1 or (primary is not None and structs != [primary]):
            raise ChainIntegrationError(
                f"the SDK signs exactly one EIP-712 struct; got {structs}",
                code=UNSUPPORTED_TYPED_DATA,
            )
        try:
            signed = self.wallet.sign_typed_data(
                dict(typed_data["domain"]), types, dict(typed_data["message"])
            )
        except Exception as exc:
            raise to_chain_error(exc, op="sign_typed_data") from exc
        return to_hex(signed["signature"])

    def sign_message(self, message: bytes) -> str:
        try:
            text = bytes(message).decode("utf-8")
        except UnicodeDecodeError:
            raise ChainIntegrationError(
                "SDK wallets sign text only; message bytes are not UTF-8",
                code=UNSUPPORTED_MESSAGE,
            ) from None
        try:
            signed = self.wallet.sign_message(text)
        except Exception as exc:
            raise to_chain_error(exc, op="sign_message") from exc
        return to_hex(signed["signature"])


class SdkAssetCatalog:
    """``AssetCatalogPort`` from the SDK's fail-closed ``ASSET_CATALOG`` (active assets only).

    Addresses and decimals are the x402 (B402) execution view of each asset.
    """

    def __init__(self, catalog: Any = None):
        self.catalog = ASSET_CATALOG if catalog is None else catalog

    def assets(self, chain_id: int) -> list[AssetSpec]:
        try:
            listed = self.catalog.list(chain_id)
        except KeyError:
            return []
        out = []
        for asset in listed:
            try:
                b402 = self.catalog.get_b402(chain_id, asset.asset_id)
            except KeyError as exc:
                raise ChainIntegrationError(str(exc), code=INVALID_REQUEST) from exc
            domain = asset.eip3009_domain
            out.append(
                AssetSpec(
                    chain_id=int(chain_id),
                    address=str(b402.address),
                    symbol=str(asset.symbol),
                    decimals=int(b402.decimals),
                    methods=tuple(asset.b402_methods),
                    eip712_domain=None
                    if domain is None
                    else {"name": domain.name, "version": domain.version},
                )
            )
        return out
