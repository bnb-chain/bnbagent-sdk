"""Judge calibration: agreement of a candidate judge with reference labels.

Ported from AgentSight ``agent_cost_reduction/benchmark.py`` metrics: MAE, per-class MAE,
Cohen's kappa, quadratic-weighted kappa, MCC and fail precision / recall / F1 at a threshold,
plus Spearman rank correlation and threshold-decision agreement.
Scores are on [0, 1]; categorical metrics bin them onto ``levels`` (default: the 5-tier scale).

Full benchmark port: :func:`benchmark_agreement` follows ``_compute_agreement`` (pairs by
case id, binary vs ordinal score types, labels rounded to 2 dp, linear-weighted kappa, MCC for
binary, fail P/R/F1 at 0.75 for ordinal), plus accuracy and confusion matrices;
:func:`rank_judges` follows the ``run_benchmark`` ranking; :func:`estimate_cost` follows
``visualize_benchmark._estimate_cost`` (price table, substring model match). Kendall's tau is
not in the source and is not provided.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from typing import Any

FIVE_TIER_LEVELS = (0.0, 0.25, 0.5, 0.75, 1.0)


@dataclass(frozen=True)
class Agreement:
    n: int
    mae: float
    per_class_mae: dict[str, float]
    cohen_kappa: float
    weighted_kappa: float
    spearman: float
    decision_agreement: float
    mcc: float
    fail_precision: float
    fail_recall: float
    fail_f1: float

    def to_dict(self) -> dict:
        return asdict(self)


def _bin(x: float, levels: Sequence[float]) -> int:
    return min(range(len(levels)), key=lambda i: (abs(levels[i] - x), i))


def cohen_kappa(a: Sequence[int], b: Sequence[int], k: int, weights: str | None = None) -> float:
    n = len(a)
    observed = [[0.0] * k for _ in range(k)]
    for x, y in zip(a, b, strict=True):
        observed[x][y] += 1
    row = [sum(r) for r in observed]
    col = [sum(observed[i][j] for i in range(k)) for j in range(k)]

    def w(i: int, j: int) -> float:
        if weights == "quadratic":
            return ((i - j) / (k - 1)) ** 2 if k > 1 else 0.0
        if weights == "linear":
            return abs(i - j) / (k - 1) if k > 1 else 0.0
        return 0.0 if i == j else 1.0

    num = sum(w(i, j) * observed[i][j] for i in range(k) for j in range(k))
    den = sum(w(i, j) * row[i] * col[j] / n for i in range(k) for j in range(k))
    return 1.0 if den == 0 else 1.0 - num / den


def _ranks(values: Sequence[float]) -> list[float]:
    order = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        for t in range(i, j + 1):
            ranks[order[t]] = (i + j) / 2 + 1
        i = j + 1
    return ranks


def spearman(a: Sequence[float], b: Sequence[float]) -> float:
    ra, rb = _ranks(a), _ranks(b)
    ma, mb = sum(ra) / len(ra), sum(rb) / len(rb)
    cov = sum((x - ma) * (y - mb) for x, y in zip(ra, rb, strict=True))
    va = math.sqrt(sum((x - ma) ** 2 for x in ra))
    vb = math.sqrt(sum((y - mb) ** 2 for y in rb))
    return 0.0 if va == 0 or vb == 0 else cov / (va * vb)


def agreement(
    candidate: Sequence[float],
    reference: Sequence[float],
    *,
    threshold: float = 0.5,
    levels: Sequence[float] = FIVE_TIER_LEVELS,
) -> Agreement:
    """Agreement of ``candidate`` judge scores with ``reference`` (human or reference-judge)."""
    if len(candidate) != len(reference) or not candidate:
        raise ValueError("candidate and reference must be non-empty and of equal length")
    n = len(candidate)
    mae = sum(abs(c - r) for c, r in zip(candidate, reference, strict=True)) / n
    bc = [_bin(c, levels) for c in candidate]
    br = [_bin(r, levels) for r in reference]
    per_class: dict[str, list[float]] = {}
    for c, r, b in zip(candidate, reference, br, strict=True):
        per_class.setdefault(str(levels[b]), []).append(abs(c - r))
    # Binary decision at the threshold; "fail" (score < threshold) is the positive class.
    pc = [c < threshold for c in candidate]
    pr = [r < threshold for r in reference]
    tp = sum(a and b for a, b in zip(pc, pr, strict=True))
    tn = sum(not a and not b for a, b in zip(pc, pr, strict=True))
    fp = sum(a and not b for a, b in zip(pc, pr, strict=True))
    fn = sum(not a and b for a, b in zip(pc, pr, strict=True))
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    mcc_den = math.sqrt((tp + fp) * (tp + fn) * (tn + fp) * (tn + fn))
    return Agreement(
        n=n,
        mae=mae,
        per_class_mae={k: sum(v) / len(v) for k, v in sorted(per_class.items())},
        cohen_kappa=cohen_kappa(bc, br, len(levels)),
        weighted_kappa=cohen_kappa(bc, br, len(levels), "quadratic"),
        spearman=spearman(candidate, reference),
        decision_agreement=(tp + tn) / n,
        mcc=(tp * tn - fp * fn) / mcc_den if mcc_den else 0.0,
        fail_precision=precision,
        fail_recall=recall,
        fail_f1=f1,
    )


# ----------------------------------------------------------------------------------------------
# Full benchmark agreement (AgentSight ``benchmark._compute_agreement``), judge ranking and cost.
# ----------------------------------------------------------------------------------------------

AS_ORDINAL_PASS_THRESHOLD = 0.75  # AgentSight ``_binarize``: ordinal scores pass at >= 0.75


def is_binary(scores: Sequence[float]) -> bool:
    """Whether every score is exactly 0.0 or 1.0 (AgentSight ``_is_binary``)."""
    return all(s in (0.0, 1.0) for s in scores)


def _labels(scores: Sequence[float]) -> list[str]:
    # AgentSight ``_to_labels``: round to 2 dp so near-identical floats share a label.
    return [str(round(s, 2)) for s in scores]


def _label_sort_key(v: Any) -> tuple[int, Any]:
    try:
        return (0, float(v))
    except (TypeError, ValueError):
        return (1, str(v))


def accuracy(candidate: Sequence[Any], reference: Sequence[Any]) -> float:
    """Share of exact label matches."""
    if len(candidate) != len(reference) or not candidate:
        raise ValueError("candidate and reference must be non-empty and of equal length")
    return sum(c == r for c, r in zip(candidate, reference, strict=True)) / len(candidate)


def label_kappa(
    candidate: Sequence[Any], reference: Sequence[Any], weights: str | None = None
) -> float:
    """Cohen's kappa over arbitrary labels (``weights``: None, ``linear`` or ``quadratic``).

    Labels are ordered numerically when possible (as sklearn sorts them) for weighted kappa.
    """
    space = sorted(set(candidate) | set(reference), key=_label_sort_key)
    index = {v: i for i, v in enumerate(space)}
    return cohen_kappa(
        [index[r] for r in reference], [index[c] for c in candidate], len(space), weights
    )


@dataclass(frozen=True)
class ConfusionMatrix:
    """``matrix[i][j]``: cases with reference label ``labels[i]`` judged as ``labels[j]``."""

    labels: tuple[str, ...]
    matrix: tuple[tuple[int, ...], ...]

    def to_dict(self) -> dict:
        return {"labels": list(self.labels), "matrix": [list(r) for r in self.matrix]}

    def count(self, reference: str, candidate: str) -> int:
        return self.matrix[self.labels.index(reference)][self.labels.index(candidate)]


def confusion_matrix(
    candidate: Sequence[Any], reference: Sequence[Any], labels: Sequence[Any] | None = None
) -> ConfusionMatrix:
    """Confusion matrix (rows: reference, columns: candidate) over ``labels`` (default: seen)."""
    if len(candidate) != len(reference):
        raise ValueError("candidate and reference must be of equal length")
    if labels is None:
        labels = sorted(set(candidate) | set(reference), key=_label_sort_key)
    index = {v: i for i, v in enumerate(labels)}
    m = [[0] * len(labels) for _ in labels]
    for c, r in zip(candidate, reference, strict=True):
        if c in index and r in index:
            m[index[r]][index[c]] += 1
    return ConfusionMatrix(tuple(str(v) for v in labels), tuple(tuple(r) for r in m))


def tiered_confusion(
    candidate: Sequence[float],
    reference: Sequence[float],
    levels: Sequence[float] = FIVE_TIER_LEVELS,
) -> ConfusionMatrix:
    """Confusion matrix after binning scores onto ``levels`` (e.g. the 5-tier scale)."""
    lv = [float(x) for x in levels]
    return confusion_matrix(
        [lv[_bin(c, lv)] for c in candidate], [lv[_bin(r, lv)] for r in reference], lv
    )


def binary_confusion(
    candidate: Sequence[float], reference: Sequence[float], threshold: float = 0.5
) -> ConfusionMatrix:
    """Pass / fail confusion matrix at ``threshold`` (score >= threshold passes)."""
    return confusion_matrix(
        ["pass" if c >= threshold else "fail" for c in candidate],
        ["pass" if r >= threshold else "fail" for r in reference],
        ("fail", "pass"),
    )


def as_per_class_mae(reference: Sequence[float], candidate: Sequence[float]) -> dict[str, float]:
    """MAE per reference level (AgentSight ``_per_class_mae``): ``mae_pass`` / ``mae_fail``
    for binary references, else ``mae_<level>``."""
    groups: dict[float, list[float]] = {}
    for r, c in zip(reference, candidate, strict=True):
        groups.setdefault(round(r, 2), []).append(abs(r - c))
    binary = is_binary(reference)
    out = {}
    for level, errors in sorted(groups.items()):
        key = ("mae_pass" if level == 1.0 else "mae_fail") if binary else f"mae_{level}"
        out[key] = round(sum(errors) / len(errors), 4)
    return out


def _fail_detection(
    ref_pass: Sequence[int], cand_pass: Sequence[int]
) -> tuple[float | None, float | None, float | None]:
    """Fail precision / recall / F1 (fail is the positive class); None when undefined."""
    if 0 not in ref_pass and 0 not in cand_pass:
        return None, None, None
    pairs = list(zip(ref_pass, cand_pass, strict=True))
    tp = sum(r == 0 and c == 0 for r, c in pairs)
    fp = sum(r == 1 and c == 0 for r, c in pairs)
    fn = sum(r == 0 and c == 1 for r, c in pairs)
    p = tp / (tp + fp) if tp + fp else 0.0
    rc = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * p * rc / (p + rc) if p + rc else 0.0
    return round(p, 4), round(rc, 4), round(f1, 4)


def _mcc(ref: Sequence[int], cand: Sequence[int]) -> float | None:
    if len(set(ref)) < 2 and len(set(cand)) < 2:
        return None  # undefined when both sides hold a single class
    pairs = list(zip(ref, cand, strict=True))
    tp = sum(r == 1 and c == 1 for r, c in pairs)
    tn = sum(r == 0 and c == 0 for r, c in pairs)
    fp = sum(r == 0 and c == 1 for r, c in pairs)
    fn = sum(r == 1 and c == 0 for r, c in pairs)
    den = math.sqrt((tp + fp) * (tp + fn) * (tn + fp) * (tn + fn))
    return round((tp * tn - fp * fn) / den, 4) if den else 0.0


@dataclass(frozen=True)
class AgreementReport:
    """AgentSight benchmark agreement of one candidate judge on one metric.

    ``None`` replaces the source's NaN for undefined values (JSON-safe). ``weighted_kappa`` is
    linear-weighted (as in the source) and set for ordinal scores only; ``mcc`` for binary only.
    """

    score_type: str  # "binary" | "ordinal" | "unknown"
    n_compared: int
    mae: float | None = None
    cohens_kappa: float | None = None
    weighted_kappa: float | None = None
    mcc: float | None = None
    accuracy: float | None = None
    spearman: float | None = None
    decision_agreement: float | None = None
    fail_precision: float | None = None
    fail_recall: float | None = None
    fail_f1: float | None = None
    per_class_mae: dict[str, float] | None = None
    confusion: ConfusionMatrix | None = None
    missing_candidate: int = 0  # candidate gave no score: penalized as 1 - reference
    decision_kappa: float | None = None  # kappa over pass / fail decisions at the cut
    labels_binary: bool = False  # reference labels are pass / fail only

    @property
    def gate_kappa(self) -> float | None:
        """The kappa a calibration gate should use: decision-level when the human labels are
        pass / fail but the judge scores on tiers (tier vs 1.0 is not a disagreement)."""
        if self.labels_binary and self.score_type == "ordinal":
            return self.decision_kappa
        return self.cohens_kappa

    def to_dict(self) -> dict:
        d = asdict(self)
        d["confusion"] = self.confusion.to_dict() if self.confusion else None
        d["gate_kappa"] = self.gate_kappa
        return d


def benchmark_agreement(
    reference: Mapping[Any, float | None] | Sequence[float | None],
    candidate: Mapping[Any, float | None] | Sequence[float | None],
    *,
    threshold: float | None = None,
) -> AgreementReport:
    """Agreement of ``candidate`` with ``reference`` scores, matched by key (case id) or index.

    As in AgentSight: pairs without a reference score are dropped; a missing candidate score is
    penalized as ``1 - reference``. Binary scores (all 0 / 1) get MCC; ordinal scores get a
    linear-weighted kappa and are binarized at ``threshold`` (default 0.75, the source value)
    for fail detection. Accuracy, Spearman and a confusion matrix are added.
    """
    ref = reference if isinstance(reference, Mapping) else dict(enumerate(reference))
    cand = candidate if isinstance(candidate, Mapping) else dict(enumerate(candidate))
    pairs: list[tuple[float, float]] = []
    missing = 0
    for key in sorted(set(ref) & set(cand), key=str):
        r, c = ref[key], cand[key]
        if r is None:
            continue
        if c is None:
            c, missing = 1.0 - r, missing + 1
        pairs.append((float(r), float(c)))
    if not pairs:
        return AgreementReport(score_type="unknown", n_compared=0)
    gt = [p[0] for p in pairs]
    cd = [p[1] for p in pairs]
    binary = is_binary(gt) and is_binary(cd)
    gl, cl = _labels(gt), _labels(cd)
    cut = threshold if threshold is not None else (0.5 if binary else AS_ORDINAL_PASS_THRESHOLD)
    gt_pass = [int(g >= cut) for g in gt]
    cd_pass = [int(c >= cut) for c in cd]
    precision, recall, f1 = _fail_detection(gt_pass, cd_pass)
    return AgreementReport(
        score_type="binary" if binary else "ordinal",
        n_compared=len(pairs),
        mae=round(sum(abs(g - c) for g, c in pairs) / len(pairs), 4),
        cohens_kappa=round(label_kappa(cl, gl), 4),
        weighted_kappa=None if binary else round(label_kappa(cl, gl, "linear"), 4),
        mcc=_mcc(gt_pass, cd_pass) if binary else None,
        accuracy=round(accuracy(cl, gl), 4),
        spearman=round(spearman(cd, gt), 4) if len(pairs) > 1 else None,
        decision_agreement=round(accuracy(cd_pass, gt_pass), 4),
        fail_precision=precision,
        fail_recall=recall,
        fail_f1=f1,
        per_class_mae=as_per_class_mae(gt, cd),
        confusion=binary_confusion(cd, gt, cut) if binary else confusion_matrix(cl, gl),
        missing_candidate=missing,
        decision_kappa=round(label_kappa(cd_pass, gt_pass), 4),
        labels_binary=is_binary(gt),
    )


# ------------------------------------------------------------------------------- judge cost
PriceTable = Mapping[str, Mapping[str, float]]


def price_row(model: str, table: PriceTable) -> Mapping[str, float] | None:
    """Price row for ``model``: exact key, else the longest key contained in the model id
    (AgentSight's substring match), else ``"*"``."""
    if model in table:
        return table[model]
    matches = [k for k in table if k != "*" and k in model]
    if matches:
        return table[max(matches, key=len)]
    return table.get("*")


def estimate_cost(
    model: str, input_tokens: int, output_tokens: int, table: PriceTable
) -> float | None:
    """USD cost of tokens, or ``None`` if ``model`` is not priced.

    Rows use ``input_per_1k`` / ``output_per_1k`` (as ``ops.cost``) or per-1M prices
    ``input_per_1m`` / ``output_per_1m`` (AgentSight ``bedrock_pricing.json``: ``input`` /
    ``output``).
    """
    row = price_row(model, table)
    if row is None:
        return None
    if "input_per_1k" in row or "output_per_1k" in row:
        return (
            input_tokens * row.get("input_per_1k", 0.0)
            + output_tokens * row.get("output_per_1k", 0.0)
        ) / 1000
    inp = row.get("input_per_1m", row.get("input", 0.0))
    out = row.get("output_per_1m", row.get("output", 0.0))
    return (input_tokens * inp + output_tokens * out) / 1_000_000


@dataclass
class JudgeUsage:
    model: str
    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    reported_usd: float = 0.0  # cost_usd reported by the provider, if any

    def cost(self, table: PriceTable | None = None) -> float | None:
        if table:
            priced = estimate_cost(self.model, self.input_tokens, self.output_tokens, table)
            if priced is not None:
                return priced
        return self.reported_usd or None

    def to_dict(self, table: PriceTable | None = None) -> dict:
        usd = self.cost(table)
        return {
            "model": self.model,
            "calls": self.calls,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "usd": None if usd is None else round(usd, 6),
        }


class MeteredJudge:
    """Wraps a judge model and counts calls and tokens (for judge cost comparison)."""

    def __init__(self, inner: Any):
        self.inner = inner
        self.config = inner.config
        self.usage = JudgeUsage(model=inner.config.model)

    def __getattr__(self, name: str) -> Any:
        return getattr(self.inner, name)

    async def complete(self, request: Any) -> Any:
        response = await self.inner.complete(request)
        u = response.usage
        self.usage.calls += 1
        self.usage.input_tokens += u.input_tokens or 0
        self.usage.output_tokens += u.output_tokens or 0
        self.usage.reported_usd += u.cost_usd or 0.0
        return response

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        return await self.inner.embed(texts)


def _finite_mean(values: Sequence[float | None]) -> float | None:
    vals = [v for v in values if v is not None and not math.isnan(v)]
    return round(sum(vals) / len(vals), 4) if vals else None


def rank_judges(
    agreements: Mapping[str, Mapping[str, AgreementReport]],
    *,
    usage: Mapping[str, JudgeUsage] | None = None,
    prices: PriceTable | None = None,
    reference_cost: float | None = None,
) -> list[dict]:
    """Rank candidate judges (``judge -> metric -> report``) by mean Cohen's kappa, best first.

    AgentSight ``run_benchmark`` ranking (overall kappa, MAE, fail F1), plus cost, cost per
    compared case and the cost ratio to the reference judge when usage is given.
    """
    ranking = []
    for judge, per_metric in agreements.items():
        reports = list(per_metric.values())
        row: dict[str, Any] = {
            "judge": judge,
            "overall_cohens_kappa": _finite_mean([r.cohens_kappa for r in reports]),
            "overall_mae": _finite_mean([r.mae for r in reports]),
            "overall_fail_f1": _finite_mean([r.fail_f1 for r in reports]),
            "n_compared": sum(r.n_compared for r in reports),
        }
        u = (usage or {}).get(judge)
        if u is not None:
            usd = u.cost(prices)
            row["usage"] = u.to_dict(prices)
            row["usd"] = usd
            row["usd_per_case"] = (
                usd / row["n_compared"] if usd is not None and row["n_compared"] else None
            )
            row["cost_ratio"] = (
                usd / reference_cost if usd is not None and reference_cost else None
            )
        ranking.append(row)
    # The source sorts ascending by kappa although higher is better; we rank best first and
    # break ties by lower cost.
    ranking.sort(
        key=lambda r: (
            -(r["overall_cohens_kappa"] if r["overall_cohens_kappa"] is not None else -999),
            r["usd"] if r.get("usd") is not None else math.inf,
        )
    )
    return ranking


# ---------------------------------------------------------------------------- human labels
_TRUE_LABELS = {"pass", "passed", "yes", "true", "correct"}
_FALSE_LABELS = {"fail", "failed", "no", "false", "incorrect"}
_TIER_LABELS = {"poor": 0.0, "below average": 0.25, "average": 0.5, "good": 0.75, "excellent": 1.0}


def parse_label(value: Any) -> float | None:
    """A human label as a score on [0, 1]: a bool, a number, pass / fail, yes / no, or a
    5-tier name (Poor ... Excellent). ``None`` stays ``None``; anything else raises."""
    if value is None:
        return None
    if isinstance(value, bool):
        return 1.0 if value else 0.0
    if isinstance(value, (int, float)):
        if not 0.0 <= float(value) <= 1.0:
            raise ValueError(f"label {value!r} is outside [0, 1]")
        return float(value)
    if isinstance(value, str):
        text = value.strip().lower()
        if text in _TIER_LABELS:
            return _TIER_LABELS[text]
        if text in _TRUE_LABELS:
            return 1.0
        if text in _FALSE_LABELS:
            return 0.0
        try:
            number = float(text)
        except ValueError:
            pass
        else:
            return parse_label(number)
    raise ValueError(f"unrecognized label {value!r}")


def case_label(
    case_id: str,
    metadata: Mapping[str, Any],
    metric_key: str,
    metric_id: str,
    labels: Mapping[str, Any] | None = None,
) -> float | None:
    """The reference label of one case for one metric (``None``: unlabelled).

    Lookup order: ``labels[case_id]`` (a scalar, or a mapping by metric name / id), then
    ``metadata["labels"]`` (same shapes), ``metadata["human_label"]``, ``metadata["label"]``.
    Clones injected for another evaluator (``metadata["evaluator"]``) are unlabelled.
    """
    evaluator = metadata.get("evaluator")
    if evaluator not in (None, "default", metric_key, metric_id):
        return None
    for source in ((labels or {}).get(case_id), metadata.get("labels")):
        if isinstance(source, Mapping):
            if metric_key in source or metric_id in source:
                return parse_label(source.get(metric_key, source.get(metric_id)))
        elif source is not None:
            return parse_label(source)
    for key in ("human_label", "label"):
        if metadata.get(key) is not None:
            return parse_label(metadata[key])
    return None
