# Reports

An `EvalResponse` is plain data: every report below is a view of it, and none changes it. The
renderers live in `bnbagent_eval.reporting`; telemetry exporters in
`bnbagent_eval.reporting.exporters`.

| Format | Function | CLI |
|---|---|---|
| JSON (the full response) | `to_json(response, indent=2)` | `bnbagent-eval run --format json` (default) |
| Markdown | `to_markdown(response)` | `--format md` |
| Console table | `to_console(response)` | `--format console` |
| CSV | `to_csv(response, per="metric" \| "case" \| "summary")` | `--csv PATH --csv-per metric` |
| OpenTelemetry, Prometheus, MLflow | `exporters.export(response, backend, ...)` | `--export otel` (repeatable) |

`--out FILE` writes the main report to a file instead of stdout.

## JSON

`to_json` is `EvalResponse.to_dict()` as JSON; `EvalResponse.from_dict` reads it back. It is the
format the eval service returns and `bnbagent-eval publish` reads; the hashes are computed over
its canonical form (keccak). `bnbagent-eval schemas` writes its JSON Schema.

<!-- doctest -->
```python
import json

from bnbagent_eval import EvalCase, EvalClient, EvalResponse, MetricConfig, Suite
from bnbagent_eval.engine.seal import verify_hashes
from bnbagent_eval.reporting import to_console, to_json, to_markdown

suite = Suite(
    name="report-demo",
    version="1",
    metrics=(MetricConfig(id="text.exact_match"), MetricConfig(id="text.token_f1")),
)
cases = [
    EvalCase(id="a", output="BNB", expected="BNB"),
    EvalCase(id="b", output="BNB Chain token", expected="BNB"),
]
response = EvalClient().evaluate_sync(suite=suite, cases=cases)

text = to_json(response)
again = EvalResponse.from_dict(json.loads(text))
assert again.result_hash == response.result_hash and verify_hashes(again)

print(to_markdown(response).splitlines()[0])  # # Evaluation report
print(to_console(response))
```

`verify_hashes` recomputes `result_hash` and `response_hash`, so anyone holding the JSON can
check that it was not edited.

## Markdown and console

`to_markdown` writes the verdict, action and reasons, the aggregate (score, pass rate, error
rate, cases), the hashes, and a table per metric (mean, pass rate, scored / errors / skipped,
value p50 / p95 with unit). `to_console` prints the same as fixed-width text tables for a
terminal. Neither needs an extra.

## CSV

`to_csv` uses only the standard library. Three shapes:

- `per="metric"`: one row per case and metric: `case_id, metric_id, name, method, status,
  score, value, unit, passed, threshold, required, weight, error_code, metric_version,
  judge_source` (and `reason` with `include_text=True`);
- `per="case"`: one row per case: score, passed, metric counts and error codes;
- `per="summary"` (or `to_summary_csv`): one row per metric: count, scored, errors, skipped,
  mean, std, min, max, pass rate and value percentiles.

Text cells that a spreadsheet could read as a formula (starting with `=`, `+`, `-`, `@`, tab or
carriage return) get a leading `'`. Free-text reasons are only written with `include_text=True`.

<!-- doctest -->
```python
import csv
import io

from bnbagent_eval import EvalCase, EvalClient, MetricConfig, Suite
from bnbagent_eval.reporting import to_csv

suite = Suite(name="csv-demo", version="1", metrics=(MetricConfig(id="text.exact_match"),))
response = EvalClient().evaluate_sync(
    suite=suite, cases=[EvalCase(id="a", output="1", expected="1")]
)
rows = list(csv.DictReader(io.StringIO(to_csv(response))))
print(rows[0]["case_id"], rows[0]["metric_id"], rows[0]["score"])  # a text.exact_match 1.0
to_csv(response, path="summary.csv", per="summary")  # writes the file and returns the text
```

## Telemetry exporters

With the `[exporters]` extra, `export(response, backend, **options)` sends a response to:

| Backend | What it records | Options |
|---|---|---|
| `otel` | a span tree `eval.run` -> `eval.case` -> `eval.metric` with `eval.*` attributes | `tracer_provider`, `tracer_name`, `metric_spans`, `include_text` |
| `prometheus` | run gauges (score, pass rate, error rate, cases, verdict) and per-metric gauges and counters, labelled by stage, suite hash and metric ID | `registry`, `prefix` (`bnbagent_eval`), `pushgateway`, `job`, `grouping_key` |
| `mlflow` | parameters, metrics and the JSON report as an artifact | `run_id`, `experiment`, `run_name`, `tracking_uri`, `artifact` |

```python
from prometheus_client import CollectorRegistry

from bnbagent_eval.reporting.exporters import export

registry = export(response, "prometheus", registry=CollectorRegistry())
span = export(response, "otel")                      # the global tracer provider
run_id = export(response, "mlflow", experiment="agent-evals")
```

No exporter sends case text (metric reasons, content details) unless `include_text=True`.
Prometheus uses a fresh registry unless you pass one; counters accumulate when you export to the
same registry again.

Prometheus labels per-metric series by `metric_id`, not by the config `name`. A suite that uses
one metric twice (e.g. two methods, each with its own `name`) writes both into the same series:
the gauges keep the last config's values, the counters add up, and the pass-rate threshold is
the first config's. Export such suites with `otel` or `mlflow`, or read `response.aggregate`
directly, where each `name` has its own entry.

## Privacy

`RunOptions.privacy` controls what the response carries. The response never contains the cases'
inputs or outputs, only hashes of the outputs; the privacy settings apply to what metrics report
about them.

| `report_contents` | Detail keys ending in `_text` (or named `excerpt`) |
|---|---|
| `hashes` (default) | removed |
| `redacted` | replaced by `[redacted]` |
| `full` | kept |

`privacy.redact` is a list of regular expressions; matches in metric reasons and details are
replaced by `[redacted]` in every mode. `bnbagent-eval run --report-contents` sets the mode.
Metric `reason` strings are kept in every mode: a judge's reasoning can quote the case, so add
`redact` patterns, or drop reasons before you share a report.

<!-- doctest -->
```python
from bnbagent_eval import (
    EvalCase,
    EvalClient,
    MetricConfig,
    Privacy,
    RunOptions,
    Suite,
    register_python_metric,
)


def has_address(case: EvalCase) -> dict:
    found = "0x" in str(case.output)
    return {
        "score": 1.0 if found else 0.0,
        "reason": f"address present in output {case.output}",
        "details": {"answer_text": case.output},
    }


register_python_metric("has_address", has_address)
suite = Suite(
    name="privacy",
    version="1",
    metrics=(MetricConfig(id="custom.python", params={"fn": "has_address"}, threshold=0.5),),
)
case = EvalCase(id="1", output="Sent to 0x" + "ab" * 20)  # a synthetic address
for mode in ("hashes", "redacted", "full"):
    options = RunOptions(privacy=Privacy(report_contents=mode, redact=(r"0x[0-9a-fA-F]{40}",)))
    response = EvalClient().evaluate_sync(suite=suite, cases=[case], options=options)
    m = response.results[0].metrics[0]
    print(mode, dict(m.details), m.reason)
# hashes {} address present in output Sent to [redacted]
# redacted {'answer_text': '[redacted]'} address present in output Sent to [redacted]
# full {'answer_text': 'Sent to [redacted]'} address present in output Sent to [redacted]
```

Reports uploaded to public storage by `MetricsPublisher` and `EvalGate` can be encrypted to
X25519 public keys (their `encrypt_to=` argument, `[crypto]` extra). `Privacy.encrypt_to` in
`RunOptions` is reserved: it is part of the request (and its hash), but neither `EvalClient` nor
the eval service encrypts responses with it. To encrypt, pass `encrypt_to=` to the publisher or
the gate, or call `encrypt_report(response.to_dict(), keys)` yourself.
`bnbagent_eval.privacy.generate_keypair()` makes a key pair, and `encrypt_report` /
`decrypt_report` handle the envelope. Metric authors:
put case content in detail keys ending in `_text`, so the privacy modes can find it.
