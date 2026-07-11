# Test data (input corpora)

Input attack corpora for the §5 evaluation. **These are inputs** — result and
summary files stay in the `eval/` root. Runners default to `data/<file>`.

| File | N | Role | Provenance / license |
|---|---|---|---|
| `confused_deputy.jsonl` | 45 | Table 2 / §5.1 confused-deputy suite | author-written — released outright |
| `attacks_full.jsonl` | 239 | §5.4 injection suite; §5.5 white-box seeds | assembled by `../build_corpus.py`; mixed provenance (below) |
| `attacks_handwritten.jsonl` | 66 | author seeds for `build_corpus.py` | author-written — released outright |
| `attacks.jsonl` | 20 | pilot author seeds for `build_corpus.py` | author-written — released outright |
| `gandalf_sample.jsonl` | 60 | real human jailbreak phrasings, spliced into job format | Lakera Gandalf (license under review) |

`attacks_full.jsonl` composition: 82 author-written + benchmark-derived, adapted
from **AgentDojo** (MIT — OK to redistribute), **InjecAgent**, and **Lakera
Gandalf** (both under license review). ~66% is public-benchmark-origin. The 127
author-written items are released outright regardless.

Regenerate `attacks_full.jsonl` from the sources here:

```bash
cd ..            # eval/
python build_corpus.py --out data/attacks_full.jsonl --min-cell 10
```

Not included here (see the paper): the 40-item human-jailbreak stress set
adapted from "Do Anything Now" (Shen et al. 2024).
