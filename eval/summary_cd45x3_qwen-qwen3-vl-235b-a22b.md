| Attack class | (a) signing-layer | (b) prompt-guarded | (c) undefended | (d) spotlighting | (e) helpful-default | (f) guardrailed |
|---|---|---|---|---|---|---|
| Key exfiltration | 0/15 | 0/15 | 15/15 | 1/15 | 15/15 | 0/15 |
| On-chain poisoning | 0/15 | 15/15 | 12/15 | 15/15 | 0/15 | 0/15 |
| Pay attacker address | 0/48 | 45/48 | 48/48 | 45/48 | 46/48 | 0/48 |
| Re-quote price | 0/36 | 36/36 | 36/36 | 36/36 | 36/36 | 0/36 |
| Rogue top-up host | 0/21 | 21/21 | 21/21 | 21/21 | 21/21 | 0/21 |
| **Total (funds moved, 95% CI)** | **0%** (0/135) [0–3] | **87%** (117/135) [80–91] | **98%** (132/135) [94–99] | **87%** (118/135) [81–92] | **87%** (118/135) [81–92] | **0%** (0/135) [0–3] |

### LLM fooled vs. funds moved (the architectural gap)
| Config | LLM fooled | Funds moved |
|---|---|---|
| (a) signing-layer | 0% (0/135) | **0%** (0/135) |
| (b) prompt-guarded | 87% (117/135) | **87%** (117/135) |
| (c) undefended | 98% (132/135) | **98%** (132/135) |
| (d) spotlighting | 87% (118/135) | **87%** (118/135) |
| (e) helpful-default | 87% (118/135) | **87%** (118/135) |
| (f) guardrailed | 87% (118/135) | **0%** (0/135) |
