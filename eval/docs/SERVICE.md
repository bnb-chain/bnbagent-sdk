# Eval service

`bnbagent-eval serve` runs the evaluation engine as an HTTP service (the `[server]` extra). Other
agents send an `EvalRequest` and get the same `EvalResponse` a local run returns, signed by the
service. Requests can be free or paid per call with x402.

```bash
pip install 'bnbagent-eval[server,bnbagent]'      # [bnbagent] includes [evm]
bnbagent-eval serve --host 0.0.0.0 --port 8080 --base-url https://eval.example \
  --signer mypkg.keys:service_signer \
  --pay-to 0x... --chain-id 97 --facilitator-url https://facilitator.example \
  --price-base 10000 --price-per-judge-call 1000
```

| Option | Meaning |
|---|---|
| `--host`, `--port`, `--log-level` | uvicorn settings (default `127.0.0.1:8080`) |
| `--base-url` | public URL in the agent card |
| `--store` | sqlite path for jobs, idempotency records and credits |
| `--no-retention` | never store case contents (below) |
| `--allowed-kinds rule,judge,...` | method kinds callers may run (overrides `EVAL_SERVER_ALLOWED_KINDS`; any of `rule`, `judge`, `model`, `vendor`) |
| `--signer module:factory` | a `SignerPort` factory: responses get an EIP-712 attestation |
| `--pay-to`, `--chain-id`, `--facilitator-url` | enable x402 payments to this address, settled through the facilitator |
| `--asset` | accept only this token (default: every catalogue asset with a supported method) |
| `--assets FILE` | payment tokens as a YAML / JSON list (default: the SDK catalogue) |
| `--price-base`, `--price-per-judge-call` | price in the token's atomic units: base + judge calls x per-call fee |
| `--permit2-spender` | also accept Permit2 (`permit2-exact`) payments through this proxy |
| `--gate module:factory` | a custom `PaymentGate` instead of the built-in `X402Gate` |

Without `--pay-to` (or `--gate`) the service is free. The built-in gate takes its payment
assets from `--assets FILE` (a YAML / JSON token list, see
[STANDALONE.md](STANDALONE.md#5-a-paid-service-on-any-evm-chain)), else from the bnbagent SDK
catalogue (`[bnbagent]` extra). The service's own judge comes from
the `EVAL_JUDGE_*` variables ([JUDGES.md](JUDGES.md)).

## Endpoints

| Method and path | Purpose |
|---|---|
| `GET /health` | liveness and package version |
| `GET /v1/metrics`, `GET /v1/metrics/{id}` | the catalogue this service offers (allowed methods only) |
| `POST /v1/quote` | validate a request and return its price, without running it |
| `POST /v1/evaluate` | evaluate synchronously; returns the `EvalResponse` |
| `POST /v1/evaluations` | start an async job (202 with `job_id` and `access_token`) |
| `GET /v1/evaluations/{job_id}` | job status and result (`Authorization: Bearer <access_token>`) |
| `POST /v1/credits/redeem` | `{credit_id, request}`: run a request with a credit |
| `GET /.well-known/agent-card.json` | an A2A-style card: metrics, endpoints, whether x402 is on |

`bnbagent-eval schemas` writes the JSON Schemas and the OpenAPI document (`--dir`; by default
`schemas/` of a source checkout, else `./schemas`); `--check` fails when they are stale.
Errors have the body `{"error": <code>, "message": ...}`: 400 malformed JSON, 402 payment
required, 404, 409 idempotency conflict or used credit, 413 too large, 422 invalid or denied by
policy, 429 rate limited (per client IP), 503 too many running async jobs (64).

## Policy and limits

Requests come from strangers, so `UntrustedSuitePolicy` restricts what a suite may run:

- only built-in metrics (plugins are never loaded); `custom.python`, `custom.human_review`,
  `code.*` and `agentic` methods are always refused;
- by default only `rule` and `judge` methods: `vendor` and `model` methods download models or use
  the operator's cloud credentials, so the operator opts in per kind;
- no `$ref`, `suite_ref` or `benchmark_ref`: the suite is sent inline;
- targets only of kind `http`, `a2a` or `mcp`, and they and `dataset_ref` must be public
  http(s) URLs; every outgoing request and redirect is checked against private addresses;
- no simulated-user targets; a judge `base_url` only from an allow-list, judge models optionally
  from `allowed_judge_models`, bounded judge settings (`max_tokens` <= 4096, no retry or
  concurrency overrides) and `options.max_concurrency` <= 16; `format.regex` with the safe
  `regex` engine and a capped timeout;
- outgoing requests connect to the address that passed the public-IP check (no DNS rebinding)
  and response bodies are capped at 10 MB.

Limits (`PolicyLimits`, set by environment variables):

| Variable | Default | Limit |
|---|---|---|
| `EVAL_SERVER_MAX_REQUEST_BYTES` | 1 000 000 | request body size |
| `EVAL_SERVER_MAX_CASES` | 500 | cases per request |
| `EVAL_SERVER_MAX_RUNS` | 5 | `options.runs` |
| `EVAL_SERVER_MAX_JUDGE_CALLS` | 2 000 | estimated judge calls (sum over judge methods of cases x samples, where a metric's `params.samples` overrides the judge's) |

The pinned judge's `samples` and every metric's `params.samples` are capped at 5
(`max_judge_samples`), and JSON nesting at 64 levels. `bnbagent-eval lint
suite.yaml --use service` reports methods the service refuses before you send a request.

## Configuration

`ServiceConfig.from_env()` reads:

| Variable | Default | Meaning |
|---|---|---|
| `EVAL_SERVER_BASE_URL` | request URL | public URL for the agent card |
| `EVAL_SERVER_STORE` | `.eval-cache/service.db` | sqlite store |
| `EVAL_SERVER_NO_RETENTION` | off | `1`, `true` or `yes` to enable |
| `EVAL_SERVER_RATE_PER_S`, `EVAL_SERVER_RATE_BURST` | 5, 20 | token bucket per client IP |
| `EVAL_SERVER_ALLOWED_KINDS` | `rule,judge` | method kinds, e.g. `rule,judge,vendor,model` (unknown kinds are an error) |
| `EVAL_SERVER_JOB_TTL_S` | 86 400 | seconds an async job is kept |
| `EVAL_SERVER_IDEMPOTENCY_TTL_S` | 86 400 | seconds an idempotency record is kept |
| `EVAL_SERVER_CREDIT_TTL_S` | 2 592 000 (30 days) | seconds a credit stays redeemable |
| `EVAL_SERVER_EVAL_TIMEOUT_S` | 600 | seconds one evaluation may run |
| `EVAL_SERVER_MAX_*` | see above | policy limits |

The same fields can be set in code with `create_app(config=ServiceConfig(...))`.

To open vendor or model methods (they need their extras installed on the server):

```bash
EVAL_SERVER_ALLOWED_KINDS=rule,judge,vendor bnbagent-eval serve
bnbagent-eval serve --allowed-kinds rule,judge,vendor        # same
```

## Payment flow

1. The client sends `POST /v1/evaluate`. The service validates the request and prices it
   (`base_fee + estimated judge calls x per_call_fee`).
2. Without an `X-PAYMENT` header the answer is **402** with an x402 challenge: `accepts` (one
   entry per asset and method: `eip3009` `TransferWithAuthorization`, or `permit2-exact`) and a
   `quote` bound to the request hash, with an expiry.
3. The client signs and resends the same request with `X-PAYMENT`. The service checks the
   signature, amount, recipient, validity window and quote, reserves the nonce atomically (a
   replay fails, also across restarts), settles, evaluates with the caller's stage, attests and
   returns **200** with `X-PAYMENT-RESPONSE`.
4. If the evaluation fails on the server after settlement, the error body carries a **credit**
   (`credit_id`, amount, expiry) instead of losing the payment. `POST /v1/credits/redeem` with
   `{credit_id, request}` runs a request covered by the credit; a credit is used once and is
   restored if that evaluation fails too.

The SDK's `X402Payer` implements the client side: `EvalClient.remote(url, payer=...,
max_payment=...)` (see [ONCHAIN.md](ONCHAIN.md#s3-evaluation-as-a-service-with-x402)).

## Idempotency

Send an `Idempotency-Key` header (or the `idempotency_key` query parameter, for payers that
cannot set headers). A retry with the same key and the same request returns the stored response
with `Idempotent-Replayed: true` and is not charged again; the same key with another request is
a 409. Keys are scoped to the payer, so another payer cannot replay your response. The payment
header itself is also a key, so a resent payment never pays twice. For async
jobs a replay returns a new `access_token` and revokes the old one. `RemoteBackend` sends a fresh
key with every call.

## No retention

With `--no-retention` the store keeps only hashes, aggregates and verdicts: case contents,
per-case results and reasons never reach the database. Async results stay in memory until they
are read or expire, and server-side (5xx) error messages are reduced to the exception type.
Combine it with `options.privacy.report_contents` (`hashes` by default) to control what the response
itself carries ([REPORTING.md](REPORTING.md#privacy)).

## Try it in process

The app is a FastAPI application (`create_app`), so it can be tested without a network:

<!-- doctest: requires=fastapi,eth_account -->
```python
import asyncio

import httpx

from bnbagent_eval import EvalCase, EvalClient, MetricConfig, Suite
from bnbagent_eval.exceptions import PaymentRequired, PolicyError
from bnbagent_eval.ports import AssetSpec
from bnbagent_eval.server.app import create_app
from bnbagent_eval.server.config import ServiceConfig
from bnbagent_eval.server.payment import X402Gate
from bnbagent_eval.testing.fakes import FakeSettlement, StaticAssetCatalog

suite = Suite(name="svc", version="1", metrics=(MetricConfig(id="text.exact_match"),))
cases = [EvalCase(id="1", output="BNB", expected="BNB")]


def remote(app) -> EvalClient:
    http = httpx.AsyncClient(transport=httpx.ASGITransport(app=app))
    return EvalClient.remote("http://eval.test", client=http)


async def main() -> None:
    # A free service.
    free = create_app(config=ServiceConfig(store_path="free.db"))
    response = await remote(free).evaluate(suite=suite, cases=cases)
    print(response.verdict.status)  # pass (hashes and request binding verified)

    # The policy refuses code execution.
    unsafe = Suite(name="x", version="1", metrics=(MetricConfig(id="custom.python"),))
    try:
        await remote(free).evaluate(suite=unsafe, cases=cases)
    except PolicyError as exc:
        print("denied:", exc)

    # A paid service: without a payer the client gets the quote.
    token = AssetSpec(
        chain_id=97,
        address="0x" + "cd" * 20,  # synthetic token address
        symbol="USDX",
        decimals=18,
        methods=("eip3009",),
        eip712_domain={"name": "USD X", "version": "1"},
    )
    gate = X402Gate(
        pay_to="0x" + "ab" * 20,
        chain_id=97,
        assets=StaticAssetCatalog([token]),
        settlement=FakeSettlement(),
        store_path="x402.db",
    )
    paid = create_app(gate=gate, config=ServiceConfig(store_path="paid.db"))
    try:
        await remote(paid).evaluate(suite=suite, cases=cases)
    except PaymentRequired as exc:
        print(exc.quote["amount"], exc.challenge["accepts"][0]["scheme"])  # 10000 exact


asyncio.run(main())
```

A paying client passes `payer=` and `max_payment=` to `EvalClient.remote`; the payer handles the
402, signs, and retries.
