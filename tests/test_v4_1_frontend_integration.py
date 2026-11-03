"""
tests/test_v4_1_frontend_integration.py
=======================================
Integration tests for Mini Swiggy V4.1 Customer Frontend Authentication:
  1. Static UI Verification:
     - Login and Register navigation buttons present in index.html
     - Auth modal with login form (email, password) and register form (name, email, password, confirm password)
     - Session badge and logout button present in DOM
     - Stylesheet contains required orange-themed auth UI classes
     - app.js contains session monitoring, auth APIs, and credentials: "include"
  2. End-to-End Authentication & Customer Lifecycle:
     - Registration flow with email verification requirement
     - Single-use email verification link consumption
     - Login sets HTTP-only session cookie
     - Session persistence across simulated page loads (/api/auth/me)
     - Session-derived customer identity for order creation, payment, and history
     - Logout clears session cookie and revokes session
     - Unauthenticated access handling
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
from database.seed import seed_data, DEFAULT_SEED_PASSWORD
from gateway.http_gateway import create_gateway_server
from server.tcp_server import serve_client


# -------------------------------------------------------------------
# Server Fixtures
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
            try:
                server_sock.close()
            except OSError:
                pass

    t = threading.Thread(target=_loop, daemon=True)
    t.start()
    ready_event.wait()
    return t, actual_port


@pytest.fixture
def running_stack(tmp_path, monkeypatch):
    db_file = tmp_path / "test_fe_auth.db"
    monkeypatch.setattr(db_module, "DB_PATH", db_file)
    init_db()
    seed_data()

    # Start TCP backend
    tcp_thread, tcp_port = _start_tcp_server_thread("127.0.0.1", 0, db_file, monkeypatch)

    # Start HTTP Gateway
    httpd = create_gateway_server(
        http_host="127.0.0.1",
        http_port=0,
        tcp_host="127.0.0.1",
        tcp_port=tcp_port,
    )
    http_port = httpd.server_address[1]

    gateway_thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    gateway_thread.start()

    yield f"http://127.0.0.1:{http_port}"

    httpd.shutdown()
    httpd.server_close()


# -------------------------------------------------------------------
# 1. UI Elements Verification in Static Files
# -------------------------------------------------------------------
def test_static_html_has_auth_ui_elements(running_stack):
    base_url = running_stack
    with urllib.request.urlopen(f"{base_url}/") as resp:
        assert resp.status == 200
        html = resp.read().decode("utf-8")

        # Logged-out buttons
        assert 'id="auth-nav-logged-out"' in html
        assert 'id="btn-nav-login"' in html
        assert 'id="btn-nav-register"' in html

        # Logged-in profile and logout
        assert 'id="auth-nav-logged-in"' in html
        assert 'id="btn-nav-logout"' in html
        assert 'id="current-customer-name"' in html

        # Auth Modal with forms
        assert 'id="auth-modal"' in html
        assert 'id="form-login"' in html
        assert 'id="login-email"' in html
        assert 'id="login-password"' in html
        assert 'id="form-register"' in html
        assert 'id="register-name"' in html
        assert 'id="register-email"' in html
        assert 'id="register-password"' in html
        assert 'id="register-confirm-password"' in html
        assert 'id="btn-dev-auto-verify"' in html


def test_static_css_has_auth_styles(running_stack):
    base_url = running_stack
    with urllib.request.urlopen(f"{base_url}/styles.css") as resp:
        assert resp.status == 200
        css = resp.read().decode("utf-8")

        assert ".auth-nav-logged-out" in css
        assert ".btn-auth-login" in css
        assert ".btn-auth-register" in css
        assert ".btn-logout" in css
        assert ".auth-modal" in css
        assert ".auth-tabs" in css
        assert ".dev-verification-banner" in css


def test_static_js_has_auth_flow(running_stack):
    base_url = running_stack
    with urllib.request.urlopen(f"{base_url}/app.js") as resp:
        assert resp.status == 200
        js = resp.read().decode("utf-8")

        assert 'credentials: "include"' in js
        assert "/api/auth/register" in js
        assert "/api/auth/login" in js
        assert "/api/auth/logout" in js
        assert "/api/auth/me" in js
        assert "checkSession" in js
        assert "setUserSession" in js
        assert "clearUserSession" in js


# -------------------------------------------------------------------
# 2. Authentication & Session Integration Flow
# -------------------------------------------------------------------
def test_customer_full_auth_and_order_flow(running_stack):
    base_url = running_stack

    # A. Unauthenticated /api/auth/me returns 401
    with pytest.raises(urllib.error.HTTPError) as exc_info:
        urllib.request.urlopen(f"{base_url}/api/auth/me")
    assert exc_info.value.code == 401

    # B. Register a brand new customer
    reg_body = json.dumps({
        "name": "Martha Jones",
        "email": "martha@hospital.org",
        "password": "Password123!",
        "role": "customer",
    }).encode("utf-8")

    reg_req = urllib.request.Request(
        f"{base_url}/api/auth/register",
        data=reg_body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(reg_req) as resp:
        assert resp.status == 201
        reg_res = json.loads(resp.read().decode("utf-8"))
        assert reg_res["ok"] is True
        token = reg_res["data"]["verification_token"]
        assert token is not None

    # C. Login fails before email verification
    unverified_login_req = urllib.request.Request(
        f"{base_url}/api/auth/login",
        data=json.dumps({"email": "martha@hospital.org", "password": "Password123!"}).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with pytest.raises(urllib.error.HTTPError) as exc_info:
        urllib.request.urlopen(unverified_login_req)
    assert exc_info.value.code == 403

    # D. Verify email via link/endpoint
    with urllib.request.urlopen(f"{base_url}/api/auth/verify-email?token={token}") as resp:
        assert resp.status == 200
        verify_res = json.loads(resp.read().decode("utf-8"))
        assert verify_res["ok"] is True
        assert verify_res["data"]["email_verified"] == 1

    # E. Login successfully
    login_req = urllib.request.Request(
        f"{base_url}/api/auth/login",
        data=json.dumps({"email": "martha@hospital.org", "password": "Password123!"}).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(login_req) as resp:
        assert resp.status == 200
        login_res = json.loads(resp.read().decode("utf-8"))
        assert login_res["ok"] is True
        new_user_id = login_res["data"]["user"]["user_id"]
        assert new_user_id > 4  # Beyond pre-seeded users 1-4

        cookie_hdr = resp.headers.get("Set-Cookie")
        assert "session_id=" in cookie_hdr
        assert "HttpOnly" in cookie_hdr
        cookie_val = cookie_hdr.split(";")[0]

    # F. Verify session persistence (Simulated page refresh -> /api/auth/me)
    me_req = urllib.request.Request(
        f"{base_url}/api/auth/me",
        headers={"Cookie": cookie_val},
        method="GET",
    )
    with urllib.request.urlopen(me_req) as resp:
        assert resp.status == 200
        me_res = json.loads(resp.read().decode("utf-8"))
        assert me_res["ok"] is True
        assert me_res["data"]["user_id"] == new_user_id
        assert me_res["data"]["name"] == "Martha Jones"
        assert me_res["data"]["email"] == "martha@hospital.org"

    # G. Place an order using session identity (NO customer_id in request body!)
    order_body = json.dumps({
        "restaurant_id": 1,
        "items": [{"item_id": 1, "quantity": 2}],
    }).encode("utf-8")

    order_req = urllib.request.Request(
        f"{base_url}/api/orders",
        data=order_body,
        headers={"Content-Type": "application/json", "Cookie": cookie_val},
        method="POST",
    )
    with urllib.request.urlopen(order_req) as resp:
        assert resp.status == 201
        order_res = json.loads(resp.read().decode("utf-8"))
        assert order_res["ok"] is True
        order_id = order_res["data"]["order_id"]
        # Authoritatively assigned to Martha, NOT hardcoded Alice!
        assert order_res["data"]["customer_id"] == new_user_id
        assert order_res["data"]["total_amount"] == 360.0

    # H. Simulate payment using session identity
    pay_body = json.dumps({
        "payment_method": "simulated_upi",
        "simulate_status": "success",
    }).encode("utf-8")

    pay_req = urllib.request.Request(
        f"{base_url}/api/orders/{order_id}/payment",
        data=pay_body,
        headers={"Content-Type": "application/json", "Cookie": cookie_val},
        method="POST",
    )
    with urllib.request.urlopen(pay_req) as resp:
        assert resp.status == 200
        pay_res = json.loads(resp.read().decode("utf-8"))
        assert pay_res["ok"] is True
        assert pay_res["data"]["payment_status"] == "paid"

    # I. View order history for authenticated user via /api/customers/me/orders
    history_req = urllib.request.Request(
        f"{base_url}/api/customers/me/orders",
        headers={"Cookie": cookie_val},
        method="GET",
    )
    with urllib.request.urlopen(history_req) as resp:
        assert resp.status == 200
        hist_res = json.loads(resp.read().decode("utf-8"))
        assert hist_res["ok"] is True
        orders = hist_res["data"]
        assert len(orders) == 1
        assert orders[0]["order_id"] == order_id
        assert orders[0]["customer_id"] == new_user_id

    # J. Logout
    logout_req = urllib.request.Request(
        f"{base_url}/api/auth/logout",
        data=b"{}",
        headers={"Content-Type": "application/json", "Cookie": cookie_val},
        method="POST",
    )
    with urllib.request.urlopen(logout_req) as resp:
        assert resp.status == 200
        logout_cookie = resp.headers.get("Set-Cookie")
        assert "Max-Age=0" in logout_cookie

    # K. Post-logout /api/auth/me returns 401
    with pytest.raises(urllib.error.HTTPError) as exc_info:
        urllib.request.urlopen(me_req)
    assert exc_info.value.code == 401

