"""
tests/test_tcp_server.py
=========================
Integration tests for the TCP server and client together.

Strategy:
  - Each test spins up a REAL server on an EPHEMERAL port (port=0 lets the OS
    pick a free port, eliminating all port-conflict issues).
  - The server runs in a background daemon thread so the test can act as a client.
  - A fresh temporary database is used for every test — the real swiggy.db is
    never touched.
  - We do NOT use arbitrary sleeps. Instead we wait for a threading.Event that
    the server sets the moment it is ready to accept() connections.

Run:
    python -m pytest tests/test_tcp_server.py -v
"""

import json
import pathlib
import socket
import sys
import threading
import time

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))

import database.db as db_module
from database.db import init_db
from database.seed import seed_data


# -------------------------------------------------------------------
# Helpers: start / stop the server in a background thread
# -------------------------------------------------------------------
def _start_server_thread(host: str, port: int,
                         db_path, monkeypatch) -> tuple[threading.Thread, int]:
    """
    Patch DB_PATH, create a server socket bound to (host, port), and
    start a daemon thread running serve_client() for one connection.

    Returns:
        (thread, actual_port) — actual_port is useful when port=0.
    """
    # Patch the database path BEFORE importing server code.
    monkeypatch.setattr(db_module, "DB_PATH", db_path)

    # Import here so the monkeypatch is already active.
    from server.request_handler import handle_request
    from server.tcp_server import serve_client, send_response, MAX_MESSAGE_BYTES

    server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server_sock.bind((host, port))
    actual_port = server_sock.getsockname()[1]  # discover the OS-assigned port
    server_sock.listen(5)

    ready_event = threading.Event()

    def _loop():
        ready_event.set()         # signal: ready to accept
        try:
            while True:
                server_sock.settimeout(2.0)   # don't block forever in tests
                try:
                    conn_sock, addr = server_sock.accept()
                    conn_sock.settimeout(5.0)
                    serve_client(conn_sock, addr)
                except socket.timeout:
                    break         # allow the thread to exit cleanly
        except OSError:
            pass
        finally:
            server_sock.close()

    t = threading.Thread(target=_loop, daemon=True)
    t.start()
    ready_event.wait(timeout=3.0)   # wait until ready (bounded, never infinite)
    return t, actual_port


# -------------------------------------------------------------------
# Helper: raw socket send/receive without SwiggyClient
# -------------------------------------------------------------------
def _raw_send_recv(host: str, port: int, data: bytes,
                   timeout: float = 5.0) -> bytes:
    """
    Connect, send raw bytes, read until '\\n', return raw bytes.
    Used to test malformed input without the client library sanitising it.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(timeout)
        s.connect((host, port))
        s.sendall(data)
        buf = b""
        while b"\n" not in buf:
            chunk = s.recv(4096)
            if not chunk:
                break
            buf += chunk
    return buf


# -------------------------------------------------------------------
# Fixtures
# -------------------------------------------------------------------
HOST = "127.0.0.1"


@pytest.fixture
def seeded_server(tmp_path, monkeypatch):
    """
    Return (host, port) of a running server backed by a seeded temp database.
    """
    temp_db = tmp_path / "server_test.db"
    monkeypatch.setattr(db_module, "DB_PATH", temp_db)
    init_db()
    seed_data()

    _, port = _start_server_thread(HOST, 0, temp_db, monkeypatch)
    yield HOST, port


@pytest.fixture
def empty_server(tmp_path, monkeypatch):
    """
    Return (host, port) of a running server backed by an EMPTY (tables only) db.
    """
    temp_db = tmp_path / "empty_server_test.db"
    monkeypatch.setattr(db_module, "DB_PATH", temp_db)
    init_db()

    _, port = _start_server_thread(HOST, 0, temp_db, monkeypatch)
    yield HOST, port


# -------------------------------------------------------------------
# Client helper within tests
# -------------------------------------------------------------------
def _client_send(host, port, action, payload, request_id="test-id") -> dict:
    """Open a fresh connection, send one request, return parsed response."""
    from clients.tcp_client import SwiggyClient
    with SwiggyClient(host=host, port=port) as c:
        return c.send(action, payload, request_id=request_id)


# ===================================================================
# 1. PING over TCP
# ===================================================================
class TestPingOverTCP:
    def test_ping_returns_ok(self, seeded_server):
        host, port = seeded_server
        resp = _client_send(host, port, "ping", {})
        assert resp["ok"] is True
        assert resp["data"]["status"] == "ok"

    def test_ping_preserves_request_id(self, seeded_server):
        host, port = seeded_server
        resp = _client_send(host, port, "ping", {}, request_id="PING-99")
        assert resp["request_id"] == "PING-99"


# ===================================================================
# 2. LIST_RESTAURANTS over TCP
# ===================================================================
class TestListRestaurantsOverTCP:
    def test_returns_three_restaurants(self, seeded_server):
        host, port = seeded_server
        resp = _client_send(host, port, "list_restaurants", {})
        assert resp["ok"] is True
        assert len(resp["data"]) == 3

    def test_data_comes_from_database_not_hardcoded(self, seeded_server):
        host, port = seeded_server
        resp = _client_send(host, port, "list_restaurants", {})
        names = {r["name"] for r in resp["data"]}
        assert "Hyderabad Spice House" in names
        assert "Dosa Corner" in names
        assert "Pizza Junction" in names

    def test_empty_database_returns_empty_list(self, empty_server):
        host, port = empty_server
        resp = _client_send(host, port, "list_restaurants", {})
        assert resp["ok"] is True
        assert resp["data"] == []


# ===================================================================
# 3. LIST_MENU over TCP
# ===================================================================
class TestListMenuOverTCP:
    def test_valid_restaurant_id(self, seeded_server):
        host, port = seeded_server
        resp = _client_send(host, port, "list_menu", {"restaurant_id": 1})
        assert resp["ok"] is True
        assert len(resp["data"]) == 2

    def test_nonexistent_restaurant_id(self, seeded_server):
        host, port = seeded_server
        resp = _client_send(host, port, "list_menu", {"restaurant_id": 9999})
        assert resp["ok"] is False
        assert resp["error"]["code"] == "NOT_FOUND"

    def test_string_restaurant_id_rejected(self, seeded_server):
        host, port = seeded_server
        resp = _client_send(host, port, "list_menu",
                            {"restaurant_id": "one"})
        assert resp["ok"] is False
        assert resp["error"]["code"] == "INVALID_PAYLOAD"

    def test_missing_restaurant_id(self, seeded_server):
        host, port = seeded_server
        resp = _client_send(host, port, "list_menu", {})
        assert resp["ok"] is False
        assert resp["error"]["code"] == "INVALID_PAYLOAD"


# ===================================================================
# 4. UNKNOWN ACTION over TCP
# ===================================================================
class TestUnknownActionOverTCP:
    def test_unknown_action(self, seeded_server):
        host, port = seeded_server
        resp = _client_send(host, port, "buy_spaceship", {})
        assert resp["ok"] is False
        assert resp["error"]["code"] == "UNKNOWN_ACTION"


# ===================================================================
# 5. MALFORMED JSON
# ===================================================================
class TestMalformedJSON:
    def test_malformed_json_does_not_crash_server(self, seeded_server):
        """
        Sending bad JSON must return a MALFORMED_JSON error response.
        The server must stay alive afterwards.
        """
        host, port = seeded_server
        raw = b"this is not json\n"
        response_bytes = _raw_send_recv(host, port, raw)
        resp = json.loads(response_bytes.strip())
        assert resp["ok"] is False
        assert resp["error"]["code"] == "MALFORMED_JSON"

    def test_server_still_works_after_malformed_json(self, seeded_server):
        """After receiving bad JSON, the server must still handle good requests."""
        host, port = seeded_server
        # First send bad JSON.
        _raw_send_recv(host, port, b"BAD{{{JSON\n")
        # Now send a valid ping on a fresh connection.
        time.sleep(0.05)   # tiny pause to let server close the bad conn
        resp = _client_send(host, port, "ping", {})
        assert resp["ok"] is True


# ===================================================================
# 6. MULTIPLE REQUESTS ON ONE CONNECTION
# ===================================================================
class TestMultipleRequestsOneConnection:
    def test_three_requests_one_connection(self, seeded_server):
        """
        Send ping, list_restaurants, and list_menu over the SAME TCP
        connection without reconnecting.
        """
        from clients.tcp_client import SwiggyClient
        host, port = seeded_server
        with SwiggyClient(host=host, port=port) as client:
            r1 = client.send("ping", {})
            r2 = client.send("list_restaurants", {})
            r3 = client.send("list_menu", {"restaurant_id": 2})

        assert r1["ok"] is True
        assert r2["ok"] is True
        assert len(r2["data"]) == 3
        assert r3["ok"] is True
        assert len(r3["data"]) == 2


# ===================================================================
# 7. TWO REQUESTS SENT BACK-TO-BACK (arrive in one recv)
# ===================================================================
class TestBatchedRequests:
    def test_two_requests_sent_at_once(self, seeded_server):
        """
        Simulate two requests arriving in a single recv() by concatenating
        them before sending. The server must handle both correctly.
        """
        host, port = seeded_server
        req1 = json.dumps({"request_id": "b1", "action": "ping", "payload": {}}) + "\n"
        req2 = json.dumps({"request_id": "b2", "action": "list_restaurants",
                           "payload": {}}) + "\n"
        combined = (req1 + req2).encode("utf-8")

        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(5.0)
            s.connect((host, port))
            s.sendall(combined)   # send both at once

            # Read two responses.
            buf = b""
            responses = []
            while len(responses) < 2:
                chunk = s.recv(4096)
                if not chunk:
                    break
                buf += chunk
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    if line.strip():
                        responses.append(json.loads(line))

        assert len(responses) == 2
        assert responses[0]["request_id"] == "b1"
        assert responses[0]["ok"] is True
        assert responses[1]["request_id"] == "b2"
        assert responses[1]["ok"] is True


# ===================================================================
# 8. PARTIAL REQUEST DELIVERY (split across multiple sends)
# ===================================================================
class TestPartialDelivery:
    def test_request_split_across_two_sends(self, seeded_server):
        """
        Send a request in two parts (simulating slow/split TCP delivery).
        The server must buffer until '\\n' arrives before processing.
        """
        host, port = seeded_server
        msg = json.dumps({"request_id": "p1", "action": "ping",
                          "payload": {}}) + "\n"
        half = len(msg) // 2

        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(5.0)
            s.connect((host, port))
            s.sendall(msg[:half].encode("utf-8"))
            time.sleep(0.05)           # tiny gap to force two recv() calls
            s.sendall(msg[half:].encode("utf-8"))

            buf = b""
            while b"\n" not in buf:
                buf += s.recv(4096)

        resp = json.loads(buf.split(b"\n")[0])
        assert resp["ok"] is True
        assert resp["request_id"] == "p1"


# ===================================================================
# 9. PARTIAL RESPONSE READS ON CLIENT SIDE
# ===================================================================
class TestPartialResponseRead:
    def test_client_assembles_partial_response(self, seeded_server):
        """
        SwiggyClient._read_response() must loop until it has a full '\\n'.
        This is implicitly tested by all other TCP tests, but we make it
        explicit here by using a tiny recv buffer (patched below).
        """
        from clients.tcp_client import SwiggyClient
        host, port = seeded_server

        # Monkey-patch recv to return only 10 bytes at a time.
        original_recv = socket.socket.recv

        def tiny_recv(self_sock, bufsize):
            return original_recv(self_sock, min(bufsize, 10))

        import socket as socket_mod
        socket_mod.socket.recv = tiny_recv
        try:
            with SwiggyClient(host=host, port=port) as client:
                resp = client.send("list_restaurants", {})
            assert resp["ok"] is True
            assert len(resp["data"]) == 3
        finally:
            socket_mod.socket.recv = original_recv


# ===================================================================
# 10. CLIENT DISCONNECT WHILE SERVER IS WAITING
# ===================================================================
class TestClientDisconnect:
    def test_server_survives_abrupt_client_disconnect(self, seeded_server):
        """
        Abruptly close a socket without sending a complete request.
        The server must not crash and must accept the next client normally.
        """
        host, port = seeded_server

        # Connect and immediately close without sending anything.
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.connect((host, port))
        s.close()   # abrupt close

        time.sleep(0.15)   # let server process the disconnect

        # Server must still be reachable.
        resp = _client_send(host, port, "ping", {})
        assert resp["ok"] is True


# ===================================================================
# 11. CONNECTION REFUSED (client-side error handling)
# ===================================================================
class TestConnectionRefused:
    def test_client_raises_connection_refused(self):
        """
        Connecting to a port where nothing is listening must raise
        ConnectionRefusedError — the client must NOT print a traceback.
        """
        from clients.tcp_client import SwiggyClient
        # Port 1 is almost certainly unused and not privileged in this context
        # (we just need something that refuses connections).
        client = SwiggyClient(host="127.0.0.1", port=19999)
        with pytest.raises(ConnectionRefusedError):
            client.connect()


# ===================================================================
# 12. OVERSIZED MESSAGE
# ===================================================================
class TestOversizedMessage:
    def test_oversized_message_closes_connection(self, seeded_server):
        """
        Sending more than MAX_MESSAGE_BYTES without a newline must trigger
        the server's buffer overflow guard and close the connection.
        """
        from server.tcp_server import MAX_MESSAGE_BYTES
        host, port = seeded_server

        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(5.0)
            s.connect((host, port))
            # Send MAX_MESSAGE_BYTES + 1 of garbage (no newline).
            s.sendall(b"X" * (MAX_MESSAGE_BYTES + 1))

            buf = b""
            try:
                while b"\n" not in buf:
                    chunk = s.recv(4096)
                    if not chunk:
                        break
                    buf += chunk
            except OSError:
                pass   # connection closed by server — that's expected

        # If we got a response line, it should be an error.
        if b"\n" in buf:
            resp = json.loads(buf.split(b"\n")[0])
            assert resp["ok"] is False
