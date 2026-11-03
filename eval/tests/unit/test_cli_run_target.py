"""``bnbagent-eval run --target``: evaluate a live agent from the command line."""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest
import yaml

from bnbagent_eval.cli import _parse_target, main
from bnbagent_eval.exceptions import EvalError


def echo_agent(text: str) -> str:
    return "BNB" if "token" in str(text) else "unknown"


def _files(tmp_path):
    suite = tmp_path / "suite.yaml"
    suite.write_text(
        yaml.safe_dump({"name": "t", "version": "1", "metrics": [{"id": "text.exact_match"}]})
    )
    cases = tmp_path / "cases.jsonl"
    cases.write_text(json.dumps({"id": "1", "input": "native token?", "expected": "BNB"}) + "\n")
    return str(suite), str(cases)


def test_run_with_callable_target(tmp_path, capsys) -> None:
    suite, cases = _files(tmp_path)
    rc = main(["run", "--suite", suite, "--cases", cases, "--target",
               f"callable:{__name__}:echo_agent", "--runs", "2"])  # fmt: skip
    out = json.loads(capsys.readouterr().out)
    assert rc == 0 and out["verdict"]["status"] == "pass"
    assert len(out["results"][0]["runs"]) == 2


def test_run_with_http_target(tmp_path, capsys) -> None:
    seen = {}

    class Agent(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            seen["auth"] = self.headers.get("Authorization")
            reply = json.dumps({"output": echo_agent(body["input"])}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(reply)))
            self.end_headers()
            self.wfile.write(reply)

        def log_message(self, *a) -> None:
            pass

    server = HTTPServer(("127.0.0.1", 0), Agent)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        suite, cases = _files(tmp_path)
        url = f"http://127.0.0.1:{server.server_port}/chat"
        rc = main(["run", "--suite", suite, "--cases", cases, "--target", url,
                   "--target-header", "Authorization: Bearer test"])  # fmt: skip
    finally:
        server.shutdown()
    assert rc == 0 and json.loads(capsys.readouterr().out)["verdict"]["status"] == "pass"
    assert seen["auth"] == "Bearer test"


def test_parse_target_forms() -> None:
    assert _parse_target("https://agent.example.com/a2a").kind == "http"
    a2a = _parse_target("a2a:https://agent.example.com/.well-known/agent-card.json")
    assert a2a.kind == "a2a" and a2a.uri.startswith("https://")
    mcp = _parse_target("mcp:ask@https://mcp.example.com/mcp")
    assert (mcp.kind, mcp.uri, mcp.config["tool"]) == ("mcp", "https://mcp.example.com/mcp", "ask")
    for bad in ("ftp://x", "mcp:https://x", "nope"):
        with pytest.raises(EvalError):
            _parse_target(bad)
    with pytest.raises(EvalError):
        _parse_target("https://x", ["no-colon"])


def test_bad_target_is_a_usage_error(tmp_path, capsys) -> None:
    suite, cases = _files(tmp_path)
    assert main(["run", "--suite", suite, "--cases", cases, "--target", "nope"]) == 64
