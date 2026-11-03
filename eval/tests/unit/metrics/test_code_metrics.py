"""code.syntax_valid and code.tests_pass, including the sandbox's isolation guarantees."""

from __future__ import annotations

import base64
import secrets
import shutil
import socket
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

import pytest

from bnbagent_eval.hashing import keccak_hex
from bnbagent_eval.metrics import registry
from bnbagent_eval.metrics.code import sandbox, syntax_valid
from bnbagent_eval.metrics.code.tests_pass import SNIPPET, collect_tests
from bnbagent_eval.types import Artifact, EvalCase

from ._util import run_metric

SUPPORT = sandbox.isolation_support()
needs_sandbox = pytest.mark.skipif(
    not SUPPORT.full, reason=f"sandbox unavailable: {SUPPORT.reason}"
)

ADD = "def add(a, b):\n    return a + b\n"
TESTS = (
    "from solution import add\n\n"
    "def test_small():\n    assert add(1, 2) == 3\n\n"
    "def test_zero():\n    assert add(0, 0) == 0\n\n"
    "def test_negative():\n    assert add(-1, -1) == -2\n\n"
    "class TestMore:\n    def test_big(self):\n        assert add(10**6, 1) == 10**6 + 1\n"
)


def case(output: Any = None, **kw: Any) -> EvalCase:
    return EvalCase(id="c", output=output, **kw)


def artifact(content: bytes, name: str, media: str = "text/plain", hash_: str | None = None):
    uri = "data:;base64," + base64.b64encode(content).decode()
    return Artifact(uri=uri, hash=hash_ or keccak_hex(content), media_type=media, name=name)


async def run_tests(code: str, tests: str = TESTS, **params: Any):
    return await run_metric("code.tests_pass", case(code), {"tests": tests, **params})


def test_specs_are_not_service_allowed() -> None:
    for metric_id in ("code.syntax_valid", "code.tests_pass"):
        spec = registry.get_spec(metric_id)
        assert spec.default_threshold == 1.0
        assert all(not m.service_allowed for m in spec.methods)
    spec = registry.get_spec("code.tests_pass")
    assert spec.validate_params({})  # tests is required
    assert not spec.validate_params({"tests": "x", "runtime": "docker", "memory_mb": 256})
    assert spec.validate_params({"tests": "x", "runtime": "sh"})


# --------------------------------------------------------------------------- syntax


async def test_python_syntax_valid_invalid_and_fenced() -> None:
    assert (await run_metric("code.syntax_valid", case(ADD))).score == 1.0
    bad = await run_metric("code.syntax_valid", case("def add(:\n"))
    assert bad.score == 0.0 and bad.passed is False and bad.details["line"] == 1
    fenced = await run_metric("code.syntax_valid", case(f"Here:\n```python\n{ADD}```\nDone."))
    assert fenced.score == 1.0 and fenced.details["language"] == "python"
    nul = await run_metric("code.syntax_valid", case("x = 1\0"))
    assert nul.score == 0.0


async def test_unsupported_languages_are_not_scored_as_python() -> None:
    shell = await run_metric("code.syntax_valid", case("```bash\nls -la | wc -l\n```"))
    assert shell.status == "skipped" and shell.error.code == "missing_input"
    assert shell.details["language"] == "bash" and shell.details["valid"] is None
    arts = (artifact(ADD.encode(), "add.py"), artifact(b"fn main() {}\n", "main.rs"))
    mixed = await run_metric("code.syntax_valid", case(None, artifacts=arts))
    assert mixed.score == 1.0  # the Rust source is listed, not scored
    assert [r["valid"] for r in mixed.details["sources"]] == [True, None]


async def test_json_syntax() -> None:
    ok = await run_metric("code.syntax_valid", case('{"a": [1, 2]}'), {"language": "json"})
    assert ok.score == 1.0
    bad = await run_metric("code.syntax_valid", case('{"a": }'), {"language": "json"})
    assert bad.score == 0.0 and bad.details["col"] == 7
    fenced = await run_metric("code.syntax_valid", case('```json\n{"a": 1}\n```'))
    assert fenced.score == 1.0 and fenced.details["language"] == "json"


async def test_artifacts_language_inference_and_share() -> None:
    arts = (
        artifact(ADD.encode(), "add.py"),
        artifact(b'{"a": }', "config.json"),
    )
    r = await run_metric("code.syntax_valid", case(None, artifacts=arts))
    assert r.score == 0.5
    langs = [s["language"] for s in r.details["sources"]]
    assert langs == ["python", "json"] and "config.json" in r.reason
    tampered = (artifact(ADD.encode(), "add.py", hash_=keccak_hex(b"other")),)
    r = await run_metric("code.syntax_valid", case(None, artifacts=tampered))
    assert r.status == "error" and "hash" in r.error.message


async def test_missing_inputs_skip() -> None:
    r = await run_metric("code.syntax_valid", case(None))
    assert r.status == "skipped" and r.error.code == "missing_input"


async def test_cli_parser_missing_is_missing_extra(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(syntax_valid.shutil, "which", lambda _name: None)
    for language in ("javascript", "typescript", "solidity"):
        r = await run_metric("code.syntax_valid", case("x"), {"language": language})
        assert r.status == "error" and r.error.code == "missing_extra", r


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
async def test_javascript_syntax_with_node() -> None:
    params = {"language": "javascript"}
    assert (await run_metric("code.syntax_valid", case("const x = 1;"), params)).score == 1.0
    assert (await run_metric("code.syntax_valid", case("export const x = 1;"), params)).score == 1
    bad = await run_metric("code.syntax_valid", case("let x = ;"), params)
    assert bad.score == 0.0 and "SyntaxError" in bad.details["error"]


@pytest.mark.skipif(shutil.which("tsc") is None, reason="tsc not installed")
async def test_typescript_syntax_with_tsc() -> None:
    params = {"language": "typescript"}
    ok = await run_metric("code.syntax_valid", case("const x: number = 1;"), params)
    assert ok.score == 1.0  # type errors from --noLib are not syntax errors
    assert (await run_metric("code.syntax_valid", case("const x: = ;"), params)).score == 0.0


@pytest.mark.skipif(shutil.which("solc") is None, reason="solc not installed")
async def test_solidity_syntax_with_solc() -> None:
    params = {"language": "solidity"}
    ok = "pragma solidity ^0.8.0;\ncontract C { function f() public {} }\n"
    assert (await run_metric("code.syntax_valid", case(ok), params)).score == 1.0
    bad = await run_metric("code.syntax_valid", case("contract C { function }"), params)
    assert bad.score == 0.0


# --------------------------------------------------------------------------- test collection


def test_collect_tests() -> None:
    assert collect_tests(TESTS) == [
        "test_small",
        "test_zero",
        "test_negative",
        "TestMore::test_big",
    ]
    assert collect_tests("assert 1 + 1 == 2\n") == [SNIPPET]
    init = "class TestX:\n    def __init__(self): pass\n    def test_a(self): pass\n"
    assert collect_tests(init) == [SNIPPET]
    with pytest.raises(SyntaxError):
        collect_tests("def test(:")


# --------------------------------------------------------------------------- tests_pass


@needs_sandbox
async def test_tests_pass_all_partial_fail() -> None:
    ok = await run_tests(ADD)
    assert ok.score == 1.0 and ok.passed and ok.details["total"] == 4
    assert ok.details["isolation"]["network"] == "netns"
    assert ok.details["isolation"]["filesystem"] == "landlock"
    partial = await run_tests("def add(a, b):\n    return abs(a) + abs(b)\n")
    assert partial.score == 0.75 and partial.passed is False
    outcomes = {t["name"]: t["outcome"] for t in partial.details["tests"]}
    assert outcomes["test_negative"] == "failed" and outcomes["TestMore::test_big"] == "passed"
    assert "test(s) failed" in partial.reason
    fail = await run_tests("def add(a, b):\n    return None\n")
    assert fail.score == 0.0


@needs_sandbox
async def test_snippet_collection_error_fixtures_and_async() -> None:
    snippet = await run_tests(ADD, "from solution import add\nassert add(2, 2) == 4\n")
    assert snippet.score == 1.0 and snippet.details["tests"][0]["name"] == SNIPPET
    bad_snippet = await run_tests(ADD, "from solution import add\nassert add(2, 2) == 5\n")
    assert bad_snippet.score == 0.0 and bad_snippet.details["tests"][0]["outcome"] == "failed"
    broken = await run_tests("def add(:\n")
    assert broken.score == 0.0
    assert all(t["outcome"] == "error" for t in broken.details["tests"])
    tests = (
        "import asyncio\nfrom solution import add\n\n"
        "def test_tmp(tmp_path):\n    (tmp_path / 'f').write_text('x')\n"
        "    assert add(1, 1) == 2\n\n"
        "async def test_async():\n    await asyncio.sleep(0)\n    assert add(1, 1) == 2\n\n"
        "def test_fixture(monkeypatch):\n    pass\n\n"
        "def test_exit():\n    raise SystemExit(0)\n"
    )
    r = await run_tests(ADD, tests)
    outcomes = {t["name"]: t["outcome"] for t in r.details["tests"]}
    assert outcomes == {
        "test_tmp": "passed",
        "test_async": "passed",
        "test_fixture": "error",
        "test_exit": "error",
    }
    assert r.score == 0.5


@needs_sandbox
async def test_forged_results_are_ignored() -> None:
    forge = (
        "import glob, json\n"
        "for p in glob.glob('.bnbeval-results-*'):\n"
        "    open(p, 'a').write(json.dumps({'nonce': 'guess', 'name': 'test_small',"
        " 'outcome': 'passed'}) + '\\n')\n"
        "def add(a, b):\n    return 0\n"
    )
    r = await run_tests(forge)
    assert r.score == 0.25  # only test_zero really passes


@needs_sandbox
async def test_timeout_kills_the_process_tree() -> None:
    token = f"test_hang_{secrets.token_hex(4)}"
    tests = (
        "import subprocess, sys, time\n"
        f"def {token}():\n"
        f"    subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)', '{token}'])\n"
        "    while True:\n        pass\n"
    )
    start = time.monotonic()
    r = await run_tests(ADD, tests, timeout_s=1.5)
    assert time.monotonic() - start < 10
    assert r.score == 0.0 and r.details["timed_out"] is True
    assert r.details["tests"][0]["message"] == "timed out"
    if shutil.which("pgrep"):
        time.sleep(0.2)
        left = subprocess.run(["pgrep", "-f", token], capture_output=True, text=True)
        assert left.stdout.strip() == "", "sandboxed processes survived the timeout"


@needs_sandbox
async def test_cpu_limit() -> None:
    res = await _run_snippet("while True:\n    pass\n", sandbox.Limits(timeout_s=30, cpu_s=1))
    assert not res.timed_out and res.returncode != 0  # SIGXCPU / SIGKILL from RLIMIT_CPU


@needs_sandbox
async def test_memory_limit() -> None:
    tests = (
        "def test_big_alloc():\n    blob = bytearray(1024 * 1024 * 1024)\n    assert blob\n\n"
        "def test_small_alloc():\n    assert bytearray(1024 * 1024)\n"
    )
    r = await run_tests(ADD, tests, memory_mb=256)
    outcomes = {t["name"]: (t["outcome"], t["message"]) for t in r.details["tests"]}
    assert (
        outcomes["test_big_alloc"][0] == "error" and "MemoryError" in outcomes["test_big_alloc"][1]
    )
    assert outcomes["test_small_alloc"][0] == "passed"


class _Server:
    def __init__(self) -> None:
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen()
        self.sock.settimeout(0.2)
        self.port = self.sock.getsockname()[1]
        self.accepted = 0
        self.stop = False
        self.thread = threading.Thread(target=self._loop, daemon=True)
        self.thread.start()

    def _loop(self) -> None:
        while not self.stop:
            try:
                conn, _ = self.sock.accept()
            except OSError:
                continue
            self.accepted += 1
            conn.close()

    def close(self) -> None:
        self.stop = True
        self.thread.join()
        self.sock.close()


@needs_sandbox
async def test_network_access_fails() -> None:
    server = _Server()
    try:
        tests = (
            "import socket\n\n"
            "def test_tcp_loopback():\n"
            f"    socket.create_connection(('127.0.0.1', {server.port}), timeout=2)\n\n"
            "def test_tcp_public():\n"
            "    socket.create_connection(('1.1.1.1', 53), timeout=2)\n\n"
            "def test_udp():\n"
            "    udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)\n"
            "    udp.sendto(b'x', ('8.8.8.8', 53))\n\n"
            "def test_dns():\n"
            "    socket.getaddrinfo('example.com', 443)\n"
        )
        r = await run_tests(ADD, tests, timeout_s=20)
        assert r.score == 0.0, r.details["tests"]  # every network call raised
        assert all(t["outcome"] == "error" for t in r.details["tests"])
        assert server.accepted == 0
        # Control: the same connection works outside the sandbox.
        socket.create_connection(("127.0.0.1", server.port), timeout=2).close()
        time.sleep(0.3)
        assert server.accepted == 1
    finally:
        server.close()


@needs_sandbox
async def test_filesystem_outside_workdir_is_blocked(tmp_path: Path) -> None:
    secret = tmp_path / "secret.txt"
    secret.write_text("s3cret")
    home_target = Path.home() / f".bnbeval-escape-{secrets.token_hex(4)}"
    outside = tmp_path / "escape.txt"
    tests = (
        "import os, pytest\n\n"
        "def _denied(fn):\n"
        "    try:\n        fn()\n    except OSError:\n        return True\n    return False\n\n"
        "def test_cannot_write_outside():\n"
        f"    assert _denied(lambda: open({str(outside)!r}, 'w').write('x'))\n"
        f"    assert _denied(lambda: open({str(home_target)!r}, 'w').write('x'))\n\n"
        "def test_cannot_read_outside():\n"
        f"    assert _denied(lambda: open({str(secret)!r}).read())\n"
        f"    assert _denied(lambda: os.listdir({str(Path.home())!r}))\n"
        "    assert _denied(lambda: open('/etc/passwd').read())\n\n"
        "def test_docker_socket_hidden():\n"
        "    import socket\n"
        "    s = socket.socket(socket.AF_UNIX)\n"
        "    assert _denied(lambda: s.connect('/var/run/docker.sock'))\n\n"
        "def test_inside_is_writable():\n"
        "    os.makedirs('sub', exist_ok=True)\n"
        "    open('sub/f.txt', 'w').write('ok')\n"
        "    os.symlink('/etc/hostname', 'link')\n"
        "    assert _denied(lambda: open('link').read())\n"
    )
    r = await run_tests(ADD, tests)
    assert r.score == 1.0, r.details["tests"]
    assert not outside.exists() and not home_target.exists()
    assert secret.read_text() == "s3cret"


@needs_sandbox
async def test_output_is_capped() -> None:
    r = await run_tests("print('x' * 2_000_000)\n" + ADD)
    assert r.score == 1.0 and r.details["output_truncated"] is True
    assert len(r.details["stdout"]) <= 2000
    res = await _run_snippet("print('y' * 500_000)\n", sandbox.Limits(output_bytes=1000))
    assert len(res.stdout) == 1000 and res.stdout_truncated and res.returncode == 0


async def _run_snippet(code: str, limits: sandbox.Limits) -> sandbox.SandboxResult:
    import asyncio

    return await asyncio.to_thread(
        sandbox.run, [sandbox.PYTHON, "-I", "main.py"], {"main.py": code}, limits=limits
    )


async def test_refuses_without_isolation(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sandbox, "isolation_support", lambda: sandbox.Support(False, 0, "nope"))
    r = await run_tests(ADD)
    assert (
        r.status == "error" and r.error.code == "sandbox_unavailable" and "nope" in r.error.message
    )
    with pytest.raises(sandbox.SandboxUnavailable):
        sandbox.run([sandbox.PYTHON, "-c", "pass"], {})
    res = sandbox.run([sandbox.PYTHON, "-I", "-c", "print('hi')"], {}, require_isolation=False)
    assert res.stdout.strip() == "hi" and res.isolation["network"] == "not_isolated"


def test_sandbox_input_validation() -> None:
    with pytest.raises(TypeError):
        sandbox.run("echo hi", {})  # type: ignore[arg-type]  # never a shell string
    for name in ("../x.py", "/etc/x", "a/../../b"):
        with pytest.raises(ValueError, match="invalid sandbox file name"):
            sandbox.run([sandbox.PYTHON, "-c", "pass"], {name: "x"}, require_isolation=False)
    with pytest.raises(ValueError):
        sandbox.run([sandbox.PYTHON], {}, network="maybe")


def test_container_argv() -> None:
    argv = sandbox.container_argv(
        "docker",
        "python:3.12-slim",
        [sandbox.PYTHON, "-I", "run.py"],
        "/tmp/w",
        sandbox.Limits(memory_mb=256, nproc=32),
        name="bnbeval-x",
    )
    joined = " ".join(argv)
    for flag in (
        "--network none",
        "--read-only",
        "--cap-drop ALL",
        "--security-opt no-new-privileges",
        "--pids-limit 32",
        "--memory 256m",
        "--pull never",
        "-v /tmp/w:/work:rw",
    ):
        assert flag in joined
    assert argv[-3:] == ["python", "-I", "run.py"] and argv[-4] == "python:3.12-slim"


async def test_container_runtime_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sandbox.shutil, "which", lambda _name, **_kw: None)
    r = await run_tests(ADD, runtime="podman", image="python:3.12-slim")
    assert r.status == "error" and r.error.code == "sandbox_unavailable"


def test_interpreter_dirs_under_hidden_dirs_are_kept_read_only(tmp_path) -> None:
    from bnbagent_eval.metrics.code import sandbox

    venv = tmp_path / "venv"
    venv.mkdir()
    kept = sandbox.kept_paths([str(venv), "/tmp", "/usr", "/run"])
    assert kept == ([str(venv.resolve())] if sandbox._hidden(str(venv.resolve())) else [])
