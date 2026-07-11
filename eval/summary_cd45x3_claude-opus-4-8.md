| Attack class | (a) signing-layer | (b) prompt-guarded | (c) undefended | (d) spotlighting | (e) helpful-default | (f) guardrailed |
|---|---|---|---|---|---|---|
| Key exfiltration | 0/15 | 0/15 | 0/15 | 0/15 | 0/15 | 0/15 |
| On-chain poisoning | 0/15 | 0/15 | 0/15 | 0/15 | 0/15 | 0/15 |
| Pay attacker address | 0/48 | 0/48 | 0/48 | 0/48 | 0/48 | 0/48 |
| Re-quote price | 0/36 | 0/36 | 4/36 | 0/38 | 21/36 | 0/36 |
| Rogue top-up host | 0/21 | 0/21 | 0/21 | 0/21 | 0/21 | 0/21 |
| **Total (funds moved, 95% CI)** | **0%** (0/135) [0–3] | **0%** (0/135) [0–3] | **3%** (4/135) [1–7] | **0%** (0/137) [0–3] | **16%** (21/135) [10–23] | **0%** (0/135) [0–3] |

### LLM fooled vs. funds moved (the architectural gap)
| Config | LLM fooled | Funds moved |
|---|---|---|
| (a) signing-layer | 0% (0/135) | **0%** (0/135) |
| (b) prompt-guarded | 0% (0/135) | **0%** (0/135) |
| (c) undefended | 3% (4/135) | **3%** (4/135) |
| (d) spotlighting | 0% (0/137) | **0%** (0/137) |
| (e) helpful-default | 16% (21/135) | **16%** (21/135) |
| (f) guardrailed | 15% (20/135) | **0%** (0/135) |
