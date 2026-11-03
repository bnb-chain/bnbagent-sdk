"""Ports on the bnbagent SDK — the only module that imports ``bnbagent``.

``ports(network, wallet, storage)`` builds every port. Without a wallet the bundle is read-only
(registry / commerce / policy reads, reader, assets); writes then fail with ``read_only``.
Adapters run synchronous SDK calls in ``asyncio.to_thread``, return neutral value types and raise
:class:`~bnbagent_eval.exceptions.ChainIntegrationError` (``code``, ``retryable``).
"""

from __future__ import annotations

from . import compat  # checks the SDK version before any other SDK import

# isort: split

from ._convert import job_view, plain_job_dict, status_name
from ._errors import from_error_dict, to_chain_error
from ._factory import ports
from .commerce import BnbagentCommerce, BnbagentPolicy
from .compat import SDK_VERSION, SUPPORTED_RANGE, SDKFeatures, UnsupportedSDKVersion, features
from .reader import BnbagentChainReader
from .registry import BnbagentRegistry, ReadOnlyIdentityRegistry
from .signer import SdkAssetCatalog, WalletSigner
from .storage import DEFAULT_IPFS_GATEWAY, BnbagentStorage, UriResolver
from .watch import watch

__all__ = [
    "DEFAULT_IPFS_GATEWAY",
    "SDK_VERSION",
    "SUPPORTED_RANGE",
    "BnbagentChainReader",
    "BnbagentCommerce",
    "BnbagentPolicy",
    "BnbagentRegistry",
    "BnbagentStorage",
    "ReadOnlyIdentityRegistry",
    "SDKFeatures",
    "SdkAssetCatalog",
    "UnsupportedSDKVersion",
    "UriResolver",
    "WalletSigner",
    "compat",
    "features",
    "from_error_dict",
    "job_view",
    "plain_job_dict",
    "ports",
    "status_name",
    "to_chain_error",
    "watch",
]
