"""
tests/test_v3_gateway.py
========================
Integration tests for Mini Swiggy V3 Seller Dashboard HTTP Gateway endpoints:
  - Static file serving (/seller, /seller.html, /seller.css, /seller.js)
  - GET /api/seller/profile
  - GET /api/seller/orders
  - GET /api/seller/orders/{order_id}
  - POST /api/seller/orders/{order_id}/status
  - GET /api/seller/menu
  - POST /api/seller/menu
  - PATCH /api/seller/menu/{item_id}
  - PATCH /api/seller/menu/{item_id}/availability
  - CORS preflight OPTIONS requests
"""

import http.server
import json
import pathlib
import socket
import sys
import threading
import urllib.error
import urllib.request
import pytest

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))

import database.db as db_module
from database.db import get_connection, init_db
from database.seed import seed_data
from gateway.http_gateway import create_gateway_server
from server.tcp_server import serve_client


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


@pytest.fixture
def running_system(tmp_path, monkeypatch):
    """Sets up running SQLite, TCP server, and HTTP gateway."""
    temp_db = tmp_path / "test_v3_gateway_sys.db"
    monkeypatch.setattr(db_module, "DB_PATH", temp_db)
    init_db()
    seed_data()

    # Seed extra seller Eve owning Restaurant 2
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO users (name, email, role, is_active) VALUES (?, ?, ?, ?);",
            ("Eve Seller", "eve@example.com", "seller", 1),
        )
        eve_id = conn.execute("SELECT user_id FROM users WHERE email = 'eve@example.com';").fetchone()["user_id"]
        conn.execute("UPDATE restaurants SET owner_user_id = ? WHERE restaurant_id = 2;", (eve_id,))

    tcp_thread, tcp_port = _start_tcp_server_thread("127.0.0.1", 0, temp_db, monkeypatch)
    web_dir = pathlib.Path(__file__).parent.parent / "web"
    gw_server, gw_host, gw_port = _start_gateway_thread("127.0.0.1", tcp_port, web_dir)

    base_url = f"http://{gw_host}:{gw_port}"
    yield base_url, tcp_port

    gw_server.shutdown()
    gw_server.server_close()


# -------------------------------------------------------------------
# Helper to make HTTP requests
# -------------------------------------------------------------------
def _http_request(url: str, method: str = "GET", body: dict | None = None) -> tuple[int, dict, dict]:
    headers = {}
    data_bytes = None
    if body is not None:
        data_bytes = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"

    req = urllib.request.Request(url, data=data_bytes, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=5.0) as resp:
            resp_headers = dict(resp.headers)
            raw = resp.read().decode("utf-8")
            return resp.status, resp_headers, (json.loads(raw) if "json" in resp_headers.get("Content-Type", "") else raw)
    except urllib.error.HTTPError as err:
        err_headers = dict(err.headers)
        raw = err.read().decode("utf-8")
        parsed = json.loads(raw) if "json" in err_headers.get("Content-Type", "") else raw
        return err.code, err_headers, parsed


class TestSellerGatewayStatic:

    def test_serve_seller_html_at_seller_path(self, running_system):
        """GET /seller serves web/seller.html with 200 OK."""
        base_url, _ = running_system
        status, headers, content = _http_request(f"{base_url}/seller")
        assert status == 200
        assert "text/html" in headers["Content-Type"]
        assert "Restaurant Seller Dashboard" in content

    def test_serve_seller_html_file(self, running_system):
        """GET /seller.html serves web/seller.html with 200 OK."""
        base_url, _ = running_system
        status, headers, content = _http_request(f"{base_url}/seller.html")
        assert status == 200
        assert "text/html" in headers["Content-Type"]
        assert "Restaurant Seller Dashboard" in content

    def test_serve_seller_css(self, running_system):
        """GET /seller.css serves CSS stylesheet with 200 OK."""
        base_url, _ = running_system
        status, headers, content = _http_request(f"{base_url}/seller.css")
        assert status == 200
        assert "text/css" in headers["Content-Type"]
        assert "--seller-primary" in content

    def test_serve_seller_js(self, running_system):
        """GET /seller.js serves JavaScript client with 200 OK."""
        base_url, _ = running_system
        status, headers, content = _http_request(f"{base_url}/seller.js")
        assert status == 200
        assert "javascript" in headers["Content-Type"]
        assert "Mini Swiggy — Seller Dashboard" in content


class TestSellerGatewayEndpoints:

    def test_get_seller_profile_success(self, running_system):
        """GET /api/seller/profile?seller_id=2 returns Bob Seller profile."""
        base_url, _ = running_system
        status, _, data = _http_request(f"{base_url}/api/seller/profile?seller_id=2")
        assert status == 200
        assert data["ok"] is True
        assert data["data"]["seller"]["name"] == "Bob Seller"
        assert data["data"]["restaurant"]["name"] == "Hyderabad Spice House"
        assert data["data"]["stats"]["total_orders"] == 0

    def test_get_seller_profile_missing_param_returns_400(self, running_system):
        """GET /api/seller/profile without seller_id returns 400 Bad Request."""
        base_url, _ = running_system
        status, _, data = _http_request(f"{base_url}/api/seller/profile")
        assert status == 400
        assert data["ok"] is False
        assert data["error"]["code"] == "INVALID_REQUEST"

    def test_get_seller_profile_customer_role_returns_403(self, running_system):
        """GET /api/seller/profile?seller_id=1 (Alice Customer) returns 403 Forbidden."""
        base_url, _ = running_system
        status, _, data = _http_request(f"{base_url}/api/seller/profile?seller_id=1")
        assert status == 403
        assert data["ok"] is False
        assert data["error"]["code"] == "UNAUTHORIZED_ROLE"

    def test_get_seller_orders_success(self, running_system):
        """GET /api/seller/orders?seller_id=2 returns orders list."""
        base_url, _ = running_system
        # First place customer order
        _http_request(
            f"{base_url}/api/orders",
            method="POST",
            body={"customer_id": 1, "restaurant_id": 1, "items": [{"item_id": 1, "quantity": 1}]},
        )
        status, _, data = _http_request(f"{base_url}/api/seller/orders?seller_id=2")
        assert status == 200
        assert data["ok"] is True
        assert len(data["data"]) == 1
        assert data["data"][0]["order_status"] == "placed"

    def test_get_seller_order_by_id_success(self, running_system):
        """GET /api/seller/orders/{order_id}?seller_id=2 returns order detail."""
        base_url, _ = running_system
        _, _, post_res = _http_request(
            f"{base_url}/api/orders",
            method="POST",
            body={"customer_id": 1, "restaurant_id": 1, "items": [{"item_id": 2, "quantity": 1}]},
        )
        order_id = post_res["data"]["order_id"]

        status, _, data = _http_request(f"{base_url}/api/seller/orders/{order_id}?seller_id=2")
        assert status == 200
        assert data["ok"] is True
        assert data["data"]["order_id"] == order_id
        assert data["data"]["customer_name"] == "Alice Customer"

    def test_post_order_status_update_lifecycle(self, running_system):
        """POST /api/seller/orders/{order_id}/status advances lifecycle."""
        base_url, _ = running_system
        _, _, post_res = _http_request(
            f"{base_url}/api/orders",
            method="POST",
            body={"customer_id": 1, "restaurant_id": 1, "items": [{"item_id": 1, "quantity": 1}]},
        )
        order_id = post_res["data"]["order_id"]

        # 1. Accept
        s1, _, d1 = _http_request(
            f"{base_url}/api/seller/orders/{order_id}/status",
            method="POST",
            body={"seller_id": 2, "status": "accepted"},
        )
        assert s1 == 200
        assert d1["data"]["order_status"] == "accepted"

        # 2. Preparing
        s2, _, d2 = _http_request(
            f"{base_url}/api/seller/orders/{order_id}/status",
            method="POST",
            body={"seller_id": 2, "status": "preparing"},
        )
        assert s2 == 200
        assert d2["data"]["order_status"] == "preparing"

        # 3. Ready for pickup
        s3, _, d3 = _http_request(
            f"{base_url}/api/seller/orders/{order_id}/status",
            method="POST",
            body={"seller_id": 2, "status": "ready_for_pickup"},
        )
        assert s3 == 200
        assert d3["data"]["order_status"] == "ready_for_pickup"

    def test_post_order_status_invalid_transition_returns_409(self, running_system):
        """Attempting invalid status transition returns 409 Conflict."""
        base_url, _ = running_system
        _, _, post_res = _http_request(
            f"{base_url}/api/orders",
            method="POST",
            body={"customer_id": 1, "restaurant_id": 1, "items": [{"item_id": 1, "quantity": 1}]},
        )
        order_id = post_res["data"]["order_id"]

        # Try to jump directly to ready_for_pickup without accepting or preparing
        status, _, data = _http_request(
            f"{base_url}/api/seller/orders/{order_id}/status",
            method="POST",
            body={"seller_id": 2, "status": "ready_for_pickup"},
        )
        assert status == 409
        assert data["ok"] is False
        assert data["error"]["code"] == "INVALID_TRANSITION"

    def test_seller_menu_crud_endpoints(self, running_system):
        """Test GET /api/seller/menu, POST /api/seller/menu, PATCH /api/seller/menu/{id}."""
        base_url, _ = running_system

        # 1. GET seller menu
        s1, _, d1 = _http_request(f"{base_url}/api/seller/menu?seller_id=2")
        assert s1 == 200
        assert len(d1["data"]) == 2

        # 2. POST add menu item
        s2, _, d2 = _http_request(
            f"{base_url}/api/seller/menu",
            method="POST",
            body={
                "seller_id": 2,
                "name": "Hyderabadi Haleem",
                "price": 220.0,
                "description": "Slow cooked meat stew.",
                "is_available": 1,
            },
        )
        assert s2 == 201
        assert d2["data"]["name"] == "Hyderabadi Haleem"
        item_id = d2["data"]["item_id"]

        # 3. PATCH update menu item
        s3, _, d3 = _http_request(
            f"{base_url}/api/seller/menu/{item_id}",
            method="PATCH",
            body={"seller_id": 2, "price": 240.0, "name": "Special Hyderabadi Haleem"},
        )
        assert s3 == 200
        assert d3["data"]["price"] == 240.0
        assert d3["data"]["name"] == "Special Hyderabadi Haleem"

        # 4. PATCH update availability
        s4, _, d4 = _http_request(
            f"{base_url}/api/seller/menu/{item_id}/availability",
            method="PATCH",
            body={"seller_id": 2, "is_available": 0},
        )
        assert s4 == 200
        assert d4["data"]["is_available"] == 0

    def test_options_cors_preflight(self, running_system):
        """OPTIONS preflight returns 204 No Content with CORS headers."""
        base_url, _ = running_system
        status, headers, _ = _http_request(f"{base_url}/api/seller/orders", method="OPTIONS")
        assert status == 204
        assert "Access-Control-Allow-Methods" in headers
        assert "PATCH" in headers["Access-Control-Allow-Methods"]
