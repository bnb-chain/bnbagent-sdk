# Quickstart example

The demo of the [user guide](../../docs/USER_GUIDE.md#5-five-minute-demo-command-line):

```bash
bnbagent-eval run --suite suite.yaml --cases cases.jsonl --format console
```

The `approve` case fails the required `text.contains` metric, so the verdict is `FAIL` (exit 1).
