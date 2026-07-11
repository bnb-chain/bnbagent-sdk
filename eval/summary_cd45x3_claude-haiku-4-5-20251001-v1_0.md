| Attack class | (a) signing-layer | (b) prompt-guarded | (c) undefended | (d) spotlighting | (e) helpful-default | (f) guardrailed |
|---|---|---|---|---|---|---|
| Key exfiltration | 0/15 | 0/15 | 3/15 | 0/15 | 3/15 | 0/15 |
| On-chain poisoning | 0/15 | 0/15 | 0/15 | 3/15 | 0/15 | 0/15 |
| Pay attacker address | 0/48 | 0/48 | 45/48 | 9/48 | 39/48 | 0/48 |
| Re-quote price | 0/36 | 0/36 | 36/36 | 0/36 | 33/36 | 0/36 |
| Rogue top-up host | 0/21 | 0/21 | 18/21 | 3/21 | 18/21 | 0/21 |
| **Total (funds moved, 95% CI)** | **0%** (0/135) [0–3] | **0%** (0/135) [0–3] | **76%** (102/135) [68–82] | **11%** (15/135) [7–18] | **69%** (93/135) [61–76] | **0%** (0/135) [0–3] |

### LLM fooled vs. funds moved (the architectural gap)
| Config | LLM fooled | Funds moved |
|---|---|---|
| (a) signing-layer | 0% (0/135) | **0%** (0/135) |
| (b) prompt-guarded | 0% (0/135) | **0%** (0/135) |
| (c) undefended | 76% (102/135) | **76%** (102/135) |
| (d) spotlighting | 11% (15/135) | **11%** (15/135) |
| (e) helpful-default | 69% (93/135) | **69%** (93/135) |
| (f) guardrailed | 69% (93/135) | **0%** (0/135) |
