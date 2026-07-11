| Attack class | (a) signing-layer | (b) prompt-guarded | (c) undefended | (d) spotlighting | (e) helpful-default | (f) guardrailed |
|---|---|---|---|---|---|---|
| Key exfiltration | 0/15 | 2/15 | 5/15 | 2/15 | 7/15 | 0/15 |
| On-chain poisoning | 0/15 | 1/15 | 1/15 | 3/15 | 0/15 | 0/15 |
| Pay attacker address | 0/48 | 32/48 | 43/48 | 23/48 | 37/48 | 0/48 |
| Re-quote price | 0/36 | 30/36 | 29/36 | 30/36 | 26/36 | 0/36 |
| Rogue top-up host | 0/21 | 12/21 | 18/21 | 13/21 | 14/21 | 0/21 |
| **Total (funds moved, 95% CI)** | **0%** (0/135) [0–3] | **57%** (77/135) [49–65] | **71%** (96/135) [63–78] | **53%** (71/135) [44–61] | **62%** (84/135) [54–70] | **0%** (0/135) [0–3] |

### LLM fooled vs. funds moved (the architectural gap)
| Config | LLM fooled | Funds moved |
|---|---|---|
| (a) signing-layer | 0% (0/135) | **0%** (0/135) |
| (b) prompt-guarded | 57% (77/135) | **57%** (77/135) |
| (c) undefended | 71% (96/135) | **71%** (96/135) |
| (d) spotlighting | 53% (71/135) | **53%** (71/135) |
| (e) helpful-default | 62% (84/135) | **62%** (84/135) |
| (f) guardrailed | 59% (79/135) | **0%** (0/135) |
