"""Lazy loading of optional dependencies (extras)."""

from __future__ import annotations

import importlib
import importlib.util
from types import ModuleType

from .exceptions import MissingExtraError


def available(module: str) -> bool:
    """Return True if ``module`` can be imported, without importing it."""
    try:
        return importlib.util.find_spec(module) is not None
    except (ImportError, ValueError):
        return False


def require(module: str, extra: str) -> ModuleType:
    """Import ``module`` or raise :class:`MissingExtraError` naming the extra to install."""
    try:
        return importlib.import_module(module)
    except ImportError as exc:
        raise MissingExtraError(module, extra) from exc
