"""The maintainer guide's old -> new map is generated from docs/design/source-map.md."""

from __future__ import annotations

import importlib.util
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "render_source_map.py"


def _module():
    spec = importlib.util.spec_from_file_location("render_source_map", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_guide_map_is_current() -> None:
    old, new = _module().updated_guide()
    assert old == new, "run: python3 eval/scripts/render_source_map.py"


def test_map_covers_every_source_item() -> None:
    m = _module()
    tables = m.source_tables(m.SOURCE_MAP.read_text(encoding="utf-8"))
    # ETK 112 + AgentSight 129 + STED 9 + earlier prototype 8 (see docs/design/source-map.md)
    assert sum(len(rows) for _, _, rows in tables) == 258
    assert all(m._target(row) for _, _, rows in tables for row in rows)
