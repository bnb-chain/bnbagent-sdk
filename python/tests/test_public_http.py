"""SRC-1659/1635: policy and real bounded HTTP/TLS transport, all offline."""

import importlib.util
import ssl
import threading
import time
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

import pytest

from bnbagent.utils import public_http as http


@pytest.mark.parametrize(
    "ip",
    [
        "::",
        "::1",
        "fe80::1",
        "fc00::1",
        "ff02::1",
        "100::1",
        "2001:db8::1",
        "2001::1",
        "2002:7f00:1::",
        "3fff::1",
        "64:ff9b::7f00:1",
        "64:ff9b:1::1",
        "::ffff:127.0.0.1",
        "::ffff:7f00:1",
        "fe80::1%lo0",
        "127.0.0.1",
        "10.0.0.1",
        "172.16.0.1",
        "192.168.0.1",
        "169.254.169.254",
        "100.100.100.200",
        "0.0.0.0",
        "224.0.0.1",
        "192.88.99.1",
        "198.18.0.1",
        "240.0.0.1",
        "invalid",
    ],
)
def test_special_addresses_are_blocked(ip):
    assert not http.is_public_ip(ip)


@pytest.mark.parametrize(
    "ip", ["8.8.8.8", "93.184.216.34", "2606:4700:4700::1111", "::ffff:808:808"]
)
def test_public_addresses_are_allowed(ip):
    assert http.is_public_ip(ip)


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/",
        "http://[::]/",
        "http://[::ffff:7f00:1]/",
        "http://user:pass@8.8.8.8/",
        "file:///etc/passwd",
        "http://metadata.google.internal./",
        "http://8.8.8.8:0/",
        "http://8.8.8.8/\r\nx: y",
        "http://8.8.8.8\\@127.0.0.1/",
    ],
)
def test_rejected_urls_do_not_open_a_socket(url):
    with patch.object(http.socket, "socket") as socket:
        with pytest.raises(http.PublicHttpError):
            http.fetch_public_json(url)
        socket.assert_not_called()


def test_mixed_dns_answers_never_open_a_socket():
    answers = [(2, 1, 6, "", (ip, 80)) for ip in ["8.8.8.8", "127.0.0.1"]]
    with (
        patch.object(http.socket, "getaddrinfo", return_value=answers),
        patch.object(http.socket, "socket") as socket,
    ):
        with pytest.raises(http.PublicHttpError):
            http.fetch_public_json("http://agent.example/")
        socket.assert_not_called()


@pytest.fixture
def local_server():
    seen = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_GET(self):
            seen.append((self.path, self.headers.get("Host")))
            if self.path == "/slow":
                self.connection.sendall(b"HTTP/1.1 200 OK\r\nX-Slow: ")
                for _ in range(100):
                    time.sleep(0.02)
                    try:
                        self.connection.sendall(b"a")
                    except OSError:
                        break
                return
            self.send_response(302 if self.path == "/redirect" else 200)
            if self.path == "/redirect":
                self.send_header("Location", "/secret")
            if self.path == "/encoded":
                self.send_header("Content-Encoding", "gzip")
            body = b"x" * 100 if self.path == "/large" else b'{"name":"local agent"}'
            if self.path != "/large":
                self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server, seen
    server.shutdown()
    server.server_close()
    thread.join(timeout=2)


def test_real_http_uses_pinned_address_and_host(local_server, monkeypatch):
    server, seen = local_server
    # Inject only the already-validated address, so transport reaches our
    # isolated server; the policy itself is tested separately above.
    monkeypatch.setattr(http, "_resolve", lambda *_: "127.0.0.1")
    port = server.server_port
    assert http.fetch_public_json(f"http://agent.example:{port}/") == {"name": "local agent"}
    assert seen == [("/", f"agent.example:{port}")]


@pytest.mark.parametrize("path", ["redirect", "large", "encoded", "slow"])
def test_real_http_refuses_redirect_size_encoding_and_slow_headers(
    local_server, monkeypatch, path
):
    server, seen = local_server
    monkeypatch.setattr(http, "_resolve", lambda *_: "127.0.0.1")
    start = time.monotonic()
    with pytest.raises(http.PublicHttpError):
        http.fetch_public_json(
            f"http://agent.example:{server.server_port}/{path}", max_bytes=32, timeout=0.15
        )
    assert time.monotonic() - start < 1
    assert len(seen) == 1


def test_tls_verifies_original_hostname(local_server, monkeypatch, tmp_path):
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    server, seen = local_server
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "agent.example")])
    now = datetime.now(timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=1))
        .not_valid_after(now + timedelta(days=1))
        .add_extension(
            x509.SubjectAlternativeName([x509.DNSName("agent.example")]), critical=False
        )
        .sign(key, hashes.SHA256())
    )
    certfile, keyfile = tmp_path / "cert.pem", tmp_path / "key.pem"
    certfile.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    keyfile.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(certfile, keyfile)
    server.socket = context.wrap_socket(server.socket, server_side=True)
    trusted = ssl.create_default_context(cafile=str(certfile))
    monkeypatch.setattr(http.ssl, "create_default_context", lambda: trusted)
    monkeypatch.setattr(http, "_resolve", lambda *_: "127.0.0.1")
    url = f"https://agent.example:{server.server_port}/"
    assert http.fetch_public_json(url)["name"] == "local agent"
    with pytest.raises(http.PublicHttpError):
        http.fetch_public_json(url.replace("agent.example", "wrong.example"))
    assert len(seen) == 1


@pytest.mark.parametrize(
    "cid", ["../secret", "//localhost", "valid?redirect=localhost", "%2f..", "x" * 129]
)
def test_ipfs_rejects_non_cid_paths(cid):
    with pytest.raises(http.PublicHttpError):
        http.public_gateway_url(f"ipfs://{cid}", "https://gateway.example/ipfs/")


def test_valid_ipfs_url():
    cid = "Qm" + "a" * 44
    assert (
        http.public_gateway_url(f"ipfs://{cid}", "https://gateway.example/ipfs/")
        == f"https://gateway.example/ipfs/{cid}"
    )


def test_voter_refuses_private_url_and_can_fetch_next_manifest():
    path = Path(__file__).parents[1] / "examples/voter/watch.py"
    spec = importlib.util.spec_from_file_location("voter_watch", path)
    voter = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(voter)
    with patch.object(http.socket, "socket") as socket:
        assert voter.fetch_manifest("http://127.0.0.1/", "https://gateway.example/ipfs") is None
        socket.assert_not_called()
    with patch.object(voter, "fetch_public_json", return_value={}) as fetch:
        voter.fetch_manifest("ipfs://" + "Qm" + "a" * 44, "https://gateway.example/ipfs/")
        fetch.assert_called_once()
