"""``quality.summarization``: is the summary faithful to its source and does it cover the key
information (new prompt; DeepEval summarization semantics). Input = source text, output = summary.
"""

from __future__ import annotations

from ...judges.prompts.quality import summarization as prompts
from ..base import MetricSpec
from ..judge_base import judge_params, native_method
from ..registry import register
from ._common import QualityJudge


@register
class Summarization(QualityJudge):
    templates = prompts.VERSIONS
    spec = MetricSpec(
        id="quality.summarization",
        category="quality",
        description="The summary is faithful to the source and covers its key information.",
        methods=(native_method("input", "output", optional=("expected",)),),
        params_schema=judge_params(),
        default_threshold=0.5,
        sources=("deepeval:summarization",),
    )
