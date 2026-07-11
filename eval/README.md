# BNB Agent Studio --- Evaluation Suite

Reproduces the evaluation in *BNB Agent Studio: An Open Toolchain for
Provisioning, Deploying, and Operating Economically Autonomous LLM Agents*
(EMNLP 2026 System Demonstrations). Pinned to `bnbagent-studio==0.0.2`,
`bnbagent==0.3.6`.

All model calls go through **AWS Bedrock** (boto3 Converse API) --- auth is your
AWS credentials, no API key. Set `AWS_REGION=us-east-1`. Runs use temperature 0
(Opus 4.8 omits `temperature`, which it deprecates); the confused-deputy table is
the mean of 3 trials per cell.

## Layout

| File | Paper ref | What it is |
|------|-----------|------------|
| `run_eval.py`, `run_eval_converse.py` | Sec. 5.1, 5.3 | Main harness: six configs (a--f) x models; prompts + tool schemas defined inline (`SYSTEM_*`, `READONLY_TOOLS`, `MONEY_TOOLS`). |
| `data/confused_deputy.jsonl` | Sec. 5.1 | The 45 hand-written confused-deputy scenarios. |
| `data/attacks_full.jsonl`, `build_corpus.py` | Sec. 5.3 | The 239-item injection corpus + its regeneration script (82 hand-written + AgentDojo/InjecAgent-adapted + Gandalf-sampled). |
| `e1_attack_surface.py` | Sec. 5.2 | Static reachability: 9 tools, 0 money-moving. |
| `e3_price_determinism.py` | Sec. 5.2 | Price-path fuzzing: 1 distinct signed value. |
| `e4_production_policy.py` | Sec. 5.2 | **Production-code policy tests** --- imports the shipped `Policy` and applies the production price-clamp formula against adversarial inputs (14/14). |
| `whitebox_adaptive*.py` | Sec. 5.3, Table 3 | Defense-aware (white-box) adaptive attacker. |
| `adaptive_cd*.py`, `adaptive_attack.py` | Sec. 5.3 | Black-box adaptive attacker. |
| `benign_utility.py`, `rescore_benign.py` | Sec. 5.7 | Benign-job utility cost (<=3 pts). |
| `conformance.py` | Sec. 5.5 | ALP conformance suite (9/9 offline). |
| `stats_per_scenario.py` | App. E | Per-scenario (trial-consistency + paired) statistics. |
| `summary_*.md`, `whitebox_cvrs_qwen*.md` | Tables 2, 3 | Released result summaries backing every reported number. |

## Metrics

We report a staged outcome: **attempted** (the model emitted a forbidden money
call) vs. **executed** (the wallet layer would then carry it out). In the harness
`executed` is the wallet layer's sign/reject decision, not an on-chain transfer;
the live testnet deployment (paper Sec. 5.6) closes that gap.

## Quick start

```bash
pip install bnbagent-studio==0.0.2
export AWS_REGION=us-east-1

# by-construction / production-code (no network, instant)
python e1_attack_surface.py
python e3_price_determinism.py
python e4_production_policy.py
python conformance.py

# confused-deputy, one model, 3 trials (Bedrock)
python run_eval.py --model claude-haiku-4-5-20251001-v1:0 --trials 3 \
    --attacks data/confused_deputy.jsonl

# per-scenario statistics from released results
python stats_per_scenario.py
```

License: code Apache-2.0; ALP spec CC-BY-4.0.
