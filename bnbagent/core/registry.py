"""
Module registry: discovers, validates, and initializes protocol modules.

Discovery strategies (in order of priority):
1. Explicit: registry.register(MyModule())
2. Built-in: auto-register known first-party modules
3. Entry points: third-party modules via pyproject.toml [project.entry-points]
"""

from __future__ import annotations

import importlib
import logging
from collections import OrderedDict
from typing import Any, Dict, List, Optional

from .module import BNBAgentModule, ModuleInfo

logger = logging.getLogger(__name__)

# Built-in modules that are always available
_BUILTIN_MODULES: Dict[str, str] = {
    "erc8004": "bnbagent.erc8004",
    "apex": "bnbagent.apex",
}


class ModuleRegistry:
    """
    Central registry for protocol modules.

    Usage:
        registry = ModuleRegistry()
        registry.discover()
        registry.initialize_all(config)

        # Or register explicitly
        registry.register(MyCustomModule())
    """

    def __init__(self):
        self._modules: OrderedDict[str, BNBAgentModule] = OrderedDict()
        self._initialized = False

    def register(self, module: BNBAgentModule) -> None:
        """Register a module instance. Raises if already registered."""
        info = module.info()
        if info.name in self._modules:
            raise ValueError(f"Module '{info.name}' is already registered")
        self._modules[info.name] = module
        logger.info(f"Registered module: {info.name} v{info.version}")

    def unregister(self, name: str) -> None:
        """Unregister a module by name."""
        if name in self._modules:
            mod = self._modules.pop(name)
            mod.shutdown()
            logger.info(f"Unregistered module: {name}")

    def get(self, name: str) -> Optional[BNBAgentModule]:
        """Get a registered module by name."""
        return self._modules.get(name)

    def list_modules(self) -> List[ModuleInfo]:
        """List all registered modules."""
        return [m.info() for m in self._modules.values()]

    @property
    def module_names(self) -> List[str]:
        return list(self._modules.keys())

    def discover(self, include_entry_points: bool = True) -> None:
        """
        Auto-discover and register modules.

        1. Import built-in modules from bnbagent.*
        2. Scan 'bnbagent.modules' entry point group
        """
        for name, module_path in _BUILTIN_MODULES.items():
            if name in self._modules:
                continue
            try:
                mod = importlib.import_module(module_path)
                factory = getattr(mod, "create_module", None)
                if factory:
                    self.register(factory())
            except ImportError as e:
                logger.debug(f"Skipping built-in {name}: {e}")

        if include_entry_points:
            self._discover_entry_points()

    def _discover_entry_points(self) -> None:
        """Scan pyproject.toml entry points for third-party modules."""
        try:
            from importlib.metadata import entry_points

            eps = entry_points(group="bnbagent.modules")
            for ep in eps:
                if ep.name in self._modules:
                    continue
                try:
                    factory = ep.load()
                    self.register(factory())
                    logger.info(f"Loaded entry-point module: {ep.name}")
                except Exception as e:
                    logger.warning(f"Failed to load entry-point '{ep.name}': {e}")
        except Exception:
            pass

    def merge_default_configs(self) -> Dict[str, Any]:
        """Merge default configs from all modules. Later modules override earlier."""
        merged: Dict[str, Any] = {}
        for mod in self._modules.values():
            merged.update(mod.default_config())
        return merged

    def validate_dependencies(self) -> List[str]:
        """Validate that all module dependencies are satisfied.

        Returns list of error messages (empty = all OK).
        """
        errors = []
        for mod in self._modules.values():
            info = mod.info()
            for dep in info.dependencies:
                if dep not in self._modules:
                    errors.append(
                        f"Module '{info.name}' requires '{dep}' but it is not registered"
                    )
        return errors

    def initialize_all(self, config: Optional[Dict[str, Any]] = None, **kwargs) -> None:
        """
        Initialize all modules in dependency order.

        1. Topological sort by dependencies
        2. Merge default configs with user config
        3. initialize(config, **kwargs) on each module
        """
        errors = self.validate_dependencies()
        if errors:
            from .exceptions import ConfigurationError

            raise ConfigurationError(
                "Module dependency errors:\n" + "\n".join(f"  - {e}" for e in errors)
            )

        ordered = self._topological_sort()

        merged_config = self.merge_default_configs()
        if config:
            merged_config.update(config)

        for name in ordered:
            mod = self._modules[name]
            mod.initialize(merged_config, **kwargs)
            logger.info(f"Initialized module: {name}")

        self._initialized = True

    def _topological_sort(self) -> List[str]:
        """Topological sort of modules by dependencies."""
        visited: set[str] = set()
        result: List[str] = []

        def visit(name: str):
            if name in visited:
                return
            visited.add(name)
            mod = self._modules.get(name)
            if mod:
                for dep in mod.info().dependencies:
                    visit(dep)
            result.append(name)

        for name in self._modules:
            visit(name)
        return result

    def get_all_actions(self) -> List[Dict[str, Any]]:
        """Collect action descriptors from all modules."""
        actions: List[Dict[str, Any]] = []
        for mod in self._modules.values():
            actions.extend(mod.get_actions())
        return actions

    def shutdown_all(self) -> None:
        """Shutdown all modules in reverse order."""
        for mod in reversed(list(self._modules.values())):
            try:
                mod.shutdown()
            except Exception as e:
                logger.warning(f"Shutdown error: {e}")
        self._initialized = False
