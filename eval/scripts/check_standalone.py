#!/usr/bin/env python3
"""Standalone use of bnbagent-eval: no bnbagent SDK, no BNB chain (docs/STANDALONE.md).

Run from any directory, in a venv with ``bnbagent-eval[standalone]`` and without the SDK:

    python eval/scripts/check_standalone.py

Uses the judge from ``EVAL_JUDGE_*`` when set (a real model), else a scripted judge. Starts
loopback servers only (an HTTP agent and a mock Ethereum JSON-RPC node). Exits 1 on a failure.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

from bnbagent_eval import EvalCase, EvalClient, MetricConfig, Suite
from bnbagent_eval.adapters.traces import load_trace
from bnbagent_eval.chain.attestation import LocalKeySigner, sign, verify
from bnbagent_eval.engine.dataset import load_cases
from bnbagent_eval.engine.seal import verify_hashes
from bnbagent_eval.integrations.jsonrpc.client import JsonRpcClient
from bnbagent_eval.integrations.jsonrpc.reader import JsonRpcReader
from bnbagent_eval.judges.providers.fake import FakeJudge
from bnbagent_eval.ports import ChainPorts
from bnbagent_eval.reporting import to_csv, to_markdown
from bnbagent_eval.types import Session, TargetRef, Trace, TxStep

DEV_KEY = (  # Anvil account #0: a public development key
    "0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80"  # secret-scan: allow
)
TX = "0x" + "ab" * 32
results: list[bool] = []


def check(name: str, condition: bool) -> None:
    print(("PASS " if condition else "FAIL ") + name)
    results.append(bool(condition))


def serve(handler: type[BaseHTTPRequestHandler]) -> str:
    server = HTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return f"http://127.0.0.1:{server.server_port}"


class _Json(BaseHTTPRequestHandler):
    def reply(self, body: object) -> None:
        data = json.dumps(body).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def read(self) -> object:
        return json.loads(self.rfile.read(int(self.headers["Content-Length"])))

    def log_message(self, *args: object) -> None:
        pass


class Agent(_Json):  # POST {"input": ...} -> {"output": ...}
    def do_POST(self) -> None:  # noqa: N802
        question = str(self.read()["input"])
        self.reply({"output": "BNB" if "token" in question else "I don't know."})


class EthNode(_Json):  # just enough Ethereum JSON-RPC for receipts
    def do_POST(self) -> None:  # noqa: N802
        body = self.read()
        calls = body if isinstance(body, list) else [body]
        results = {
            "eth_chainId": "0x1",
            "eth_blockNumber": "0x10",
            "eth_getTransactionReceipt": {
                "status": "0x1",
                "gasUsed": "0x5208",
                "blockNumber": "0x10",
                "logs": [],
                "transactionHash": TX,
            },
            "eth_getTransactionByHash": {
                "hash": TX,
                "to": "0x" + "12" * 20,
                "value": "0x0",
                "input": "0x",
                "gas": "0x5208",
                "blockNumber": "0x10",
            },
        }
        out = [
            {"jsonrpc": "2.0", "id": c["id"], "result": results.get(c["method"])} for c in calls
        ]
        self.reply(out if isinstance(body, list) else out[0])


OTEL_TRACE = {  # OpenTelemetry GenAI spans: one agent invocation calling one tool
    "resourceSpans": [
        {
            "scopeSpans": [
                {
                    "spans": [
                        {
                            "traceId": "c1" * 16,
                            "spanId": "d1" * 8,
                            "parentSpanId": "",
                            "name": "invoke_agent wallet-helper",
                            "startTimeUnixNano": "1700000000000000000",
                            "endTimeUnixNano": "1700000002000000000",
                            "attributes": [
                                {
                                    "key": "gen_ai.operation.name",
                                    "value": {"stringValue": "invoke_agent"},
                                },
                                {
                                    "key": "gen_ai.agent.name",
                                    "value": {"stringValue": "wallet-helper"},
                                },
                            ],
                        },
                        {
                            "traceId": "c1" * 16,
                            "spanId": "d2" * 8,
                            "parentSpanId": "d1" * 8,
                            "name": "execute_tool get_balance",
                            "startTimeUnixNano": "1700000000500000000",
                            "endTimeUnixNano": "1700000001000000000",
                            "attributes": [
                                {
                                    "key": "gen_ai.operation.name",
                                    "value": {"stringValue": "execute_tool"},
                                },
                                {
                                    "key": "gen_ai.tool.name",
                                    "value": {"stringValue": "get_balance"},
                                },
                                {"key": "gen_ai.tool.call.id", "value": {"stringValue": "call_1"}},
                            ],
                        },
                    ]
                }
            ]
        }
    ]
}


def main() -> int:
    work = Path(tempfile.mkdtemp())

    # 1. A suite and cases from files, rule metrics only.
    (work / "suite.yaml").write_text(
        "name: s\nversion: '1'\nmetrics:\n  - {id: text.contains, required: true}\n"
        "  - {id: ops.latency, value_max: 2000}\n"
    )
    (work / "cases.jsonl").write_text(
        '{"id": "a", "output": "Fees are paid in BNB.", "expected": "BNB",'
        ' "app_metrics": {"latency_ms": 800}}\n'
    )
    r = EvalClient().evaluate_sync(
        suite=Suite.load(str(work / "suite.yaml")), cases=load_cases(str(work / "cases.jsonl"))
    )
    check("rule suite from files", r.verdict.status == "pass" and verify_hashes(r))

    # 2. Judge metrics: a real model from EVAL_JUDGE_*, else a scripted judge.
    real = bool(os.environ.get("EVAL_JUDGE_PROVIDER"))

    def scripted(request):  # rates the off-topic answer low
        good = "trains" not in request.user
        return {"reasoning": "checked", "score": "Excellent" if good else "Poor"}

    suite = Suite(name="j", version="1", metrics=(MetricConfig(id="quality.relevance"),))
    question = "What is the capital of France?"
    r = EvalClient(judge=None if real else FakeJudge(scripted)).evaluate_sync(
        suite=suite,
        cases=[
            EvalCase(id="good", input=question, output="Paris is the capital of France."),
            EvalCase(id="bad", input=question, output="I like trains."),
        ],
    )
    scores = {c.case_id: c.score for c in r.results}
    label = "real judge" if real else "scripted judge"
    check(f"{label}: good > bad {scores}", scores["good"] > scores["bad"])

    # 3. A live HTTP agent as the target, two runs per case.
    r = EvalClient().evaluate_sync(
        suite=Suite(name="t", version="1", metrics=(MetricConfig(id="text.exact_match"),)),
        cases=[EvalCase(id="1", input="What is the native token?", expected="BNB")],
        target=TargetRef(kind="http", uri=serve(Agent) + "/chat"),
        options={"runs": 2},
    )
    check("HTTP agent target", r.verdict.status == "pass" and len(r.results[0].runs) == 2)

    # 4. An OpenTelemetry GenAI trace -> trajectory metrics.
    session = load_trace(OTEL_TRACE)
    r = EvalClient().evaluate_sync(
        suite=Suite(name="tr", version="1", metrics=(MetricConfig(id="agent.trajectory_stats"),)),
        cases=[EvalCase(id="t", trajectory=session)],
    )
    check("OTel GenAI trace import", r.results[0].metrics[0].status == "scored")

    # 5. Reports.
    check(
        "Markdown and CSV reports",
        "verdict" in to_markdown(r).lower() and "metric_id" in to_csv(r),
    )

    # 6. A signed, verifiable result with no chain at all (EIP-712, chain id 0).
    signed = sign(r, LocalKeySigner(DEV_KEY))
    check("attestation sign / verify", verify(signed) == signed.attestation.evaluator)

    # 7. On-chain metrics on Ethereum (chain 1) through plain JSON-RPC.
    reader = JsonRpcReader(JsonRpcClient(serve(EthNode)), chain_id=1)
    step = TxStep(id="1", chain_id=1, to="0x" + "12" * 20, tx_hash=TX)
    r = EvalClient(ports=ChainPorts(reader=reader)).evaluate_sync(
        suite=Suite(
            name="o",
            version="1",
            metrics=(
                MetricConfig(id="onchain.tx_success"),
                MetricConfig(id="onchain.gas_efficiency", value_max=50_000),
            ),
        ),
        cases=[
            EvalCase(id="tx", trajectory=Session(id="s", traces=(Trace(id="t", steps=(step,)),)))
        ],
    )
    by_id = {m.metric_id: m for m in r.results[0].metrics}
    check(
        "on-chain metrics on Ethereum via JSON-RPC",
        by_id["onchain.tx_success"].score == 1.0
        and by_id["onchain.gas_efficiency"].value == 21000,
    )

    leaked = sorted({m.split(".")[0] for m in sys.modules} & {"bnbagent", "web3"})
    check(f"bnbagent / web3 never imported {leaked}", not leaked)
    print("ALL OK" if all(results) else "FAILED")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
