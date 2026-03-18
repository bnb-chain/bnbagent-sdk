"""
BNBAgent -- high-level convenience wrapper.

Optional facade over the module system. Users can also use modules directly.
"""

from __future__ import annotations

import logging
from typing import Any

from .config import BNBAgentConfig
from .core.module import BNBAgentModule
from .core.registry import ModuleRegistry

logger = logging.getLogger(__name__)


class BNBAgent:
    """
    High-level SDK entry point.

    Usage:
        sdk = BNBAgent.from_env()

        # Access modules
        apex = sdk.module("apex")

        # Get all AI actions (reserved)
        actions = sdk.actions()
    """

    def __init__(
        self,
        config: BNBAgentConfig,
        modules: list[str] | None = None,
        **kwargs,
    ):
        self._config = config
        self._registry = ModuleRegistry()

        self._registry.discover()
        if modules is not None:
            for name in list(self._registry.module_names):
                if name not in modules:
                    self._registry.unregister(name)

        self._registry.initialize_all(config.to_flat_dict(), **kwargs)

    @classmethod
    def from_env(
        cls,
        modules: list[str] | None = None,
        **kwargs,
    ) -> BNBAgent:
        """Create SDK from environment variables."""
        config = BNBAgentConfig.from_env()
        return cls(config, modules=modules, **kwargs)

    def module(self, name: str) -> BNBAgentModule | None:
        """Get an initialized module by name."""
        return self._registry.get(name)

    def actions(self) -> list[dict[str, Any]]:
        """Get all AI-invocable actions from all modules."""
        return self._registry.get_all_actions()

    @property
    def config(self) -> BNBAgentConfig:
        return self._config

    @property
    def registry(self) -> ModuleRegistry:
        return self._registry

    def shutdown(self) -> None:
        """Shutdown all modules."""
        self._registry.shutdown_all()
