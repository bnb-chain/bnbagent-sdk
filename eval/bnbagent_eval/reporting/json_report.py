from __future__ import annotations

import json

from ..types.request import EvalResponse


def to_json(response: EvalResponse, *, indent: int | None = 2) -> str:
    return json.dumps(response.to_dict(), indent=indent, ensure_ascii=False, sort_keys=False)
