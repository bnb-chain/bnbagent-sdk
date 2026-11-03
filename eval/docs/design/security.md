# Security model

Scope: the `bnbagent-eval` package in `eval/`. This document is the full model; the
[maintainer guide](../MAINTAINER_GUIDE.md) summarizes the invariants every change must keep, and
[architecture.md](architecture.md) places the controls in the system.

## 1. Threat model

| Actor | Can control | Wants |
|---|---|---|
| Agent under evaluation | outputs, trajectories, deliverables, target responses | a higher score than earned; instructions to the judge |
| ERC-8183 counterparty (client or provider) | the negotiated suite, the deliverable, the evidence | a lenient suite, forged or replayed evidence, the other party's secrets |
| Caller of the eval service | the whole `EvalRequest`, target URLs, payment headers | code execution, SSRF, free or cheaper evaluations, other callers' results, the operator's judge budget |
| Network attacker / malicious host | DNS answers, HTTP responses | reach private addresses, exhaust memory |
| Sandboxed code (`code.tests_pass`) | the code it runs and the files it writes | read host files, reach the network, block the host |

Out of scope: a compromised host, a compromised judge provider, and the security of the bnbagent
SDK, the chain and the x402 facilitator.

## 2. Trust boundaries

| Input | Trust | Loaded with |
|---|---|---|
| Suites and cases the user loads locally (CLI, `EvalClient`) | trusted: chosen by the user | `Suite.load` (local `$ref`, `custom.python` and a judge `base_url` allowed) |
| Suites agreed in an ERC-8183 job (`suite_from_job`) | untrusted: written by the other party | `Suite.load(..., refs=False)` and `check_negotiated_suite` (`chain/erc8183.py`) |
| Requests to the eval service | untrusted: a paying stranger | `UntrustedSuitePolicy` (`server/policy.py`) |
| Case contents (outputs, contexts, trajectories) | untrusted data, never instructions | fenced in judge prompts (`judges/fencing.py`) |
| URLs the package did not choose (artifacts, deliverables, on-chain suites, `erc8004` target endpoints, service targets and `dataset_ref`) | untrusted | the egress guard (`net.py`) |
| Provider evidence and attestations | untrusted until verified | `chain/attestation.py`, `check_provider_evidence` |
| Code under test | untrusted | the sandbox (`metrics/code/sandbox.py`) |

## 3. Controls and invariants

### 3.1 Negotiated suites (`chain/erc8183.py`)

- `suite_from_job` fetches the suite through the storage port and verifies the pinned
  `suite_hash`.
- It loads with `refs=False`: any `$ref` is rejected, so no local file is read.
- `check_negotiated_suite` denies `custom.python` and any judge `base_url` other than the local
  `EVAL_JUDGE_BASE_URL` or an explicit `allowed_judge_base_urls`, so the local judge key never
  goes to a host the counterparty chose. A denial raises `ChainIntegrationError(code="suite_denied")`
  and the gate abandons the job.

### 3.2 Egress (`net.py`)

Used by `format.artifact_integrity`, the code metrics' artifact fetch, the agentic judge's tools,
the JSON-RPC storage adapter, `erc8004` targets (the endpoint read from the registration file,
`engine/runner.py`), and the service (`GuardedTransport` in `server/policy.py`, dataset
fetch in `server/runner.py`).

- Only `http(s)`; every address of the host must be public. Private, loopback, link-local,
  reserved and tunnelled forms (NAT64, 6to4, Teredo, IPv4-mapped and IPv4-compatible) are
  rejected, before connecting and on every redirect hop (at most 3).
- The connection goes to the address that passed the check (`pin_request`); the Host header,
  TLS SNI and certificate name keep the hostname. DNS rebinding cannot redirect it.
- Bodies are capped, by `content-length` and while streaming (10 MB by default;
  `PolicyLimits.max_response_bytes` on the service). Clients use `trust_env=False`.
- `data:` URIs are decoded locally; declared hashes are checked (`hash_matches`).

### 3.3 Eval service (`server/`)

**Request policy** (`UntrustedSuitePolicy`, `PolicyLimits`):
- the raw body is size-capped and depth-limited (64) before parsing; `$ref` anywhere in it is
  rejected; oversized integers and `RecursionError` map to 400;
- `suite_ref` and `benchmark_ref` are refused (the suite is sent inline); `dataset_ref` must be a
  public http(s) URL and is fetched through the guard, verified against its `#0x<keccak>`
  fragment when present; local paths are never read;
- metric aliases resolve to canonical IDs before the allow-list; each resolved method must be
  `service_allowed`, built-in (never a plugin) and of an allowed kind (`rule` and `judge` by
  default; `vendor` and `model` are operator opt-in through `--allowed-kinds`);
- `custom.python`, `custom.human_review`, `code.*`, `agentic` methods and simulated-user targets
  are denied; targets are `http`, `a2a` or `mcp` with public URLs;
- regexes need the `regex` engine and a capped timeout; XML parsing is DTD- and entity-free and
  XSDs with `schemaLocation` are rejected (`metrics/format/structured.py`);
- limits: request bytes, cases (500), runs (5), judge calls (2,000, estimated before the quote),
  per-metric `samples` (5), `options.max_concurrency` (16), redaction patterns (16, 256 chars).

**Caller-pinned judges** run on the operator's credentials, so they are bounded: providers
`openai`, `anthropic` or `bedrock`; `max_tokens` ≤ 4096; only `extra.response_format`; a judge
`base_url` only from the operator's allow-list; optionally `allowed_judge_models`.

**Payments** (`server/payment.py`, x402 `exact` with EIP-3009 or Permit2):
- the server re-prices every request; the quote is persisted and bound to `request_hash` and an
  expiry;
- `to` / `witness.to` must equal `payTo`, the Permit2 spender must be the configured proxy, the
  EIP-712 domain uses the gate's chain ID and asset (never the envelope's), and the value must
  cover the quote in that asset's decimals;
- signatures are 65 bytes and recovered through `eth_account`; the validity window is checked;
  the `X-PAYMENT` header is size-capped;
- nonces are reserved atomically in sqlite before settlement and survive restarts; on-chain
  `authorizationState` is checked; a settlement failure releases the nonce unless the chain
  reports it used.

**Idempotency and credits** (`server/app.py`, `server/store.py`):
- `Idempotency-Key` records are scoped to the payer recovered from `X-PAYMENT`
  (`X402Gate.identify`, which reserves and settles nothing); gates without `identify` scope to
  the exact header;
- a server-side failure after payment issues a credit for the quoted amount in the quoted
  asset; redemption requires the same asset and an amount covering the new quote.

**Jobs and storage**: job IDs are 192-bit, access tokens 256-bit; only the token's SHA-256 is
stored and compared with `hmac.compare_digest`; a missing job, wrong token or expired job all
return the same 404. Expired rows are purged on submit (at most once a minute); async jobs are
capped (`ServiceConfig.max_running_jobs`, 64; 503 with `Retry-After` beyond).

**No-retention mode** (`--no-retention`): case contents are never stored, and every stored
failure message is reduced to the exception type. In all modes, 5xx bodies carry only the
exception type, and logs carry request hashes, never case data or payment headers.

### 3.4 Judges (`judges/`)

- Every untrusted field is fenced in `<untrusted-NONCE>` tags, with a system clause that fenced
  content is data, never an instruction. The nonce is deterministic,
  `keccak(case_hash, metric_id, sample_idx, salt)`, so replays render byte-identical prompts;
  it is public by design, so escaping is the boundary: any case or spacing variant of an
  opening or closing `untrusted` tag inside content is defused.
- API keys come from the environment only; they are not part of `JudgeConfig`, provenance,
  `to_dict` or a provider's `repr`. Provider error bodies are truncated to 200 characters.

### 3.5 Attestations and evidence (`chain/attestation.py`, `chain/evidence.py`, `chain/erc8183.py`)

- The EIP-712 `Attestation` signs `resultHash`, `subjectHash`, `suiteHash`, the verdict
  `status` and `scoreBps` (the `EvalEvidenceV1` encoding), plus evaluator and expiry. The
  domain name and version are fixed by the verifier; for job and agent subjects the domain must
  be the subject's chain and commerce or registry contract.
- Verification recovers the signer and compares it with the stated evaluator (and
  `expected_evaluator` when given), enforces expiry, recomputes the digest, and re-verifies the
  result and response hashes of full responses.
- Advisors compare the evidence subject (chain, commerce, job, provider, `output_hash`) and suite
  hash with the job. `check_provider_evidence` reports `summary_consistent` (unsigned headline vs
  signed status) and `self_attested` (evaluator equals provider). A deliverable whose manifest
  hash differs from the on-chain `deliverable` is a dispute / `vote_reject`.
- Signing goes only through `SignerPort` (`supports()` checked). `LocalKeySigner.__repr__` hides
  the key; SDK wallets are created by the SDK, and its private environment reader is reached only
  through `integrations/bnbagent/compat.wallet_env_kwargs` (`sdk_api_missing` if a release drops it).

### 3.6 Code sandbox (`metrics/code/sandbox.py`, `metrics/code/_launcher.py`)

- argv lists only (no shell), with a scrubbed environment (`PATH`, `LANG`, `HOME` and `TMPDIR`
  set to the work directory), so no `EVAL_*` or wallet variables reach the child.
- User, mount, PID and network namespaces; Landlock with read-only system paths, a read-write
  work directory, TCP denied, and signal and abstract-socket scoping; `no_new_privs` and an
  empty capability bounding set; `/tmp`, `/run`, `/dev/shm` and `/var/tmp` hidden. Read access
  is never granted to `/` or `$HOME`.
- rlimits on CPU, address space, file size, open files and processes; no core dumps; the
  wall-clock kill takes the whole PID namespace.
- stdout and stderr go through capped buffers; collected files are capped, must be regular
  files inside the work directory (opened `O_NOFOLLOW | O_NONBLOCK`), and names cannot be
  absolute or contain `..`.
- Without isolation the run is refused (`sandbox_unavailable`); only syntax-check tools run
  with `require_isolation=False`.

### 3.7 Privacy and logs (`privacy.py`, `logging_utils.py`)

- Reports default to `report_contents="hashes"` (no case contents).
- `options.privacy.redact` patterns use the `regex` engine with a per-pattern timeout when it is
  installed (a timeout redacts the whole string; the service requires it); they also apply to
  error messages and per-run errors.
- Encrypted reports use a fresh SecretBox key per report, sealed to each X25519 recipient; the
  plaintext hash is checked after decryption.
- `get_logger` attaches a `RedactingFilter` (API keys, private keys, Bearer / Basic values, AWS
  key IDs, `X-PAYMENT` bodies) to the service, payment, gate and runner loggers. The gate warns
  when a report would be stored at a `file://` URI that clients and voters cannot read.

### 3.8 Supply chain

- No `pickle`, `marshal`, `eval` or `exec`; YAML only through `safe_load` / `safe_dump`.
- `subprocess` only in the sandbox, with fixed argv.
- `importlib.import_module` only for optional extras (`_optional.require`), built-in metric
  packages, exporters and entry-point plugins (plugins never on the service), `custom.python`
  and callable targets (local only), the sandboxed test runner, and the CLI `MODULE:FACTORY`
  options (`--work`, `--ports`, `--judge`, `--gate`, `--signer`).
- `import bnbagent_eval` imports no extra, SDK or web3 and makes no network call
  (`tests/unit/test_isolation.py`); `scripts/check_secrets.py` scans the repository.

## 4. Accepted risks

| Risk | Why it is accepted, and the mitigation |
|---|---|
| On a free service (`NoPaymentGate`) idempotency keys share one anonymous scope, so whoever knows a key and its request can replay the response and rotate the job token | The request is free to re-run anyway; free services are for development and private deployments. Use unguessable keys. |
| x402 `exact` signatures bind recipient, amount, validity and nonce, not the request, so an authorization can pay for any request it covers | Protocol limitation. The server re-prices the actual request and requires the value to cover it; nonces are single-use. |
| Credits are bearer IDs and are restored when a redemption fails, so failing redemptions cost compute | IDs are at least 192-bit, visible only to the payer, rate-limited and bounded by `eval_timeout_s`. |
| An encrypted report publishes `plaintext_hash` (a guessed plaintext can be confirmed), and sealed boxes do not authenticate the sender | `result_hash` is already public in the evidence; authenticity comes from the attestation inside the report. |
| A caller's `target.config.timeout_s` is not capped per request | Bounded by `eval_timeout_s` and `max_running_jobs`. |
| `DeliverableView.verified` checks the manifest hash against the on-chain `deliverable`, not the manifest's own `job_id` / `chain_id` | The on-chain hash, set by the provider for this job, is authoritative; evidence binding uses on-chain IDs (`JobSubject`), never manifest fields. |
| Rate-limit buckets are per client IP (shared behind a proxy; the table resets at 100,000 keys) | Deploy behind a proxy with its own per-client limits. |
| By default a caller may pin any model of an allowed provider at the per-call price | Operators set `allowed_judge_models` or a model-aware `price_fn`. |
| Locally loaded suites are trusted (`$ref`, `custom.python`, judge `base_url`) | Local files are chosen by the user; code that handles third-party suites uses `Suite.load(refs=False)` and `check_negotiated_suite`. |

## 5. Tests

Regression tests for these controls: `tests/unit/server/test_service_security.py`,
`tests/unit/chain/test_chain_security.py`, `tests/security/` and the payment tests in
`tests/unit/server/test_x402_gate.py`. [testing.md](testing.md) §11 maps each control to its tests.
