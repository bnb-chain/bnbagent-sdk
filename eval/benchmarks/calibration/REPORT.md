# Judge calibration report (2026-10-30)

How well do LLM judges agree with human labels on the built-in judge metrics?
Reproduce with:

```bash
EVAL_JUDGE_PROVIDER=bedrock EVAL_JUDGE_MODEL=global.anthropic.claude-haiku-5-5 AWS_REGION=us-east-1 \
  bnbagent-eval calibrate --suite eval/benchmarks/calibration/suite.yaml \
  --cases eval/benchmarks/calibration/cases.jsonl --min-kappa 0.6 --json
```

## Dataset

`cases.jsonl` has 16 synthetic, clear-cut cases about BNB Smart Chain, ERC-20 and ERC-8004, with
pass / fail labels per metric in `metadata.labels` (one annotator):

| Group | Cases | Labels |
|---|---|---|
| Correct, grounded answers | f1–f4 | faithfulness, relevance, helpfulness: pass |
| On-topic answers that contradict the context | u1–u4 | faithfulness: fail; relevance: pass; helpfulness: fail (u3, u4) |
| Off-topic answers | r1–r4 | relevance, helpfulness: fail |
| Evasive vs complete how-to answers | h1–h4 | helpfulness: fail (h1, h2) / pass (h3, h4); relevance: pass (h3, h4) |

## Results

Bedrock Converse, temperature 0, one sample. The gate statistic is Cohen's kappa on pass / fail
decisions when the labels are pass / fail and the judge scores on tiers (`gate_kappa`; a "Good"
score for a "pass" label is not a disagreement); otherwise plain Cohen's kappa.

| Metric | n | Haiku 5.5 gate κ | Haiku decision agreement | Sonnet 5.5 gate κ | Sonnet decision agreement |
|---|---|---|---|---|---|
| `rag.faithfulness` | 8 | 1.00 | 1.00 | 1.00 | 1.00 |
| `quality.relevance` | 14 | **0.46** | 0.71 | 0.70 | 0.86 |
| `quality.helpfulness` | 14 | 1.00 | 1.00 | 1.00 | 1.00 |
| Judge calls / input / output tokens | | 44 / 91.7k / 11.7k | | 44 / 86.1k / 11.4k | |

At `--min-kappa 0.6`, Sonnet 5.5 passes every metric; Haiku 5.5 passes faithfulness and
helpfulness but not relevance. Raw results: `results/2026-10-30-*.json`.

## Findings

1. **Relevance vs correctness.** Both judges marked some on-topic but factually wrong answers
   (u2, u4) as irrelevant; Haiku did so more often. The relevance prompt does not separate
   "addresses the question" from "answers it correctly". For suites that need the distinction,
   pair `quality.relevance` with `rag.faithfulness` / `quality.answer_correctness`, or prefer a
   stronger judge for relevance. A prompt revision is a candidate follow-up (it changes
   `prompt_hash`, so it needs a new prompt version).
2. **Forced tool use.** Sonnet 5.5 on Bedrock rejects a forced `toolChoice`. The Bedrock and
   Anthropic providers retry once with `auto` tool choice and remember it
   (`provenance.judge.tool_choice = "auto"`).
3. **Judge errors are reported, not scored.** Unscored cases are excluded from κ and reported as
   `judge_errors` with the first error message, so a broken judge cannot pass for κ = −1.
4. **Tiered scores vs binary labels.** When labels are binary, a tiered score (for example
   "Good", 0.75) is compared at decision level: the kappa is computed on pass / fail.
5. **Cost.** `calibrate` meters the judge's real token usage; USD needs `--prices` (there is no
   built-in price table).

## Limits

16 cases and a single annotator: this checks that judges and the calibration tooling work, not
judge quality in general. Calibrate on labelled data from your own domain before relying on a
judge for disputes (`bnbagent-eval calibrate --inject` adds clean / faulty pairs).
