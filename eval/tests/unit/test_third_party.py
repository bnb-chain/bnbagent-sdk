"""Third-party methods are labelled, installable with one extra, and listed."""

from __future__ import annotations

import pytest

from bnbagent_eval.authoring.catalog_doc import render_third_party, third_party_methods
from bnbagent_eval.metrics import registry
from bnbagent_eval.metrics.base import Kind
from bnbagent_eval.metrics.vendors import PROVIDERS


def test_every_vendor_or_model_method_with_an_extra_names_its_provider() -> None:
    for spec in registry.list_specs():
        for m in spec.methods:
            if m.kind in (Kind.VENDOR, Kind.MODEL) and m.extra:
                assert m.extra in PROVIDERS, (spec.id, m.name, m.extra)


def test_providers_are_installable_extras() -> None:
    tomllib = pytest.importorskip("tomllib")
    from pathlib import Path

    pyproject = Path(__file__).resolve().parents[2] / "pyproject.toml"
    extras = tomllib.loads(pyproject.read_text())["project"]["optional-dependencies"]
    for extra in PROVIDERS:
        assert extras.get(extra), f"[{extra}] must install the provider"


def test_listing_and_native_defaults() -> None:
    listed = third_party_methods()
    assert {"ragas", "deepeval", "fmeval", "agentcore"} <= set(listed)
    assert ("rag.faithfulness", "ragas") in listed["ragas"]
    for extra, items in listed.items():
        if PROVIDERS[extra].role != "evaluator":
            continue  # "model": our implementation (e.g. BERTScore) may be the default
        for metric_id, method in items:
            assert registry.get_spec(metric_id).methods[0].name != method  # native first
    section = render_third_party()
    assert "pip install 'bnbagent-eval[deepeval]'" in section and "| evaluator |" in section


def test_cli_lists_third_party_methods(capsys) -> None:
    from bnbagent_eval.cli import main

    assert main(["metrics", "--third-party"]) == 0
    out = capsys.readouterr().out
    assert "RAGAS (evaluator): pip install 'bnbagent-eval[ragas]'" in out
    assert "method: agentcore" in out
