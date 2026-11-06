import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

import pytest

from bnbagent.erc8004.agent import ERC8004Agent
from bnbagent.utils import public_http


def test_indexer_stream_is_rejected_at_one_mib(monkeypatch):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Length", str(1024 * 1024 + 1))
            self.end_headers()

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    agent = object.__new__(ERC8004Agent)
    agent._network_config = {"chain_id": 97}
    monkeypatch.setattr(public_http, "_resolve", lambda *_: "127.0.0.1")
    try:
        with patch(
            "bnbagent.erc8004.agent.SCAN_API_URL", f"http://indexer.example:{server.server_port}"
        ):
            with pytest.raises(ConnectionError):
                agent.get_all_agents()
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=2)
