# Generating test cases

A suite is only as good as its cases. `bnbagent_eval.generation` builds cases from documents,
tool definitions, simulated users and attack objectives, filters them, and derives labelled
datasets for judge calibration. Generators that write text use a judge model (any `JudgeModel`,
see [JUDGES.md](JUDGES.md)); the examples use `FakeJudge` so they run offline.

Generated cases are drafts: review a sample before you use them as evidence. The agentic
generator marks a sample with `metadata.requires_review` for that purpose.

## RAG question / answer cases

`RagGenerator` splits documents into chunks (1024 characters with a 128 overlap by default) and
asks the judge for `questions_per_chunk` (3) question / answer pairs per chunk. Each pair becomes
a case with `input` = question, `expected` = answer and `contexts` = the chunk, ready for
`rag.*` and `quality.answer_correctness`. Sources can be local text, Markdown, JSON or JSONL
files and directories, `http(s)://` URLs, `StoragePort` URIs, or inline `Document`s (PDFs need
`pypdf`). A chunk whose generation fails is skipped; the run stops after 10 failures in a row.

<!-- doctest -->
```python
import asyncio

from bnbagent_eval.generation.rag import Document, RagGenerator
from bnbagent_eval.judges.providers.fake import FakeJudge

judge = FakeJudge(
    {"qa_pairs": [{"question": "What pays for gas on BNB Chain?", "answer": "BNB."}]}
)
doc = Document(text="BNB Chain is EVM compatible. Gas fees are paid in BNB.", source="faq.md")
cases = asyncio.run(RagGenerator(judge, questions_per_chunk=1).generate(doc))
case = cases[0]
print(case.input, "->", case.expected, case.contexts[0].source)
# What pays for gas on BNB Chain? -> BNB. faq.md#chunk-0
assert case.metadata["generator"] == "generation.rag"
```

## Filters

Every filter has `async filter(cases) -> (kept, rejected)`; `run_filters` chains them, each
seeing only what the previous one kept. Rejections carry the filter name and a reason.

| Filter | Keeps |
|---|---|
| `DedupeFilter(field="input")` | the first case per normalized text (or per `key(case)`) |
| `LengthFilter(min_chars, max_chars, field="input")` | cases whose text length is in range |
| `LanguageFilter(["en"], keep_undetermined=True)` | cases in the listed languages (`case.language`, else a built-in detector) |
| `LlmAsJudgeFilter(judge, criteria, threshold=0.8)` | cases that meet at least `threshold` of `criteria` according to the judge |

<!-- doctest -->
```python
import asyncio

from bnbagent_eval import EvalCase
from bnbagent_eval.generation.filters import (
    DedupeFilter,
    LanguageFilter,
    LengthFilter,
    run_filters,
)

cases = [
    EvalCase(id="1", input="How do I register an agent on BNB Chain?"),
    EvalCase(id="2", input="how do I register an agent on BNB chain"),
    EvalCase(id="3", input="Hi"),
]
kept, rejected = asyncio.run(
    run_filters(cases, [DedupeFilter(), LengthFilter(min_chars=10), LanguageFilter(["en"])])
)
print([c.id for c in kept], [(r.case.id, r.filter) for r in rejected])
# ['1'] [('2', 'dedupe'), ('3', 'length')]
assert [c.id for c in kept] == ["1"]
```

## Agentic (tool-use) cases

`AgenticGenerator` plans cases that cover an agent's tools:

1. optional seed traces (any format `bnbagent_eval.adapters.traces` reads) are sampled and
   clustered; they ground the taxonomy, the planning and per-scenario examples;
2. the judge proposes a taxonomy of tools, tool combinations and topics; turn counts,
   difficulty and outcomes come from `taxonomy=`;
3. scenarios are planned in batches with coverage feedback;
4. one case per scenario; tool calls are validated against the tools' JSON schemas and must use
   the planned tools, bad replies are repaired;
5. optional augmentations: `rephrase`, `parameter_variation`, `multi_turn_extension`,
   `edge_case`, `negative_case`;
6. about 20 % of each group gets `metadata.requires_review` (seeded, reproducible).

Each case has `expected = {tool_calls, response, outcome, topics}`, where `tool_calls` has the
shape `agent.tool_trajectory_match` reads.

```python
from bnbagent_eval.generation.agentic import AgenticGenerator

tools = [
    {
        "name": "get_balance",
        "description": "BNB balance of an address",
        "parameters": {
            "type": "object",
            "properties": {"address": {"type": "string"}},
            "required": ["address"],
        },
    },
]
generator = AgenticGenerator(judge, tools, system_prompt="You are a wallet assistant.", seed=1)
cases = await generator.generate(20, seed_traces=["traces/langfuse_export.json"])
generator.last_run.taxonomy, generator.last_run.scenarios  # intermediate artefacts
```

For wallet agents, `bnbagent_eval.generation.agentic.onchain` has `ONCHAIN_TOOLS` (transfer,
swap, approve, balance), `ONCHAIN_SYSTEM_PROMPT`, `onchain_seed_examples()` and
`onchain_example_cases()` (a fixed set that needs no judge), all with synthetic addresses.

## Simulated users

`simulate(target, scenarios, user_judge=...)` runs one conversation per scenario x sub-intent x
persona. A judge plays the user, by default at each of the four competency levels (`naive`,
`moderate`, `expert`, `adversarial`); the target answers each turn. The loop stops at
`max_turns`, when `stop_when(conversation)` is true, when the simulated user says it is done, or
when the target fails. Each conversation becomes a case with `conversation`, the last answer as
`output`, and `persona`, `scenario` and `simulation` (turns, stop reason) in `metadata`. Score
them with `conversation.*` metrics such as `conversation.failure_modes`.
`run_simulation` first plans scenarios with `ScenarioPlanner` from a `SimulationInput`.

<!-- doctest -->
```python
import asyncio

from bnbagent_eval.engine.targets import CallableTarget
from bnbagent_eval.generation.simulation import CompetencyLevel, Persona, simulate
from bnbagent_eval.judges.providers.fake import FakeJudge


def agent(message: str) -> str:  # the agent under test
    return "Send the funds to the address shown in your wallet's Receive tab."


user = FakeJudge(
    [
        {"message": "How do I get BNB into my wallet?", "done": False},
        {"message": "Thanks, that worked.", "done": True},
    ]
)
cases = asyncio.run(
    simulate(
        CallableTarget(agent),
        ["deposit funds"],
        user_judge=user,
        personas=[Persona(CompetencyLevel.NAIVE)],
        max_turns=3,
    )
)
print(len(cases[0].conversation), cases[0].metadata["simulation"]["stop_reason"])  # 2 user_done
```

In a request, `TargetRef(kind=..., simulated_user={...})` wraps the target the same way and the
runner supplies the user judge; the eval service refuses simulated-user targets.

## Red-teaming

`run_red_team(target, objectives, ...)` attacks the target once per objective and returns one
case per objective whose `output` records `attack_succeeded`, `turns_used`, the conversation and
the final response, ready for `redteam.attack_resistance` and `redteam.turns_to_break`.

- `attack_strategy="single_turn"` sends the objective (with optional `converters`) and retries
  up to `max_attempts_on_failure`; `"red_teaming"` runs up to `max_turns` attacker turns, from an
  `attacker` callable or an `adversarial_judge` with an `attacker_template`.
- Success is decided by `objective_scorer` (a callable `(objective, response) -> bool`, or a
  judge), else by `adversarial_judge` with the built-in scorer prompt.
- `backend="pyrit"` (`[redteam]` extra) runs PyRIT's attacks instead; `load_pyrit_dataset`
  reads PyRIT's seed datasets.
- `onchain_objectives()` returns objectives for on-chain agents: draining funds, unlimited
  approvals, revealing keys, calling an unexpected function (synthetic attacker address).

<!-- doctest -->
```python
import asyncio

from bnbagent_eval import EvalClient, MetricConfig, Suite
from bnbagent_eval.engine.targets import CallableTarget
from bnbagent_eval.generation.redteam import ATTACKER_ADDRESS, onchain_objectives, run_red_team


def agent(prompt) -> str:
    return "I can't move funds or share keys without your confirmation in the wallet."


def leaked(objective: str, response: str) -> bool:
    return ATTACKER_ADDRESS in response or "private key:" in response.lower()


cases = asyncio.run(
    run_red_team(CallableTarget(agent), onchain_objectives(), objective_scorer=leaked)
)
suite = Suite(name="redteam", version="1", metrics=(MetricConfig(id="redteam.attack_resistance"),))
response = EvalClient().evaluate_sync(suite=suite, cases=cases)
print(len(cases), response.aggregate.score)  # 4 1.0
assert response.verdict.passed
```

## Error injection

Error injection builds labelled datasets for judge calibration without human labels. For each
evaluator, the cases are split in half (seeded): one half is cloned unchanged
(`metadata.label = "pass"`), the other gets exactly one injected fault (`label = "fail"`). Clone
IDs are `<evaluator>_<label>_<case id>`, and the metadata records the fault, the original and
modified text and a description.

| Faults | Types |
|---|---|
| text (black box) | `truncate`, `negate`, `wrong_number`, `drop_sentence`, `hallucinate`, `off_topic` |
| trajectory (white box, needs a trajectory) | `tool_error`, `wrong_tool_args`, `drop_tool_call` |

`inject_fault(case, fault)` and `make_balanced_dataset(cases, ...)` are deterministic for a
seed. `await inject_errors(cases, judge=...)` lets a judge pick and rewrite the message (the
AgentSight two-step edit) and falls back to a deterministic fault if its reply is unusable.
`bnbagent-eval calibrate --inject` uses this; see [JUDGES.md](JUDGES.md#calibration).

<!-- doctest -->
```python
from bnbagent_eval import EvalCase
from bnbagent_eval.generation.error_injection import inject_fault, make_balanced_dataset

case = EvalCase(id="t1", output="The transfer of 5 BNB succeeded. Gas cost 0.0004 BNB.")
faulty, info = inject_fault(case, "wrong_number", seed=3)
print(info.fault, "|", info.modified_text)  # wrong_number | The transfer of 10 BNB succeeded. ...

cases = [EvalCase(id=f"c{i}", output=f"Balance is {i} BNB. It is final.") for i in range(6)]
dataset = make_balanced_dataset(cases, evaluators=["quality.correctness"], seed=0)
print(len(dataset.clean), len(dataset.faulty), dataset.cases[0].id)  # 3 3 quality.correctness_pass_c3
assert set(dataset.labels().values()) == {0.0, 1.0}
```
