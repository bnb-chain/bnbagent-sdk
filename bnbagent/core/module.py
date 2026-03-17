"""
Base module interface for bnbagent protocol modules.

Every protocol module (ERC-8004, APEX, Escrow, x402, etc.) implements
this interface. The ModuleRegistry discovers and initializes modules.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Dict, Sequence


@dataclass(frozen=True)
class ModuleInfo:
    """Immutable metadata for a module."""

    name: str
    version: str
    description: str
    dependencies: tuple[str, ...] = ()


class BNBAgentModule(ABC):
    """
    Abstract base class for all bnbagent protocol modules.

    Lifecycle:
      1. __init__() — module created (no I/O)
      2. initialize(config, **kwargs) — receives merged config + shared infra
      3. get_actions() — returns AI-invocable action descriptors (reserved)
      4. shutdown() — cleanup
    """

    @abstractmethod
    def info(self) -> ModuleInfo:
        """Return immutable metadata for this module."""
        ...

    @abstractmethod
    def default_config(self) -> Dict[str, Any]:
        """Return default configuration keys and values.

        These are merged (lowest priority) into the unified config.
        """
        ...

    def initialize(self, config: Dict[str, Any], **kwargs) -> None:
        """Initialize the module with merged configuration.

        Args:
            config: Flat dict of merged configuration from all modules.
            **kwargs: Shared infrastructure (web3, wallet, etc.).
                      Modules extract what they need.
        """
        self._config = config

    def get_actions(self) -> Sequence[Dict[str, Any]]:
        """Return AI-invocable actions this module provides.

        Reserved for future AI framework integration (LangChain, AgentKit, etc.).
        Default: no actions.
        """
        return ()

    def shutdown(self) -> None:
        """Cleanup resources. Default: no-op."""
        pass
