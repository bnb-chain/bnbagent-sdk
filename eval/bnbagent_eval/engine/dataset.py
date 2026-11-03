"""Load evaluation cases from local files (JSON array / {"cases": [...]}, JSONL, YAML)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..exceptions import RequestError, SchemaError
from ..types.case import EvalCase


def parse_cases(data: Any, *, source: str = "<data>") -> tuple[EvalCase, ...]:
    if isinstance(data, dict) and "cases" in data:
        data = data["cases"]
    if not isinstance(data, list):
        raise RequestError(f"{source}: expected a list of cases or an object with 'cases'")
    try:
        return tuple(EvalCase.from_dict(item) for item in data)
    except SchemaError as exc:
        raise RequestError(f"{source}: {exc}") from exc


def load_cases(path: str | Path) -> tuple[EvalCase, ...]:
    p = Path(path)
    try:
        text = p.read_text(encoding="utf-8")
    except OSError as exc:
        raise RequestError(f"cannot read {p}: {exc}") from exc
    suffix = p.suffix.lower()
    if suffix == ".jsonl":
        data = []
        for n, line in enumerate(text.splitlines(), 1):
            if line.strip():
                try:
                    data.append(json.loads(line))
                except ValueError as exc:
                    raise RequestError(f"{p}:{n}: not valid JSON: {exc}") from exc
    elif suffix in (".yaml", ".yml"):
        import yaml

        try:
            data = yaml.safe_load(text)
        except yaml.YAMLError as exc:
            raise RequestError(f"{p}: not valid YAML: {exc}") from exc
    else:
        try:
            data = json.loads(text)
        except ValueError as exc:
            raise RequestError(f"{p}: not valid JSON: {exc}") from exc
    return parse_cases(data, source=str(p))


def load_dataset_ref(ref: str) -> tuple[EvalCase, ...]:
    """Load a local dataset reference ``path`` or ``path#0x<keccak of the file bytes>``.

    Remote datasets (https / ipfs) are fetched only by the eval service; locally, download
    the file (or pass ``cases``).
    """
    from ..hashing import keccak_hex

    path, _, expected_hash = ref.partition("#")
    path = path.removeprefix("file://")
    if "://" in path:
        raise RequestError(
            "a remote dataset_ref is only fetched by the eval service: "
            "pass a local file path or cases"
        )
    if expected_hash:
        try:
            actual = keccak_hex(Path(path).read_bytes())
        except OSError as exc:
            raise RequestError(f"cannot read {path}: {exc}") from exc
        if actual.lower() != expected_hash.lower():
            raise RequestError(
                f"dataset hash mismatch for {path}: expected {expected_hash}, got {actual}"
            )
    return load_cases(path)
