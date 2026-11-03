"""Schema-version handling for serialized contracts.

Readers accept older versions through explicit migrations, reject newer major versions, and keep
unknown fields (via ``extensions``) when round-tripping.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from ..exceptions import SchemaError
from .request import SCHEMA_VERSION

_PREFIX = "bnbagent-eval/v"
CURRENT_MAJOR = int(SCHEMA_VERSION.removeprefix(_PREFIX))

# major version -> function upgrading a payload to major + 1
MIGRATIONS: dict[int, Callable[[dict[str, Any]], dict[str, Any]]] = {}


def parse_version(version: str) -> int:
    if not isinstance(version, str) or not version.startswith(_PREFIX):
        raise SchemaError(f"unrecognized schema_version {version!r}")
    try:
        return int(version.removeprefix(_PREFIX).split(".")[0])
    except ValueError as exc:
        raise SchemaError(f"unrecognized schema_version {version!r}") from exc


def upgrade(data: Mapping[str, Any]) -> dict[str, Any]:
    """Return ``data`` migrated to the current schema version."""
    payload = dict(data)
    major = parse_version(payload.get("schema_version", SCHEMA_VERSION))
    if major > CURRENT_MAJOR:
        raise SchemaError(
            f"schema_version v{major} is newer than this package supports (v{CURRENT_MAJOR}); "
            "upgrade bnbagent-eval"
        )
    while major < CURRENT_MAJOR:
        if major not in MIGRATIONS:
            raise SchemaError(f"no migration from schema v{major}")
        payload = MIGRATIONS[major](payload)
        major += 1
    payload["schema_version"] = SCHEMA_VERSION
    return payload
