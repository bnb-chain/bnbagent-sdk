# SRC-1314 guard-class sweep

Self-audit prompted by bug bounty report SRC-1314. That report was fixed in PR #64
(TypeScript) and #65 (Python); this document covers the sweep for **other instances of the
same class**, since the root cause was a pattern rather than a single mistake.

**Date of sweep:** 2026-07-30 · **Base:** `feat-tssdk` @ `f2a3ade` · **Both SDKs in scope.**

## The class

> A guard checks one side of a bound on a value it does not own, and the value's real
> domain is enforced only incidentally by something downstream.

SRC-1314's shape: `SessionBudgetTracker.reserve()` tested `cur + amt > cap` and nothing
else. A negative `amt` shrank the sum, so the test passed and the counter went negative,
after which the cap never bound again. The only thing that had been keeping `amt`
non-negative was `eth_abi` refusing to encode a negative into the `uint256` the canonical
EIP-3009 schema declares — and the schema arrives in an attacker-controlled `types` dict,
so the attacker could simply declare `int256` instead.

Five sub-patterns were swept. Each finding is labelled with the one it belongs to.

- **A** one-sided numeric bound on an externally-influenced value
- **B** caller-supplied schema trusted to decide encoding or validation
- **C** invariant installed on the defensive path instead of the load-bearing one
- **D** Python ↔ TypeScript guard drift
- **E** guard that silently becomes a no-op when its config is omitted

## Findings

### F-1 (class A, fixed) — quoted amount reaches the budget counter through a one-sided precheck

| | |
|---|---|
| Sites | `python/bnbagent/x402/twak.py:135` · `typescript/src/wallets/twak/x402.ts:149` · `typescript/src/wallets/altana/x402.ts:288` |
| Input | The `amount` of a quoted x402 option, parsed from an untrusted 402 challenge (`python/bnbagent/x402/payer.py:61`, `int(entry["amount"])`) |
| Sink | `SessionBudgetTracker.reserve(option.asset, option.amount)` — the same counter SRC-1314 poisoned |

**Worse than the reported path.** These three have no ABI encoder downstream. The reported
attack needed the `int256` substitution to make the corruption persist — otherwise
`eth_abi` raised, `rollback()` ran, and the counter returned to exactly zero. Here the
provider or CLI call simply succeeds, nothing rolls back, and a negative quoted amount is
**single-defect exploitable**.

`reserve()`'s new non-negative guard (PR #64/#65) already blocks the fund impact, because
that guard sits inside the tracker. Two reasons to still fix the prechecks:

1. The surface that owns the amount should reject it with `X402AmountExceededError`, not
   let it surface as a budget error from a layer below.
2. `session_budget` is optional. With no tracker configured there is nothing else in the
   path that looks at the amount at all — it flows into `X402PaymentResult.amount` and any
   accounting the caller does on top of it.

**Fix:** explicit `< 0` rejection at each of the three prechecks.
**Tests:** `test_negative_quoted_amount_rejected`,
`test_negative_quoted_amount_rejected_without_a_session_budget` (Python);
`"rejects a negative quoted amount before signing"` (TypeScript / altana).

### F-2 (class A, fixed) — `maxTimeoutSeconds` bounded on one side only

| | |
|---|---|
| Sites | `python/bnbagent/x402/twak.py:143` · `typescript/src/wallets/twak/x402.ts:157` |

A negative claimed payment window passed the cap test. No fund impact was identified — the
value is only bounded here, never applied — so this is hygiene, not exposure. Fixed anyway
because it is the same shape sitting two lines from F-1, and leaving it invites the reader
to conclude the shape is acceptable.

**Test:** `test_negative_quoted_timeout_rejected`.

### F-3 (class A, recorded — no code change) — `to_raw` / `toRaw` are exported negative-amount factories

| | |
|---|---|
| Sites | `python/bnbagent/utils/amounts.py:14` · `typescript/src/utils/amounts.ts:28` |

Both accept and deliberately preserve a leading minus (`return sign === "-" ? -magnitude
: magnitude`; `Decimal("-1e30")` is valid). Both are exported from the public `utils`
barrel and have **zero internal callers** — they exist for SDK consumers converting decimal
strings at the boundary.

A consumer doing `to_raw(remote_amount_string, decimals)` and passing the result to
`X402Signer` is the exact input SRC-1314 needed. The payment guards now reject it, so this
is a hazard on the public surface rather than a live path.

**Not changed**, because rejecting negatives would break legitimate uses (rendering
balance deltas) and the guards downstream now hold. Recorded so the next reader does not
have to re-derive it. If a `to_raw_amount()` non-negative variant is ever wanted, this is
the justification.

### F-4 (class E, recorded — design decision needed) — guards that vanish when unconfigured

Three cases where a security control silently becomes a no-op:

| Site | Unconfigured behaviour |
|---|---|
| `X402Signer(wallet)` with no `max_value_per_call` | No per-call cap at all — `cap = self._max_value.get(verifying)` returns `None` and the check is skipped |
| `SessionBudgetTracker(None)` | No cap, so `reserve()` never refuses on amount grounds |
| `TWAKX402Payer(provider)` with defaults | `expected_pay_to=None` and `expected_asset=None`, so **points 1 and 2 of the advertised "five-point precheck" do not run** |

This is the same failure mode as SRC-1314 one level up: something documented as a defence
is not actually enforcing. `X402Signer` is the sharpest case — the class name and its
docstring ("pass the resulting signer to agent tool functions instead of the raw wallet")
both imply protection that an argument-less constructor does not provide.

**Not changed in this PR.** Changing the defaults to fail-closed is a breaking change and
needs a product call, not an audit call. Current behaviour is now pinned by
`test_signer_with_no_caps_enforces_nothing_numeric` (Python) and
`"enforces nothing numeric when constructed with no caps"` (TypeScript) so that any change
to it becomes visible in a diff. **Recommended follow-up:** either warn at construction
time when a payment-guard object is built with no caps, or state the degradation in the
docstrings.

### F-5 (class D, recorded) — the TypeScript twak x402 payer has no tests

Python has `python/tests/test_twak_x402_payer.py` (20 tests, fixture-driven from real
`twak x402 quote --json` captures). TypeScript has no test file for
`src/wallets/twak/x402.ts` at all. F-1's fix on that file is covered only indirectly, via
the property tests and the Python mirror.

Not addressed here because porting the fixture harness is its own piece of work.

## Surfaces checked and found sound

Recorded so the next sweep does not repeat them.

| Surface | Why it holds |
|---|---|
| `signing/checks.py` / `checks.ts` validity chain | `valid_before <= valid_after` raises first, so `window > 0` is guaranteed before `window > max`; `valid_before <= current` raises first, so `future > 0` is guaranteed. Both one-sided tests are backed by a prior check. |
| `erc8183/negotiation.py:556` | `quote_ttl_seconds <= 0 or > MAX` — correct two-sided form; use as the reference example. |
| `erc8183/client.py:309` / `client.ts:455` | `approve_floor < 0` already rejected. |
| `utils/rate_limit.py` / `rateLimit.ts` | `<= 0` on all three config knobs. |
| `core/contract_mixin.py` / `core/txConfig.ts` | `wei <= 0`, `seconds <= 0` rejected. |
| `erc8183/job_ops.py:261,276` / `jobOps.ts:433,460` | Size caps compare `len(...)` output — non-negative by construction. |
| `erc8183/job_ops.py:519` / `jobOps.ts:871` | `budget < service_price` — a negative budget is caught by the comparison's direction. |
| `wallets/altana/provider.ts:379,385` | Expiry bounded on both sides (`>= MAX` and `<= now`). |
| `erc8183/job_ops.py:_read_int_env` | `<= 0` rejected, falls back to the default. |
| **Class B sweep** | The `types` dict (fixed in SRC-1314) was the only instance of externally-supplied schema deciding an encoding. `decimals` is read from the chain via `token_decimals`, not from remote input. No other instance found. |
| **Class C sweep** | `reserve`/`rollback` was the only signed accumulator with the invariant on the wrong side. Two other paired operations were examined. `utils/rate_limit.py` `check()` is not a signed counter at all — it is a deque of timestamps, its "decrement" is `popleft()` guarded by `while bucket and ...`, and `len(bucket) >= max` is non-negative by construction, so the pattern is structurally inapplicable. `erc8183/client.py` `fund()`'s allowance top-up leans on the ABI encoder to reject a negative `amount` (a negative makes `current < amount` false, so no approve is issued and the negative reaches `commerce.fund`, where encoding into `uint256` fails) — the same incidental enforcement SRC-1314 depended on, but **no state is mutated before the encoder gets a say**, so the call fails cleanly with nothing to poison. That difference is the whole reason SRC-1314 was severe and this is not. |
| **Class D sweep** | No case of "guard present in one SDK, missing in the other" beyond F-5's test gap. `altana` is TypeScript-only and has no Python counterpart to compare against, so it was swept independently against A/B/C/E. `to_raw`/`toRaw` behave consistently including on negatives. |

## Property tests

`python/tests/test_guard_properties.py` and `typescript/tests/guardProperties.test.ts`.
These are the durable part of this work: they state the invariants rather than specific
inputs, so a guard added later is covered without editing them.

1. A rejected call must not change any counter.
2. An accepted amount must land inside `[0, cap]`.

The signer invariant is asserted **against the wallet** — "everything that reached
`sign_typed_data` satisfied every advertised cap" — rather than against any individual
guard, so it keeps holding when guards are added and fails if any guard becomes
bypassable.

**The harness was validated against the vulnerable baseline before being trusted.** Run on
`feat-tssdk` @ `f2a3ade` it rediscovers SRC-1314 from scratch, with `-1` as the minimal
counterexample in both languages: 5 of 7 Python properties and 4 of 6 TypeScript
properties fail. All pass with the fix applied. A property suite that cannot find the bug
it was written for is decoration, so this check should be repeated whenever the invariants
are edited.

New dev dependencies: `hypothesis>=6.100.0` (Python), `fast-check` (TypeScript).

## Follow-ups (deliberately not in this PR)

1. **F-4 decision** — fail-closed defaults, constructor warning, or documented degradation.
2. **F-5** — port the twak x402 fixture tests to TypeScript.
3. **Dependency and taint scanning.** The repo currently has **no security tooling at all**:
   no `pip-audit`, no `npm audit`, no CodeQL, no `bandit`, no `semgrep`; `ruff` runs with
   `E,F,W,I,UP,B`. These address a different bug class (dependency CVEs, generic taint) and
   belong in their own PR, but the gap is total and worth closing.
4. **A semgrep rule for class A** was considered and dropped. Only two live instances were
   found (F-1, F-2), both now fixed and covered by property tests; a new tool dependency to
   catch a third hypothetical instance is not yet worth its weight.
