"""The generated metric catalogue (docs/METRICS.md) is up to date and deterministic."""

from __future__ import annotations

from pathlib import Path

from bnbagent_eval.authoring.catalog_doc import render_catalog
from bnbagent_eval.cli import main
from bnbagent_eval.metrics.registry import aliases, list_specs, resolve

DOC = Path(__file__).resolve().parents[2] / "docs" / "METRICS.md"
REGENERATE = "bnbagent-eval metrics --markdown > docs/METRICS.md"


def test_committed_catalogue_is_up_to_date():
    assert DOC.read_text(encoding="utf-8") == render_catalog(), (
        f"docs/METRICS.md is stale; regenerate it with `{REGENERATE}`"
    )


def test_render_is_deterministic():
    assert render_catalog() == render_catalog()


def test_every_builtin_metric_and_alias_is_listed():
    text = render_catalog()
    builtin = {
        s.id for s in list_specs() if resolve(s.id)[0].__module__.startswith("bnbagent_eval.")
    }
    assert len(builtin) >= 99
    for metric_id in builtin:
        assert f"| `{metric_id}` |" in text
    for alias, (target, _method) in aliases().items():
        if target in builtin:
            assert f"| `{alias}` |" in text
    assert "## Aliases" in text


def test_runtime_registered_metrics_are_left_out():
    from bnbagent_eval.metrics.base import BaseMetric, Kind, MethodSpec, MetricSpec

    class Local(BaseMetric):
        spec = MetricSpec(
            id="text.local_only",
            category="text",
            description="x",
            methods=(MethodSpec("rule", Kind.RULE),),
        )

    specs = [*list_specs(), Local.spec]
    assert "text.local_only" not in render_catalog(specs)
    assert "text.local_only" in render_catalog(specs, builtin_only=False)


def test_cli_markdown_and_check(tmp_path, capsys):
    assert main(["metrics", "--markdown"]) == 0
    out = capsys.readouterr().out
    assert out == render_catalog()
    good = tmp_path / "METRICS.md"
    good.write_text(out, encoding="utf-8")
    assert main(["metrics", "--markdown", "--check", str(good)]) == 0
    stale = tmp_path / "stale.md"
    stale.write_text(out + "extra\n", encoding="utf-8")
    assert main(["metrics", "--markdown", "--check", str(stale)]) == 1
    assert "stale" in capsys.readouterr().err
    assert main(["metrics", "--check", str(tmp_path / "missing.md")]) == 64
