"""Contract inputs for text.bertscore (model methods: skipped unless the extra is installed).

Real BERTScore never reaches exactly 0.0 on disjoint text (unrescaled F1 stays well above 0), so
the behavioural checks are skipped even with the extra; tests/unit/metrics/test_local_models.py
covers the method with a fake encoder; tests/parity/test_bertscore_parity.py checks it against
bert-score.
"""

from __future__ import annotations

from .catalog import DISJOINT, SENTENCE, ContractCase, ref_pair

CONTRACT = {
    "text.bertscore": ContractCase(
        **ref_pair(SENTENCE, DISJOINT),
        skip="model scores have no exact 0.0 worst case; see test_local_models.py",
    ),
}
