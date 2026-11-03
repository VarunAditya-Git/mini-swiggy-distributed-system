"""
tests/test_v4_1_security_audit.py
==================================
Comprehensive security audit tests for Mini Swiggy V4.1.

Verification coverage:
  1. Session cookie consistency (session_id vs mini_swiggy_session)
  2. Database stores only SHA-256 hash of session token, never raw token
  3. Cookie security flags (HttpOnly, SameSite=Strict, Secure in production HTTPS)
  4. CSRF protection on state-changing requests with cookie authentication
  5. Seed account safety in production (seed_data raises RuntimeError)
  6. Admin account cannot be publicly registered
  7. Customer ownership enforcement:
     - Customer A cannot view Customer B's order
     - Customer A cannot view Customer B's order history
     - Customer A cannot pay for Customer B's order
     - Customer A cannot view or retry Customer B's refund
  8. Logout invalidates session in database
  9. Expired and revoked sessions are rejected
 10. Unauthenticated requests cannot access protected operations
 11. Customer session cannot access seller endpoints
"""

import hashlib
import json
import os
import sqlite3
import threading
import time
import urllib.error
import urllib.request
import pytest

import socket
import database.db as db_module
from database.db import get_connection, init_db
from database.seed import seed_data
from gateway.http_gateway import create_gateway_server, derive_csrf_token
from server.auth import hash_token
from server.tcp_server import serve_client


def _start_tcp_server_thread(host: str, port: int, db_path: str):
    server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server_sock.bind((host, port))
    actual_port = server_sock.getsockname()[1]
    server_sock.listen(10)

    stop_event = threading.Event()

    def _loop():
        server_sock.settimeout(0.5)
        while not stop_event.is_set():
            try:
                conn_sock, addr = server_sock.accept()
                conn_sock.settimeout(5.0)
                serve_client(conn_sock, addr)
            except socket.timeout:
                continue
            except OSError:
                break
        try:
            server_sock.close()
        except OSError:
            pass

    t = threading.Thread(target=_loop, daemon=True)
    t.start()
    return t, actual_port, stop_event


@pytest.fixture
def audit_system(tmp_path, monkeypatch):
    """
    Spins up an isolated TCP server and HTTP gateway instance backed by
    a temporary test database with standard seed data.
    """
    test_db_path = tmp_path / "audit_swiggy.db"
    monkeypatch.setattr(db_module, "DB_PATH", test_db_path)

    init_db()
    seed_data()

    tcp_thread, tcp_port, stop_event = _start_tcp_server_thread("127.0.0.1", 0, str(test_db_path))

    gateway_server = create_gateway_server(
        http_host="127.0.0.1",
        http_port=0,
        tcp_host="127.0.0.1",
        tcp_port=tcp_port,
    )
    http_port = gateway_server.server_address[1]
    http_thread = threading.Thread(target=gateway_server.serve_forever, daemon=True)
    http_thread.start()

    time.sleep(0.05)
    base_url = f"http://127.0.0.1:{http_port}"

    yield base_url, test_db_path

    gateway_server.shutdown()
    gateway_server.server_close()
    stop_event.set()


def _http_post(url: str, data: dict, headers: dict = None) -> tuple[int, dict, dict]:
    """Helper to send HTTP POST and return (status, body_json, response_headers)."""
    hdrs = {"Content-Type": "application/json"}
    if headers:
        hdrs.update(headers)
    body = json.dumps(data).encode("utf-8")
    req = urllib.request.Request(url, data=body, headers=hdrs, method="POST")
    try:
        with urllib.request.urlopen(req) as resp:
            raw = resp.read().decode("utf-8")
            return resp.status, json.loads(raw), dict(resp.headers)
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8")
        try:
            parsed = json.loads(raw)
        except Exception:
            parsed = {"raw": raw}
        return exc.code, parsed, dict(exc.headers)


def _http_get(url: str, headers: dict = None) -> tuple[int, dict, dict]:
    """Helper to send HTTP GET and return (status, body_json, response_headers)."""
    req = urllib.request.Request(url, headers=headers or {}, method="GET")
    try:
        with urllib.request.urlopen(req) as resp:
            raw = resp.read().decode("utf-8")
            return resp.status, json.loads(raw), dict(resp.headers)
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8")
        try:
            parsed = json.loads(raw)
        except Exception:
            parsed = {"raw": raw}
        return exc.code, parsed, dict(exc.headers)


# -------------------------------------------------------------------
# 1. Cookie & Token Security
# -------------------------------------------------------------------
class TestCookieAndSessionSecurity:
    def test_database_stores_only_sha256_hash_of_session_token(self, audit_system):
        """Verify the database stores SHA-256 hash of token, never raw token."""
        base_url, db_path = audit_system

        # Login Alice
        status, data, hdrs = _http_post(f"{base_url}/api/auth/login", {
            "email": "alice@example.com",
            "password": "Password123!",
        })
        assert status == 200
        raw_token = data["data"]["session_token"]
        expected_hash = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()

        # Check database directly
        with sqlite3.connect(db_path) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute("SELECT session_id, user_id FROM sessions WHERE session_id = ?;", (expected_hash,)).fetchone()
            assert row is not None
            assert row["session_id"] == expected_hash
            assert row["user_id"] == 1

            # Ensure the raw token string is nowhere in the sessions table
            raw_check = conn.execute("SELECT session_id FROM sessions WHERE session_id = ?;", (raw_token,)).fetchone()
            assert raw_check is None

    def test_cookie_name_consistency_and_dual_support(self, audit_system):
        """Verify session_id is standard and mini_swiggy_session is accepted."""
        base_url, _ = audit_system

        # Login
        status, data, hdrs = _http_post(f"{base_url}/api/auth/login", {
            "email": "alice@example.com",
            "password": "Password123!",
        })
        assert status == 200
        raw_token = data["data"]["session_token"]

        # Check Set-Cookie format
        set_cookie = hdrs.get("Set-Cookie", "")
        assert "session_id=" in set_cookie
        assert "HttpOnly" in set_cookie
        assert "SameSite=Strict" in set_cookie

        # 1. Access /api/auth/me using standard session_id cookie
        status1, data1, _ = _http_get(f"{base_url}/api/auth/me", headers={"Cookie": f"session_id={raw_token}"})
        assert status1 == 200
        assert data1["data"]["email"] == "alice@example.com"

        # 2. Access /api/auth/me using alternative mini_swiggy_session cookie
        status2, data2, _ = _http_get(f"{base_url}/api/auth/me", headers={"Cookie": f"mini_swiggy_session={raw_token}"})
        assert status2 == 200
        assert data2["data"]["email"] == "alice@example.com"

        # 3. Logout clears both cookies
        csrf_token = derive_csrf_token(raw_token)
        status3, _, logout_hdrs = _http_post(
            f"{base_url}/api/auth/logout",
            {},
            headers={"Cookie": f"session_id={raw_token}", "X-CSRF-Token": csrf_token},
        )
        assert status3 == 200
        set_cookies_header = logout_hdrs.get("Set-Cookie", "")
        assert "Max-Age=0" in set_cookies_header

    def test_cookie_secure_flag_in_production_mode(self, audit_system):
        """Verify Secure attribute is dynamically added when X-Forwarded-Proto=https or in production."""
        base_url, _ = audit_system

        # Normal request (development HTTP)
        status, data, hdrs = _http_post(f"{base_url}/api/auth/login", {
            "email": "alice@example.com",
            "password": "Password123!",
        })
        assert status == 200
        set_cookie = hdrs.get("Set-Cookie", "")
        assert "Secure" not in set_cookie

        # Secure proxy request (X-Forwarded-Proto: https)
        status_sec, data_sec, hdrs_sec = _http_post(
            f"{base_url}/api/auth/login",
            {"email": "alice@example.com", "password": "Password123!"},
            headers={"X-Forwarded-Proto": "https"},
        )
        assert status_sec == 200
        set_cookie_sec = hdrs_sec.get("Set-Cookie", "")
        assert "Secure" in set_cookie_sec


# -------------------------------------------------------------------
# 2. CSRF Protection
# -------------------------------------------------------------------
class TestCSRFProtection:
    def test_csrf_protection_on_cookie_authenticated_state_changes(self, audit_system):
        """State-changing requests with session cookie require valid X-CSRF-Token."""
        base_url, _ = audit_system

        # Login Alice
        status, login_res, _ = _http_post(f"{base_url}/api/auth/login", {
            "email": "alice@example.com",
            "password": "Password123!",
        })
        assert status == 200
        raw_token = login_res["data"]["session_token"]
        valid_csrf = login_res["data"]["csrf_token"]
        cookie_header = f"session_id={raw_token}"

        order_payload = {
            "restaurant_id": 1,
            "items": [{"item_id": 1, "quantity": 1}],
        }

        # Case A: Cookie present, NO X-CSRF-Token header -> 403 Forbidden
        status_a, res_a, _ = _http_post(
            f"{base_url}/api/orders",
            order_payload,
            headers={"Cookie": cookie_header},
        )
        assert status_a == 403
        assert res_a["error"]["code"] == "CSRF_VERIFICATION_FAILED"

        # Case B: Cookie present, FORGED / INVALID X-CSRF-Token -> 403 Forbidden
        status_b, res_b, _ = _http_post(
            f"{base_url}/api/orders",
            order_payload,
            headers={"Cookie": cookie_header, "X-CSRF-Token": "forged_malicious_token"},
        )
        assert status_b == 403
        assert res_b["error"]["code"] == "CSRF_VERIFICATION_FAILED"

        # Case C: Cookie present, VALID X-CSRF-Token -> 201 Created
        status_c, res_c, _ = _http_post(
            f"{base_url}/api/orders",
            order_payload,
            headers={"Cookie": cookie_header, "X-CSRF-Token": valid_csrf},
        )
        assert status_c == 201
        assert res_c["ok"] is True
        order_id = res_c["data"]["order_id"]

        # Case D: Payment simulation with valid CSRF
        pay_status, pay_res, _ = _http_post(
            f"{base_url}/api/orders/{order_id}/payment",
            {"payment_method": "simulated_upi", "simulate_status": "success"},
            headers={"Cookie": cookie_header, "X-CSRF-Token": valid_csrf},
        )
        assert pay_status == 200
        assert pay_res["ok"] is True

        # Case E: Non-cookie request (unauthenticated or direct API) does not require CSRF
        legacy_status, legacy_res, _ = _http_post(
            f"{base_url}/api/orders",
            {"customer_id": 1, "restaurant_id": 1, "items": [{"item_id": 1, "quantity": 1}]},
        )
        assert legacy_status == 201
        assert legacy_res["ok"] is True


# -------------------------------------------------------------------
# 3. Seed Account Safety & Role Protection
# -------------------------------------------------------------------
class TestSeedAndRoleSafety:
    def test_seed_refuses_to_run_in_production(self, monkeypatch):
        """seed_data() must raise RuntimeError in a production environment."""
        monkeypatch.setenv("ENV", "production")
        with pytest.raises(RuntimeError) as exc_info:
            seed_data()
        assert "Cannot seed default development accounts" in str(exc_info.value)

    def test_admin_account_cannot_be_publicly_registered(self, audit_system):
        """Registering with role 'admin' must be rejected with 403 UNAUTHORIZED_ROLE."""
        base_url, _ = audit_system

        status, data, _ = _http_post(f"{base_url}/api/auth/register", {
            "name": "Attacker Admin",
            "email": "hacker_admin@example.com",
            "password": "Password123!",
            "role": "admin",
        })
        assert status == 403
        assert data["error"]["code"] == "UNAUTHORIZED_ROLE"

    def test_delivery_role_cannot_be_publicly_registered(self, audit_system):
        """Registering with role 'delivery_partner' must also be rejected."""
        base_url, _ = audit_system

        status, data, _ = _http_post(f"{base_url}/api/auth/register", {
            "name": "Attacker Delivery",
            "email": "hacker_delivery@example.com",
            "password": "Password123!",
            "role": "delivery_partner",
        })
        assert status == 403
        assert data["error"]["code"] == "UNAUTHORIZED_ROLE"


# -------------------------------------------------------------------
# 4. Customer Ownership & Cross-Account Authorization
# -------------------------------------------------------------------
class TestCustomerAuthorizationEnforcement:
    def test_customer_cannot_access_or_modify_other_customer_order(self, audit_system):
        """Customer A cannot read, pay, or access Customer B's order details."""
        base_url, _ = audit_system

        # 1. Register Customer A
        _http_post(f"{base_url}/api/auth/register", {
            "name": "Customer Alpha",
            "email": "alpha@test.com",
            "password": "Password123!",
        })
        # Directly verify in DB for testing
        with get_connection() as conn:
            conn.execute("UPDATE users SET email_verified = 1 WHERE email = 'alpha@test.com';")
        _, login_a, _ = _http_post(f"{base_url}/api/auth/login", {
            "email": "alpha@test.com",
            "password": "Password123!",
        })
        token_a = login_a["data"]["session_token"]
        csrf_a = login_a["data"]["csrf_token"]
        user_a_id = login_a["data"]["user"]["user_id"]

        # 2. Register Customer B
        _http_post(f"{base_url}/api/auth/register", {
            "name": "Customer Beta",
            "email": "beta@test.com",
            "password": "Password123!",
        })
        with get_connection() as conn:
            conn.execute("UPDATE users SET email_verified = 1 WHERE email = 'beta@test.com';")
        _, login_b, _ = _http_post(f"{base_url}/api/auth/login", {
            "email": "beta@test.com",
            "password": "Password123!",
        })
        token_b = login_b["data"]["session_token"]
        csrf_b = login_b["data"]["csrf_token"]
        user_b_id = login_b["data"]["user"]["user_id"]

        # 3. Customer B places an order
        _, order_b_res, _ = _http_post(
            f"{base_url}/api/orders",
            {"restaurant_id": 1, "items": [{"item_id": 1, "quantity": 1}]},
            headers={"Cookie": f"session_id={token_b}", "X-CSRF-Token": csrf_b},
        )
        order_b_id = order_b_res["data"]["order_id"]
        assert order_b_res["data"]["customer_id"] == user_b_id

        # 4. Customer A attempts to view Customer B's order -> 403 FORBIDDEN
        status_read, read_res, _ = _http_get(
            f"{base_url}/api/orders/{order_b_id}?customer_id={user_b_id}",
            headers={"Cookie": f"session_id={token_a}"},
        )
        assert status_read == 403
        assert read_res["error"]["code"] == "FORBIDDEN"

        # 5. Customer A attempts to view Customer B's order history -> 403 FORBIDDEN
        status_hist, hist_res, _ = _http_get(
            f"{base_url}/api/customers/{user_b_id}/orders",
            headers={"Cookie": f"session_id={token_a}"},
        )
        assert status_hist == 403
        assert hist_res["error"]["code"] == "FORBIDDEN"

        # 6. Customer A attempts to pay for Customer B's order -> 403 FORBIDDEN
        status_pay, pay_res, _ = _http_post(
            f"{base_url}/api/orders/{order_b_id}/payment",
            {"payment_method": "simulated_upi", "simulate_status": "success"},
            headers={"Cookie": f"session_id={token_a}", "X-CSRF-Token": csrf_a},
        )
        assert status_pay == 403
        assert pay_res["error"]["code"] == "FORBIDDEN"

        # 7. Customer A attempts to view Customer B's refund -> 403 FORBIDDEN
        status_ref, ref_res, _ = _http_get(
            f"{base_url}/api/orders/{order_b_id}/refund",
            headers={"Cookie": f"session_id={token_a}"},
        )
        assert status_ref == 403
        assert ref_res["error"]["code"] == "FORBIDDEN"

        # 8. Customer A attempts to retry Customer B's refund -> 403 FORBIDDEN
        status_retry, retry_res, _ = _http_post(
            f"{base_url}/api/orders/{order_b_id}/refund/retry",
            {},
            headers={"Cookie": f"session_id={token_a}", "X-CSRF-Token": csrf_a},
        )
        assert status_retry == 403
        assert retry_res["error"]["code"] == "FORBIDDEN"

    def test_customer_cannot_access_seller_dashboard_routes(self, audit_system):
        """A customer session attempting to access /api/seller/* must return 403."""
        base_url, _ = audit_system

        _, login_res, _ = _http_post(f"{base_url}/api/auth/login", {
            "email": "alice@example.com",
            "password": "Password123!",
        })
        token = login_res["data"]["session_token"]
        csrf = login_res["data"]["csrf_token"]
        cookie_header = f"session_id={token}"

        # GET seller profile
        status_prof, _, _ = _http_get(
            f"{base_url}/api/seller/profile?seller_id=2",
            headers={"Cookie": cookie_header},
        )
        assert status_prof == 403

        # GET seller orders
        status_ord, _, _ = _http_get(
            f"{base_url}/api/seller/orders?seller_id=2",
            headers={"Cookie": cookie_header},
        )
        assert status_ord == 403

        # POST seller menu item
        status_menu, _, _ = _http_post(
            f"{base_url}/api/seller/menu",
            {"name": "Hacked Item", "price": 100},
            headers={"Cookie": cookie_header, "X-CSRF-Token": csrf},
        )
        assert status_menu == 403


# -------------------------------------------------------------------
# 5. Session Expiration & Revocation
# -------------------------------------------------------------------
class TestSessionLifecycle:
    def test_logout_revokes_session_in_database(self, audit_system):
        """Logout must set revoked_at in sessions table; subsequent requests must fail."""
        base_url, db_path = audit_system

        _, login_res, _ = _http_post(f"{base_url}/api/auth/login", {
            "email": "alice@example.com",
            "password": "Password123!",
        })
        token = login_res["data"]["session_token"]
        csrf = login_res["data"]["csrf_token"]
        token_hash = hash_token(token)

        # Confirm session is active
        with sqlite3.connect(db_path) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute("SELECT revoked_at FROM sessions WHERE session_id = ?;", (token_hash,)).fetchone()
            assert row["revoked_at"] is None

        # Logout
        status, _, _ = _http_post(
            f"{base_url}/api/auth/logout",
            {},
            headers={"Cookie": f"session_id={token}", "X-CSRF-Token": csrf},
        )
        assert status == 200

        # Confirm revoked_at is now set
        with sqlite3.connect(db_path) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute("SELECT revoked_at FROM sessions WHERE session_id = ?;", (token_hash,)).fetchone()
            assert row["revoked_at"] is not None

        # Subsequent /api/auth/me returns 401
        status_me, _, _ = _http_get(f"{base_url}/api/auth/me", headers={"Cookie": f"session_id={token}"})
        assert status_me == 401

    def test_expired_session_is_rejected(self, audit_system):
        """A session with expires_at in the past must be rejected."""
        base_url, db_path = audit_system

        _, login_res, _ = _http_post(f"{base_url}/api/auth/login", {
            "email": "alice@example.com",
            "password": "Password123!",
        })
        token = login_res["data"]["session_token"]
        token_hash = hash_token(token)

        # Manually expire session in DB
        with sqlite3.connect(db_path) as conn:
            conn.execute("UPDATE sessions SET expires_at = datetime('now', '-1 hour') WHERE session_id = ?;", (token_hash,))

        status, _, _ = _http_get(f"{base_url}/api/auth/me", headers={"Cookie": f"session_id={token}"})
        assert status == 401

    def test_unauthenticated_protected_endpoints_rejected(self, audit_system):
        """Unauthenticated requests to protected endpoints return 401."""
        base_url, _ = audit_system

        # /api/auth/me
        s1, _, _ = _http_get(f"{base_url}/api/auth/me")
        assert s1 == 401

        # /api/customers/me/orders
        s2, _, _ = _http_get(f"{base_url}/api/customers/me/orders")
        assert s2 == 401

        # /api/customers/me/refunds
        s3, _, _ = _http_get(f"{base_url}/api/customers/me/refunds")
        assert s3 == 401
