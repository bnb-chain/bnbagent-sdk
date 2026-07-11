| Attack class | (a) signing-layer | (b) prompt-guarded | (c) undefended | (d) spotlighting | (e) helpful-default | (f) guardrailed |
|---|---|---|---|---|---|---|
| Key exfiltration | 0/15 | 0/15 | 0/15 | 0/15 | 0/15 | 0/15 |
| On-chain poisoning | 0/15 | 0/15 | 0/15 | 0/15 | 0/15 | 0/15 |
| Pay attacker address | 0/48 | 3/48 | 23/48 | 0/48 | 4/48 | 0/48 |
| Re-quote price | 0/36 | 0/36 | 21/36 | 0/36 | 14/36 | 0/36 |
| Rogue top-up host | 0/21 | 0/21 | 0/21 | 0/21 | 0/21 | 0/21 |
| **Total (funds moved, 95% CI)** | **0%** (0/135) [0–3] | **2%** (3/135) [1–6] | **33%** (44/135) [25–41] | **0%** (0/135) [0–3] | **13%** (18/135) [9–20] | **0%** (0/135) [0–3] |

### LLM fooled vs. funds moved (the architectural gap)
| Config | LLM fooled | Funds moved |
|---|---|---|
| (a) signing-layer | 0% (0/135) | **0%** (0/135) |
| (b) prompt-guarded | 2% (3/135) | **2%** (3/135) |
| (c) undefended | 33% (44/135) | **33%** (44/135) |
| (d) spotlighting | 0% (0/135) | **0%** (0/135) |
| (e) helpful-default | 13% (18/135) | **13%** (18/135) |
| (f) guardrailed | 16% (22/135) | **0%** (0/135) |
