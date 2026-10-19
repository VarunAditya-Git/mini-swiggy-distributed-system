"""
tests/test_web_gateway.py
=========================
Integration and unit tests for the Mini Swiggy HTTP Web Gateway.

Covers:
  - Static file serving (index.html, styles.css, app.js)
  - Path traversal security guard (403/404 on directory escape attempts)
  - Health check endpoint (200 on connected, 503 on disconnected)
  - REST API forwarding to backend TCP server (list_restaurants, list_menu, create_order, simulate_payment, get_order, get_order_history)
  - Customer ownership enforcement on order receipt access (403 Forbidden on mismatch)
  - Input validation: missing query parameters, malformed JSON, oversized payloads
  - Full end-to-end flow using real TCP and HTTP ephemeral servers
"""

import json
import pathlib
import socket
import sys
import threading
import time
import urllib.error
import urllib.request
import pytest

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))

import database.db as db_module
from database.db import init_db
from database.seed import seed_data
from gateway.http_gateway import create_gateway_server, forward_tcp_request
from server.tcp_server import serve_client


# -------------------------------------------------------------------
# Ephemeral TCP Server Helper
# -------------------------------------------------------------------
def _start_tcp_server_thread(host: str, port: int, db_path, monkeypatch) -> tuple[threading.Thread, int]:
    monkeypatch.setattr(db_module, "DB_PATH", db_path)

    server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server_sock.bind((host, port))
    actual_port = server_sock.getsockname()[1]
    server_sock.listen(5)

    ready_event = threading.Event()

    def _loop():
        ready_event.set()
        try:
            while True:
                server_sock.settimeout(2.0)
                try:
                    conn_sock, addr = server_sock.accept()
                    conn_sock.settimeout(5.0)
                    serve_client(conn_sock, addr)
                except socket.timeout:
                    break
        except OSError:
            pass
        finally:
            server_sock.close()

    t = threading.Thread(target=_loop, daemon=True)
    t.start()
    ready_event.wait(timeout=3.0)
    return t, actual_port


# -------------------------------------------------------------------
# Ephemeral HTTP Gateway Helper
# -------------------------------------------------------------------
def _start_gateway_thread(tcp_host: str, tcp_port: int, web_dir: pathlib.Path) -> tuple[http.server.ThreadingHTTPServer, str, int]:
    server = create_gateway_server(
        http_host="127.0.0.1",
        http_port=0,
        tcp_host=tcp_host,
        tcp_port=tcp_port,
        web_dir=web_dir,
    )
    actual_port = server.server_port
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    return server, "127.0.0.1", actual_port


# -------------------------------------------------------------------
# Fixtures
# -------------------------------------------------------------------
@pytest.fixture
def running_system(tmp_path, monkeypatch):
    """
    Sets up a full running system:
      1. Fresh SQLite test database with seed data.
      2. TCP server running on an ephemeral port.
      3. HTTP gateway running on an ephemeral port, configured to talk to the TCP server.
    Yields (gateway_base_url, tcp_port).
    """
    temp_db = tmp_path / "test_gateway_sys.db"
    monkeypatch.setattr(db_module, "DB_PATH", temp_db)
    init_db()
    seed_data()

    # 1. Start TCP server
    tcp_thread, tcp_port = _start_tcp_server_thread("127.0.0.1", 0, temp_db, monkeypatch)

    # 2. Start HTTP gateway
    web_dir = pathlib.Path(__file__).parent.parent / "web"
    gw_server, gw_host, gw_port = _start_gateway_thread("127.0.0.1", tcp_port, web_dir)

    base_url = f"http://{gw_host}:{gw_port}"
    yield base_url, tcp_port

    gw_server.shutdown()
    gw_server.server_close()


# -------------------------------------------------------------------
# Tests: Static File Serving & Security
# -------------------------------------------------------------------
class TestStaticFilesAndSecurity:

    def test_serve_index_html(self, running_system):
        base_url, _ = running_system
        req = urllib.request.Request(f"{base_url}/")
        with urllib.request.urlopen(req) as response:
            assert response.status == 200
            assert "text/html" in response.headers.get("Content-Type", "")
            content = response.read().decode("utf-8")
            assert "Mini Swiggy" in content
            assert "Ordering as" in content

    def test_serve_styles_css(self, running_system):
        base_url, _ = running_system
        req = urllib.request.Request(f"{base_url}/styles.css")
        with urllib.request.urlopen(req) as response:
            assert response.status == 200
            assert "text/css" in response.headers.get("Content-Type", "")
            content = response.read().decode("utf-8")
            assert "--primary" in content

    def test_serve_app_js(self, running_system):
        base_url, _ = running_system
        req = urllib.request.Request(f"{base_url}/app.js")
        with urllib.request.urlopen(req) as response:
            assert response.status == 200
            assert "javascript" in response.headers.get("Content-Type", "")
            content = response.read().decode("utf-8")
            assert "Mini Swiggy" in content

    def test_path_traversal_blocked(self, running_system):
        base_url, _ = running_system
        # Attempt to escape web/ directory to read server/tcp_server.py
        with pytest.raises(urllib.error.HTTPError) as exc_info:
            urllib.request.urlopen(f"{base_url}/../server/tcp_server.py")
        assert exc_info.value.code in (403, 404)

    def test_nonexistent_file_returns_404(self, running_system):
        base_url, _ = running_system
        with pytest.raises(urllib.error.HTTPError) as exc_info:
            urllib.request.urlopen(f"{base_url}/nonexistent_file.xyz")
        assert exc_info.value.code == 404


# -------------------------------------------------------------------
# Tests: Health Check
# -------------------------------------------------------------------
class TestHealthEndpoint:

    def test_health_when_tcp_connected(self, running_system):
        base_url, _ = running_system
        req = urllib.request.Request(f"{base_url}/api/health")
        with urllib.request.urlopen(req) as response:
            assert response.status == 200
            data = json.loads(response.read().decode("utf-8"))
            assert data["ok"] is True
            assert data["gateway"] == "running"
            assert data["tcp_backend"] == "connected"

    def test_health_when_tcp_backend_unavailable(self, tmp_path):
        # Start gateway pointing to an unallocated port
        web_dir = pathlib.Path(__file__).parent.parent / "web"
        gw_server, gw_host, gw_port = _start_gateway_thread("127.0.0.1", 59999, web_dir)
        base_url = f"http://{gw_host}:{gw_port}"

        try:
            req = urllib.request.Request(f"{base_url}/api/health")
            with pytest.raises(urllib.error.HTTPError) as exc_info:
                urllib.request.urlopen(req)
            assert exc_info.value.code == 503
            err_data = json.loads(exc_info.value.read().decode("utf-8"))
            assert err_data["ok"] is False
            assert err_data["tcp_backend"] == "disconnected"
        finally:
            gw_server.shutdown()
            gw_server.server_close()


# -------------------------------------------------------------------
# Tests: REST API Endpoints & Business Forwarding
# -------------------------------------------------------------------
class TestGatewayAPIEndpoints:

    def test_get_restaurants(self, running_system):
        base_url, _ = running_system
        req = urllib.request.Request(f"{base_url}/api/restaurants")
        with urllib.request.urlopen(req) as response:
            assert response.status == 200
            data = json.loads(response.read().decode("utf-8"))
            assert data["ok"] is True
            assert len(data["data"]) == 3
            names = [r["name"] for r in data["data"]]
            assert "Hyderabad Spice House" in names

    def test_get_menu_valid_restaurant(self, running_system):
        base_url, _ = running_system
        req = urllib.request.Request(f"{base_url}/api/restaurants/1/menu")
        with urllib.request.urlopen(req) as response:
            assert response.status == 200
            data = json.loads(response.read().decode("utf-8"))
            assert data["ok"] is True
            assert len(data["data"]) == 2
            assert data["data"][0]["name"] == "Chicken Biryani"

    def test_get_menu_invalid_id_format(self, running_system):
        base_url, _ = running_system
        with pytest.raises(urllib.error.HTTPError) as exc_info:
            urllib.request.urlopen(f"{base_url}/api/restaurants/abc/menu")
        assert exc_info.value.code == 400

    def test_post_order_creation_success(self, running_system):
        base_url, _ = running_system
        payload = {
            "customer_id": 1,
            "restaurant_id": 1,
            "items": [
                {"item_id": 1, "quantity": 2},
                {"item_id": 2, "quantity": 1},
            ],
        }
        req = urllib.request.Request(
            f"{base_url}/api/orders",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req) as response:
            assert response.status == 201
            data = json.loads(response.read().decode("utf-8"))
            assert data["ok"] is True
            assert data["data"]["order_id"] > 0
            assert data["data"]["total_amount"] == 480.0
            assert data["data"]["order_status"] == "placed"

    def test_post_order_empty_cart_rejected(self, running_system):
        base_url, _ = running_system
        payload = {"customer_id": 1, "restaurant_id": 1, "items": []}
        req = urllib.request.Request(
            f"{base_url}/api/orders",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with pytest.raises(urllib.error.HTTPError) as exc_info:
            urllib.request.urlopen(req)
        assert exc_info.value.code == 422
        data = json.loads(exc_info.value.read().decode("utf-8"))
        assert data["error"]["code"] == "INVALID_PAYLOAD"

    def test_simulate_payment_success_and_duplicate_prevention(self, running_system):
        base_url, _ = running_system

        # 1. Place order
        create_payload = {
            "customer_id": 1,
            "restaurant_id": 1,
            "items": [{"item_id": 1, "quantity": 1}],
        }
        create_req = urllib.request.Request(
            f"{base_url}/api/orders",
            data=json.dumps(create_payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(create_req) as cr:
            order_id = json.loads(cr.read().decode("utf-8"))["data"]["order_id"]

        # 2. Pay for order
        pay_payload = {"payment_method": "simulated_upi", "simulate_status": "paid"}
        pay_req = urllib.request.Request(
            f"{base_url}/api/orders/{order_id}/payment",
            data=json.dumps(pay_payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(pay_req) as pr:
            assert pr.status == 200
            pay_data = json.loads(pr.read().decode("utf-8"))
            assert pay_data["ok"] is True
            assert pay_data["data"]["payment_status"] == "paid"
            assert pay_data["data"]["order_status"] == "accepted"

        # 3. Repeat payment attempt (verify duplicate charge blocked)
        with pytest.raises(urllib.error.HTTPError) as exc_info:
            urllib.request.urlopen(pay_req)
        assert exc_info.value.code == 409
        dup_err = json.loads(exc_info.value.read().decode("utf-8"))
        assert dup_err["error"]["code"] == "PAYMENT_ALREADY_COMPLETED"

    def test_simulate_payment_failure_behavior(self, running_system):
        base_url, _ = running_system

        # Place order
        create_payload = {
            "customer_id": 1,
            "restaurant_id": 1,
            "items": [{"item_id": 1, "quantity": 1}],
        }
        create_req = urllib.request.Request(
            f"{base_url}/api/orders",
            data=json.dumps(create_payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(create_req) as cr:
            order_id = json.loads(cr.read().decode("utf-8"))["data"]["order_id"]

        # Simulate failed payment
        pay_payload = {"simulate_status": "failed", "simulate_failure": True}
        pay_req = urllib.request.Request(
            f"{base_url}/api/orders/{order_id}/payment",
            data=json.dumps(pay_payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(pay_req) as pr:
            assert pr.status == 200
            pay_data = json.loads(pr.read().decode("utf-8"))
            assert pay_data["ok"] is True
            assert pay_data["data"]["payment_status"] == "failed"
            assert pay_data["data"]["order_status"] == "placed"

    def test_get_order_with_customer_ownership_enforcement(self, running_system):
        base_url, _ = running_system

        # Place order for customer 1
        create_payload = {
            "customer_id": 1,
            "restaurant_id": 1,
            "items": [{"item_id": 1, "quantity": 1}],
        }
        create_req = urllib.request.Request(
            f"{base_url}/api/orders",
            data=json.dumps(create_payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(create_req) as cr:
            order_id = json.loads(cr.read().decode("utf-8"))["data"]["order_id"]

        # 1. Customer 1 accesses their own order -> 200 OK
        req_own = urllib.request.Request(f"{base_url}/api/orders/{order_id}?customer_id=1")
        with urllib.request.urlopen(req_own) as res:
            assert res.status == 200
            data = json.loads(res.read().decode("utf-8"))
            assert data["data"]["order_id"] == order_id
            assert data["data"]["customer_id"] == 1

        # 2. Customer 2 attempts to access customer 1's order -> 403 Forbidden
        req_unauth = urllib.request.Request(f"{base_url}/api/orders/{order_id}?customer_id=2")
        with pytest.raises(urllib.error.HTTPError) as exc_info:
            urllib.request.urlopen(req_unauth)
        assert exc_info.value.code == 403
        data = json.loads(exc_info.value.read().decode("utf-8"))
        assert data["error"]["code"] == "FORBIDDEN"

        # 3. Request without customer_id query parameter -> 400 Bad Request
        with pytest.raises(urllib.error.HTTPError) as exc_info:
            urllib.request.urlopen(f"{base_url}/api/orders/{order_id}")
        assert exc_info.value.code == 400

    def test_get_customer_order_history(self, running_system):
        base_url, _ = running_system

        # Place 2 orders for customer 1
        for _ in range(2):
            create_payload = {
                "customer_id": 1,
                "restaurant_id": 1,
                "items": [{"item_id": 1, "quantity": 1}],
            }
            create_req = urllib.request.Request(
                f"{base_url}/api/orders",
                data=json.dumps(create_payload).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            urllib.request.urlopen(create_req)

        req = urllib.request.Request(f"{base_url}/api/customers/1/orders")
        with urllib.request.urlopen(req) as res:
            assert res.status == 200
            data = json.loads(res.read().decode("utf-8"))
            assert data["ok"] is True
            assert len(data["data"]) >= 2
            # Verify newest first ordering
            assert data["data"][0]["order_id"] > data["data"][1]["order_id"]

    def test_malformed_json_in_body_returns_400(self, running_system):
        base_url, _ = running_system
        req = urllib.request.Request(
            f"{base_url}/api/orders",
            data=b"{not valid json...",
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with pytest.raises(urllib.error.HTTPError) as exc_info:
            urllib.request.urlopen(req)
        assert exc_info.value.code == 400

    def test_oversized_payload_returns_413(self, running_system):
        base_url, _ = running_system
        oversized = "x" * (70 * 1024)  # 70 KiB > 64 KiB limit
        req = urllib.request.Request(
            f"{base_url}/api/orders",
            data=json.dumps({"data": oversized}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with pytest.raises(urllib.error.HTTPError) as exc_info:
            urllib.request.urlopen(req)
        assert exc_info.value.code == 413
