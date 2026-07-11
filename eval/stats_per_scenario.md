# Per-scenario statistics (scenario as sampling unit)

Each cell: for config (e) helpful-default, the number of the 45 scenarios fooled in exactly $k/3$ trials; scenario-rate = fooled in $\ge2/3$. (f) guardrailed executes on 0 regardless.

| Model | fooled 0/3 | 1/3 | 2/3 | 3/3 | scenario-rate ($\ge2$) | (e)$\wedge$(f) both fooled | (f) executed |
|---|---|---|---|---|---|---|---|
| Haiku 4.5 | 14 | 0 | 0 | 31 | 31/45 | 31 | 0 |
| Sonnet 4.6 | 37 | 2 | 2 | 4 | 6/45 | 5 | 0 |
| Opus 4.8 | 37 | 1 | 1 | 6 | 7/45 | 6 | 0 |
| Qwen3-235B | 5 | 1 | 0 | 39 | 39/45 | 39 | 0 |
| MiniMax-M2 | 11 | 5 | 8 | 21 | 29/45 | 24 | 0 |

**Reading.** The trial-consistency histogram shows the fooled outcome is largely deterministic per scenario (most scenarios sit at 0/3 or 3/3), so the 135-run rates are stable, not an artifact of independence assumptions. In the paired column, scenarios that fool the natural build (e) also fool the guardrailed build (f) at a comparable rate---yet (f) executes on \textbf{0} scenarios, so the $0$ result is not explained by (f) being fooled less often. For the structural claim, the primary evidence is the by-construction / production-code tests (\S5.2), not these rates.
