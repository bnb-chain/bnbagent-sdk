"""Exercise disputed-job downloads before the operator prompt, without RPC."""

import importlib.util
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest


@pytest.fixture
def voter():
    path = Path(__file__).parents[1] / "examples/voter/watch.py"
    spec = importlib.util.spec_from_file_location("voter_watch_security", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def loopback_service():
    payload = {
        "version": 1,
        "job_id": 777,
        "chain_id": 97,
        "contracts": {},
        "response": {"content": "test-only-loopback-content"},
        "metadata": {},
    }
    hits = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            hits.append(self.path)
            if self.path == "/redirect":
                self.send_response(302)
                self.send_header("Location", "/manifest")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            body = b"not json" if self.path == "/invalid" else json.dumps(payload).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server.server_port, hits, payload
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


@pytest.mark.parametrize("path", ["/manifest", "/invalid", "/redirect"])
@pytest.mark.parametrize("choice", ["s", "r"])
def test_disputed_job_never_fetches_loopback_before_operator_choice(
    voter, loopback_service, monkeypatch, capsys, path, choice
):
    port, hits, payload = loopback_service
    manifest_hash = voter.DeliverableManifest.from_dict(payload).manifest_hash()
    client = SimpleNamespace(
        policy=SimpleNamespace(
            has_voted=Mock(return_value=False),
            dispute_window=Mock(return_value=3600),
        ),
        get_job=Mock(
            return_value=SimpleNamespace(
                client="client",
                provider="provider",
                budget=1,
                status=SimpleNamespace(name="Submitted"),
                deliverable=manifest_hash,
                submitted_at=int(time.time()) - 60,
            )
        ),
        get_deliverable_url=Mock(return_value=f"http://127.0.0.1:{port}{path}"),
        vote_reject=Mock(),
    )
    hits_at_prompt = []

    def respond(_prompt):
        hits_at_prompt.append(list(hits))
        return choice

    monkeypatch.setattr("builtins.input", respond)
    gateway = "https://gateway.example/ipfs/"
    # The refused download counts as a failed one: retried, then put to a vote.
    for failures in range(voter.MANIFEST_DOWNLOAD_ATTEMPTS - 1):
        with pytest.raises(voter.ManifestUnavailable):
            voter.handle_disputed_job(client, 777, "voter", gateway, failures)
    voter.handle_disputed_job(client, 777, "voter", gateway, voter.MANIFEST_DOWNLOAD_ATTEMPTS - 1)

    assert hits_at_prompt == [[]]
    assert hits == []
    output = capsys.readouterr().out
    assert "deliverable unavailable: could not fetch manifest" in output
    assert "test-only-loopback-content" not in output
    if choice == "r":
        client.vote_reject.assert_called_once_with(777)
    else:
        client.vote_reject.assert_not_called()
        assert "skipped job 777" in output
