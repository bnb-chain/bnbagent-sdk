from __future__ import annotations

import json

import pytest

from bnbagent_eval.engine import build_lock, resolve_suite
from bnbagent_eval.exceptions import SuiteError
from bnbagent_eval.types import LockEntry, MetricConfig, Suite

PY = {"id": "custom.python", "params": {"fn": "contract_echo"}}


def test_load_yaml_with_local_ref(tmp_path) -> None:
    (tmp_path / "params.json").write_text(json.dumps({"fn": "x"}))
    (tmp_path / "s.yaml").write_text(
        "name: s\nversion: '1'\nmetrics:\n  - id: custom.python\n    params: {$ref: params.json}\n"
    )
    suite = Suite.load(tmp_path / "s.yaml")
    assert suite.metrics[0].params == {"fn": "x"}
    h1 = resolve_suite(suite).suite_hash
    (tmp_path / "params.json").write_text(json.dumps({"fn": "y"}))
    assert resolve_suite(Suite.load(tmp_path / "s.yaml")).suite_hash != h1


def test_remote_ref_rejected() -> None:
    with pytest.raises(SuiteError, match="remote"):
        Suite.load(
            {
                "name": "s",
                "version": "1",
                "metrics": [{"id": "custom.python", "params": {"$ref": "https://x/p.json"}}],
            }
        )


@pytest.mark.parametrize(
    "bad",
    [
        {"name": "s", "version": "1", "metrics": []},
        {"name": "s", "version": "1", "metrics": [PY, PY]},
        {"name": "s", "version": "1", "metrics": [{**PY, "threshold": 2}]},
        {"name": "s", "version": "1", "metrics": [{**PY, "bogus": 1}]},
    ],
)
def test_invalid_suites(bad) -> None:
    with pytest.raises(SuiteError):
        Suite.load(bad)


def test_resolution_errors() -> None:
    with pytest.raises(SuiteError, match="unknown metric"):
        resolve_suite(Suite(name="s", version="1", metrics=(MetricConfig(id="nope.metric"),)))
    with pytest.raises(SuiteError, match="no method"):
        resolve_suite(
            Suite(
                name="s",
                version="1",
                metrics=(MetricConfig(id="custom.python", method="llm", params={"fn": "x"}),),
            )
        )
    with pytest.raises(SuiteError, match="invalid params"):
        resolve_suite(Suite(name="s", version="1", metrics=(MetricConfig(id="custom.python"),)))


def test_lock_pins_versions() -> None:
    suite = Suite(
        name="s", version="1", metrics=(MetricConfig(id="custom.python", params={"fn": "x"}),)
    )
    lock = build_lock(suite)
    locked = Suite(name="s", version="1", metrics=suite.metrics, lock=lock)
    assert resolve_suite(locked).suite_hash == resolve_suite(suite).suite_hash
    stale = Suite(
        name="s",
        version="1",
        metrics=suite.metrics,
        lock={"custom.python": LockEntry(metric_version="0")},
    )
    with pytest.raises(SuiteError, match="lock mismatch"):
        resolve_suite(stale)


def test_lock_keys_a_repeated_metric_id_by_name() -> None:
    from bnbagent_eval.authoring.lint import lock_suite

    metrics = (
        MetricConfig(id="quality.answer_correctness", name="correctness_judge"),
        MetricConfig(id="quality.answer_correctness", name="correctness_rule", method="rule"),
        MetricConfig(id="text.exact_match"),
    )
    suite = Suite(name="s", version="1", metrics=metrics)
    lock = build_lock(suite)
    assert set(lock) == {"correctness_judge", "correctness_rule", "text.exact_match"}
    assert lock["correctness_judge"].prompt_hash != lock["correctness_rule"].prompt_hash
    locked = lock_suite(suite)
    assert dict(locked.lock) == lock
    assert resolve_suite(locked).suite_hash == resolve_suite(suite).suite_hash
    # each use is verified against its own pinned entry
    stale = {**lock, "correctness_judge": LockEntry(metric_version="0")}
    with pytest.raises(SuiteError, match="lock mismatch for correctness_judge"):
        resolve_suite(Suite(name="s", version="1", metrics=metrics, lock=stale))
    # a lock keyed by the bare metric id does not cover the repeated uses
    legacy = {
        "quality.answer_correctness": lock["correctness_judge"],
        "text.exact_match": lock["text.exact_match"],
    }
    with pytest.raises(SuiteError, match="no entry for correctness_judge"):
        resolve_suite(Suite(name="s", version="1", metrics=metrics, lock=legacy))
    # a name equal to another metric's id would collide in the lock
    clash = (
        MetricConfig(id="quality.answer_correctness", name="text.exact_match_x"),
        MetricConfig(id="quality.answer_correctness", name="text.contains", method="rule"),
        MetricConfig(id="text.contains", name="contains"),
    )
    with pytest.raises(SuiteError, match="lock key 'text.contains' is used twice"):
        build_lock(Suite(name="s", version="1", metrics=clash))


def test_suite_hash_changes_with_threshold() -> None:
    a = Suite(
        name="s", version="1", metrics=(MetricConfig(id="custom.python", params={"fn": "x"}),)
    )
    b = Suite(
        name="s",
        version="1",
        metrics=(MetricConfig(id="custom.python", params={"fn": "x"}, threshold=0.9),),
    )
    assert resolve_suite(a).suite_hash != resolve_suite(b).suite_hash


async def test_register_method_attaches_vendor_methods() -> None:
    from bnbagent_eval.metrics import registry
    from bnbagent_eval.metrics.base import Kind, MethodSpec, Outcome
    from bnbagent_eval.types import EvalCase, EvalRequest

    method = MethodSpec(
        "unittest_vendor",
        Kind.VENDOR,
        required_inputs=frozenset({"output", "expected"}),
        extra="vendorx",
        extra_module="definitely_missing_vendor_module",
    )
    registry.register_method(
        "text.exact_match", method, lambda metric, case, ctx: Outcome(score=0.25)
    )
    try:
        assert "unittest_vendor" in registry.get_spec("text.exact_match").method_names
        with pytest.raises(ValueError, match="already has"):
            registry.register_method("text.exact_match", method, lambda *a: 1.0)
        with pytest.raises(KeyError):
            registry.register_method("nope.metric", method, lambda *a: 1.0)
        from bnbagent_eval.exceptions import MissingExtraError

        suite = Suite(
            name="s",
            version="1",
            metrics=(MetricConfig(id="text.exact_match", method="unittest_vendor"),),
        )
        with pytest.raises(MissingExtraError, match="vendorx"):
            resolve_suite(suite)
        registry._EXTRA_METHODS["text.exact_match"]["unittest_vendor"] = (
            MethodSpec(
                "unittest_vendor", Kind.VENDOR, required_inputs=frozenset({"output", "expected"})
            ),
            lambda metric, case, ctx: Outcome(score=0.25),
        )
        from bnbagent_eval.engine import Runner

        resp = await Runner().run(
            EvalRequest(suite=suite, cases=(EvalCase(id="1", output="a", expected="a"),))
        )
        assert resp.results[0].metrics[0].score == 0.25
    finally:
        registry._EXTRA_METHODS.get("text.exact_match", {}).pop("unittest_vendor", None)


def test_register_method_params_and_prompt_hash() -> None:
    from bnbagent_eval.metrics import registry
    from bnbagent_eval.metrics.base import Kind, MethodSpec

    method = MethodSpec("unittest_extra", Kind.RULE, required_inputs=frozenset({"output"}))
    registry.register_method(
        "text.exact_match",
        method,
        lambda metric, case, ctx: 1.0,
        params={"unittest_steps": {"type": "integer", "minimum": 1}},
        prompt_hash=lambda params: "0x" + "ab" * 32,
    )
    try:
        spec = registry.get_spec("text.exact_match")
        assert "unittest_steps" in spec.params_schema["properties"]
        assert spec.validate_params({"unittest_steps": 0})  # schema enforced
        suite = Suite(
            name="s",
            version="1",
            metrics=(
                MetricConfig(
                    id="text.exact_match", method="unittest_extra", params={"unittest_steps": 2}
                ),
            ),
        )
        resolved = resolve_suite(suite)
        assert resolved.lock["text.exact_match"].prompt_hash == "0x" + "ab" * 32
    finally:
        registry._EXTRA_METHODS.get("text.exact_match", {}).pop("unittest_extra", None)
        registry._EXTRA_PARAMS.pop("text.exact_match", None)
        registry._PROMPT_HASHES.pop(("text.exact_match", "unittest_extra"), None)
