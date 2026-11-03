"""Supported bnbagent SDK range and feature detection.

Checked on import: ``integrations.bnbagent`` refuses to load outside :data:`SUPPORTED_RANGE`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache

from ...exceptions import ChainIntegrationError, MissingExtraError

SUPPORTED_RANGE = ">=0.5.1,<0.7"
MIN_VERSION = (0, 5, 1)
MAX_VERSION_EXCLUSIVE = (0, 7, 0)

__all__ = [
    "MAX_VERSION_EXCLUSIVE",
    "MIN_VERSION",
    "SDK_VERSION",
    "SDKFeatures",
    "SUPPORTED_RANGE",
    "UnsupportedSDKVersion",
    "check_version",
    "features",
    "parse_version",
    "wallet_env_kwargs",
]


class UnsupportedSDKVersion(ChainIntegrationError, ImportError):
    """The installed bnbagent SDK is outside :data:`SUPPORTED_RANGE`."""

    def __init__(self, version: str):
        self.version = version
        super().__init__(
            f"bnbagent {version} is not supported by bnbagent-eval (requires bnbagent"
            f"{SUPPORTED_RANGE}). Install a supported release: "
            f"pip install 'bnbagent{SUPPORTED_RANGE}'",
            code="unsupported_sdk_version",
            retryable=False,
        )


def parse_version(version: str) -> tuple[int, int, int]:
    """Leading ``major.minor.patch`` of a version string (pre-release suffixes ignored)."""
    match = re.match(r"^\s*v?(\d+)(?:\.(\d+))?(?:\.(\d+))?", version)
    if not match:
        raise ValueError(f"unparseable version {version!r}")
    return tuple(int(part or 0) for part in match.groups())  # type: ignore[return-value]


def _installed_version() -> str:
    try:
        import bnbagent
    except ImportError as exc:
        raise MissingExtraError("bnbagent", "bnbagent") from exc
    return str(getattr(bnbagent, "__version__", "0"))


def check_version(version: str | None = None) -> str:
    """Return ``version`` (default: installed) or raise :class:`UnsupportedSDKVersion`."""
    version = _installed_version() if version is None else version
    try:
        parsed = parse_version(version)
    except ValueError:
        raise UnsupportedSDKVersion(version) from None
    if not MIN_VERSION <= parsed < MAX_VERSION_EXCLUSIVE:
        raise UnsupportedSDKVersion(version)
    return version


SDK_VERSION = check_version()


@dataclass(frozen=True)
class SDKFeatures:
    """Optional SDK behaviour the adapter (or its callers) branch on."""

    success_criteria_signed: bool  # terms.success_criteria is inside the signed job description
    policy_vote_window: bool  # PolicyClient exposes a vote window
    job_submitted_at: bool  # Job carries the on-chain submittedAt
    wallet_capabilities: bool  # WalletProvider.capabilities() / supports()


def _success_criteria_signed() -> bool:
    try:
        from bnbagent.erc8183 import negotiation

        content = negotiation._build_description_content(
            {
                "request": {"task_description": "probe"},
                "response": {
                    "accepted": True,
                    "terms": {
                        "price": "1",
                        "currency": "0x" + "00" * 20,
                        "deliverables": "d",
                        "quality_standards": "q",
                        "success_criteria": ["eval_suite:probe"],
                    },
                },
                "negotiated_at": 1,
            }
        )
    except Exception:
        return False
    return bool(content.get("terms", {}).get("success_criteria"))


@lru_cache(maxsize=1)
def features() -> SDKFeatures:
    from bnbagent.erc8183.policy import PolicyClient
    from bnbagent.erc8183.types import Job
    from bnbagent.wallets import WalletProvider

    return SDKFeatures(
        success_criteria_signed=_success_criteria_signed(),
        policy_vote_window=callable(getattr(PolicyClient, "vote_window", None)),
        job_submitted_at="submitted_at" in getattr(Job, "__dataclass_fields__", {}),
        wallet_capabilities=callable(getattr(WalletProvider, "capabilities", None)),
    )


def wallet_env_kwargs(agent_config: type | None = None) -> dict[str, str]:
    """The SDK's wallet env vars as ``AgentConfig`` kwargs (``private_key``,
    ``wallet_password``, ``wallet_address``, ``wallet_kind``).

    The SDK (0.5 / 0.6) reads them only in the private ``AgentConfig._wallet_kwargs_from_env``
    (its ``*Config.from_env`` loaders call it); there is no public equivalent, so the call
    is kept here. Raises :class:`ChainIntegrationError` (``sdk_api_missing``) if a release
    drops it. ``agent_config`` overrides the class (tests).
    """
    if agent_config is None:
        from bnbagent.core.config import AgentConfig

        agent_config = AgentConfig
    reader = getattr(agent_config, "_wallet_kwargs_from_env", None)
    if not callable(reader):
        raise ChainIntegrationError(
            f"bnbagent {SDK_VERSION} has no AgentConfig._wallet_kwargs_from_env: build the "
            "wallet yourself (e.g. AgentConfig(network=..., private_key=..., "
            "wallet_password=...).wallet_provider) and pass it to "
            "integrations.bnbagent.ports(network, wallet=...)",
            code="sdk_api_missing",
            retryable=False,
        )
    return dict(reader())
