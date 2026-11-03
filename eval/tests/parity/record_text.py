"""Record reference values for the text metrics.

Run OUTSIDE the normal test environment, in a venv with the reference libraries:
    pip install rouge-score nltk scikit-learn && pip install -e eval
    python eval/tests/parity/record_text.py
It writes tests/fixtures/parity/text.json, which test_text_parity.py compares against.
"""

from __future__ import annotations

import json
from pathlib import Path

from nltk.stem.porter import PorterStemmer
from nltk.translate.bleu_score import SmoothingFunction, sentence_bleu
from rouge_score import rouge_scorer
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

PAIRS = [
    ("the cat is on the mat", "The cat sat on the mat."),
    ("A quick brown fox jumps over the lazy dog", "the quick brown fox jumped over a lazy dog"),
    ("BNB Chain processes transactions quickly", "Transactions on BNB Chain are processed fast"),
    ("The agent called the search tool twice", "The agent used the search tool two times"),
    ("Deliver the report by Friday", "Report delivered on Monday"),
    ("running runners ran", "the runner runs"),
    ("identical sentence here", "identical sentence here"),
    ("completely different", "nothing shared at all"),
    ("Generalization and organization of hypotheses", "generalize organizing hypothesis"),
    ("one", "one two three four five six"),
]
STEM_WORDS = [
    "caresses",
    "ponies",
    "relational",
    "conditional",
    "rational",
    "valenci",
    "digitizer",
    "conformabli",
    "radicalli",
    "differentli",
    "vileli",
    "analogousli",
    "vietnamization",
    "predication",
    "operator",
    "feudalism",
    "decisiveness",
    "hopefulness",
    "callousness",
    "formaliti",
    "sensitiviti",
    "sensibiliti",
    "triplicate",
    "formative",
    "formalize",
    "electriciti",
    "electrical",
    "hopeful",
    "goodness",
    "revival",
    "allowance",
    "inference",
    "airliner",
    "gyroscopic",
    "adjustable",
    "defensible",
    "irritant",
    "replacement",
    "adjustment",
    "dependent",
    "adoption",
    "homologou",
    "communism",
    "activate",
    "angulariti",
    "homologous",
    "effective",
    "bowdlerize",
    "probate",
    "rate",
    "cease",
    "controll",
    "roll",
    "running",
    "hopping",
    "agreed",
    "plastered",
    "motoring",
    "sing",
    "conflated",
    "troubled",
    "sized",
    "hissing",
    "failing",
    "filing",
    "happy",
    "sky",
]

out = {"pairs": []}
scorer = {
    v: rouge_scorer.RougeScorer([v], use_stemmer=s)
    for v in ("rouge1", "rouge2", "rougeL")
    for s in (True,)
}
nostem = rouge_scorer.RougeScorer(["rougeL"], use_stemmer=False)
for ref, cand in PAIRS:
    entry = {"reference": ref, "candidate": cand}
    for v, sc in scorer.items():
        entry[f"{v}_stem"] = sc.score(ref, cand)[v].fmeasure
    entry["rougeL_nostem"] = nostem.score(ref, cand)["rougeL"].fmeasure
    entry["bleu"] = sentence_bleu(
        [ref.lower().split()],
        cand.lower().split(),
        weights=(0.25,) * 4,
        smoothing_function=SmoothingFunction().method1,
    )
    m = TfidfVectorizer().fit_transform([cand, ref])
    entry["tfidf_cosine"] = float(cosine_similarity(m[0:1], m[1:2])[0][0])
    out["pairs"].append(entry)
st = PorterStemmer(mode=PorterStemmer.ORIGINAL_ALGORITHM)
out["porter_original"] = {w: st.stem(w) for w in STEM_WORDS}
path = Path(__file__).resolve().parents[1] / "fixtures" / "parity" / "text.json"
path.write_text(json.dumps(out, indent=1, ensure_ascii=False) + "\n")
print("wrote", path)
