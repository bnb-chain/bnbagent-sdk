"""Optional telemetry exporters (``[exporters]`` extra): OpenTelemetry, Prometheus, MLflow.

Each backend module has ``export(response, **opts)`` and imports its library lazily. No case
text (metric reasons, content details) is exported unless ``include_text=True``.
"""

from __future__ import annotations

import importlib
from typing import Any

from ...types.request import EvalResponse

EXPORTERS: tuple[str, ...] = ("otel", "prometheus", "mlflow")


def export(response: EvalResponse, backend: str, **opts: Any) -> Any:
    """Dispatch to ``reporting.exporters.<backend>.export`` (see :data:`EXPORTERS`)."""
    if backend not in EXPORTERS:
        raise ValueError(f"unknown exporter {backend!r}; expected one of {', '.join(EXPORTERS)}")
    module = importlib.import_module(f"{__name__}.{backend}")
    return module.export(response, **opts)


__all__ = ["EXPORTERS", "export"]
