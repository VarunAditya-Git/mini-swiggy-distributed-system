"""
tests/test_v4_1_auth.py
=======================
Comprehensive test suite for Mini Swiggy V4.1 Authentication Foundation:
  1. Cryptographic primitives & password validation (server/auth.py)
  2. Development email adapter & link logging (server/email_adapter.py)
  3. Server application logic for auth actions (server/request_handler.py):
     - register (with email verification generation)
     - verify_email (single-use token verification & expiration)
     - login (credential check, unverified block, session generation)
     - logout (session revocation)
     - get_session (session authentication & user profile)
     - seed identity compatibility (Alice Customer, Bob Seller)
  4. HTTP Gateway auth endpoints (gateway/http_gateway.py):
     - POST /api/auth/register
     - GET  /api/auth/verify-email
     - POST /api/auth/login (HTTP-only session cookie)
     - GET  /api/auth/me (cookie authentication)
     - POST /api/auth/logout (cookie clearing)
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
from server.auth import (
    hash_password,
    verify_password,
    validate_password,
    validate_email,
    generate_token,
    hash_token,
)
from server.email_adapter import DevelopmentEmailAdapter
from server.request_handler import handle_request
from server.tcp_server import serve_client


# -------------------------------------------------------------------
# Fixtures
# -------------------------------------------------------------------
@pytest.fixture
def auth_db(tmp_path, monkeypatch):
    """Initialise an isolated test database with seeds."""
    db_file = tmp_path / "test_auth.db"
    monkeypatch.setattr(db_module, "DB_PATH", db_file)
    init_db()
    seed_data()
    return db_file


@pytest.fixture
def dev_email_adapter(tmp_path):
    log_file = tmp_path / "test_emails.jsonl"
    return DevelopmentEmailAdapter(log_file=log_file)


# -------------------------------------------------------------------
# 1. Cryptographic Primitives & Validation
# -------------------------------------------------------------------
def test_scrypt_hash_and_verify():
    password = "CorrectHorseBattery99!"
    hashed = hash_password(password)

    assert hashed.startswith("scrypt$16384$8$1$")
    assert verify_password(password, hashed) is True
    assert verify_password("WrongPassword123!", hashed) is False
    assert verify_password("", hashed) is False
    assert verify_password(password, "invalid_hash_string") is False
    assert verify_password(password, None) is False


def test_validate_password():
    assert validate_password("Valid1234")[0] is True
    assert validate_password("Short1!")[0] is False  # < 8 chars
    assert validate_password("alllettersnohere")[0] is False  # no digits
    assert validate_password("1234567890")[0] is False  # no letters
    assert validate_password("")[0] is False


def test_validate_email():
    assert validate_email("user@example.com") is True
    assert validate_email("user.name+tag@domain.co.in") is True
    assert validate_email("invalid-email") is False
    assert validate_email("@no-local.com") is False
    assert validate_email("user@") is False
    assert validate_email("") is False


def test_token_generation_and_hashing():
    raw_token, token_hash = generate_token()
    assert len(raw_token) >= 32
    assert len(token_hash) == 64  # SHA-256 hex string
    assert hash_token(raw_token) == token_hash

    # Randomness
    raw_2, hash_2 = generate_token()
    assert raw_token != raw_2
    assert token_hash != hash_2


# -------------------------------------------------------------------
# 2. Development Email Adapter
# -------------------------------------------------------------------
def test_email_adapter_logging(dev_email_adapter):
    to_email = "newuser@example.com"
    verify_url = "http://127.0.0.1:8000/api/auth/verify-email?token=xyz123"

    success = dev_email_adapter.send_verification_email(to_email, verify_url)
    assert success is True

    latest = dev_email_adapter.get_latest_email(to_email)
    assert latest is not None
    assert latest["to"] == to_email
    assert latest["verify_url"] == verify_url
    assert "timestamp" in latest


# -------------------------------------------------------------------
# 3. Server Request Handler Auth Flow
# -------------------------------------------------------------------
def test_register_and_verify_flow(auth_db):
    # 1. Register a new customer
    reg_req = {
        "request_id": "r1",
        "action": "register",
        "payload": {
            "name": "David Tennant",
            "email": "david@tardis.org",
            "password": "TimeLordPassword10!",
            "role": "customer",
        },
    }
    reg_res = handle_request(reg_req)
    assert reg_res["ok"] is True
    assert reg_res["data"]["email"] == "david@tardis.org"
    assert reg_res["data"]["email_verified"] == 0
    raw_token = reg_res["data"]["verification_token"]
    user_id = reg_res["data"]["user_id"]

    # 2. Attempt login before verification -> blocked
    login_unverified = handle_request({
        "request_id": "r2",
        "action": "login",
        "payload": {"email": "david@tardis.org", "password": "TimeLordPassword10!"},
    })
    assert login_unverified["ok"] is False
    assert login_unverified["error"]["code"] == "EMAIL_NOT_VERIFIED"

    # 3. Verify email with raw token
    verify_res = handle_request({
        "request_id": "r3",
        "action": "verify_email",
        "payload": {"token": raw_token},
    })
    assert verify_res["ok"] is True
    assert verify_res["data"]["email_verified"] == 1
    assert verify_res["data"]["user_id"] == user_id

    # 4. Attempt to re-use token -> fails single use
    reuse_res = handle_request({
        "request_id": "r4",
        "action": "verify_email",
        "payload": {"token": raw_token},
    })
    assert reuse_res["ok"] is False
    assert reuse_res["error"]["code"] == "TOKEN_ALREADY_USED"

    # 5. Login successfully after verification
    login_res = handle_request({
        "request_id": "r5",
        "action": "login",
        "payload": {"email": "david@tardis.org", "password": "TimeLordPassword10!"},
    })
    assert login_res["ok"] is True
    session_token = login_res["data"]["session_token"]
    assert session_token is not None
    assert login_res["data"]["user"]["user_id"] == user_id

    # 6. Get session info
    session_res = handle_request({
        "request_id": "r6",
        "action": "get_session",
        "payload": {"session_token": session_token},
    })
    assert session_res["ok"] is True
    assert session_res["data"]["user_id"] == user_id
    assert session_res["data"]["email"] == "david@tardis.org"

    # 7. Logout
    logout_res = handle_request({
        "request_id": "r7",
        "action": "logout",
        "payload": {"session_token": session_token},
    })
    assert logout_res["ok"] is True

    # 8. Get session after logout -> UNAUTHORIZED
    revoked_res = handle_request({
        "request_id": "r8",
        "action": "get_session",
        "payload": {"session_token": session_token},
    })
    assert revoked_res["ok"] is False
    assert revoked_res["error"]["code"] == "UNAUTHORIZED"


def test_register_duplicate_email(auth_db):
    req = {
        "request_id": "r1",
        "action": "register",
        "payload": {
            "name": "Alice Duplicate",
            "email": "alice@example.com",  # Already in seed data
            "password": "Password123!",
            "role": "customer",
        },
    }
    res = handle_request(req)
    assert res["ok"] is False
    assert res["error"]["code"] == "EMAIL_ALREADY_EXISTS"


def test_register_disallowed_role(auth_db):
    req = {
        "request_id": "r1",
        "action": "register",
        "payload": {
            "name": "Malicious Admin Wannabe",
            "email": "wannabe@admin.com",
            "password": "Password123!",
            "role": "admin",
        },
    }
    res = handle_request(req)
    assert res["ok"] is False
    assert res["error"]["code"] == "UNAUTHORIZED_ROLE"


def test_verify_email_invalid_token(auth_db):
    res = handle_request({
        "request_id": "r1",
        "action": "verify_email",
        "payload": {"token": "completely_fake_token_value_999"},
    })
    assert res["ok"] is False
    assert res["error"]["code"] == "INVALID_TOKEN"


def test_verify_email_expired_token(auth_db):
    # Register user
    reg = handle_request({
        "request_id": "r1",
        "action": "register",
        "payload": {
            "name": "Rip Van Winkle",
            "email": "rip@winkle.org",
            "password": "Password123!",
        },
    })
    raw_token = reg["data"]["verification_token"]
    t_hash = hash_token(raw_token)

    # Manually expire the token in database
    with get_connection() as conn:
        conn.execute(
            "UPDATE email_verification_tokens SET expires_at = datetime('now', '-2 days') WHERE token_hash = ?;",
            (t_hash,),
        )

    res = handle_request({
        "request_id": "r2",
        "action": "verify_email",
        "payload": {"token": raw_token},
    })
    assert res["ok"] is False
    assert res["error"]["code"] == "TOKEN_EXPIRED"


def test_login_invalid_password(auth_db):
    res = handle_request({
        "request_id": "r1",
        "action": "login",
        "payload": {"email": "alice@example.com", "password": "WrongPassword999!"},
    })
    assert res["ok"] is False
    assert res["error"]["code"] == "INVALID_CREDENTIALS"


def test_seed_identities_can_login(auth_db):
    # Verify Alice (ID 1) Customer can log in
    alice_login = handle_request({
        "request_id": "r1",
        "action": "login",
        "payload": {"email": "alice@example.com", "password": DEFAULT_SEED_PASSWORD},
    })
    assert alice_login["ok"] is True
    assert alice_login["data"]["user"]["user_id"] == 1
    assert alice_login["data"]["user"]["role"] == "customer"

    # Verify Bob (ID 2) Seller can log in
    bob_login = handle_request({
        "request_id": "r2",
        "action": "login",
        "payload": {"email": "bob@example.com", "password": DEFAULT_SEED_PASSWORD},
    })
    assert bob_login["ok"] is True
    assert bob_login["data"]["user"]["user_id"] == 2
    assert bob_login["data"]["user"]["role"] == "seller"


# -------------------------------------------------------------------
# 4. HTTP Gateway End-to-End Auth Tests
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
    db_file = tmp_path / "test_gateway_auth.db"
    monkeypatch.setattr(db_module, "DB_PATH", db_file)
    init_db()
    seed_data()

    # Start TCP server
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


def test_gateway_auth_full_lifecycle(running_stack):
    base_url = running_stack

    # 1. Register new user via POST /api/auth/register
    reg_payload = json.dumps({
        "name": "Rose Tyler",
        "email": "rose@badwolf.com",
        "password": "Password123!",
        "role": "customer",
    }).encode("utf-8")

    req = urllib.request.Request(
        f"{base_url}/api/auth/register",
        data=reg_payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req) as resp:
        assert resp.status == 201
        data = json.loads(resp.read().decode("utf-8"))
        assert data["ok"] is True
        raw_token = data["data"]["verification_token"]

    # 2. Verify email via GET /api/auth/verify-email?token=...
    with urllib.request.urlopen(f"{base_url}/api/auth/verify-email?token={raw_token}") as resp:
        assert resp.status == 200
        verify_data = json.loads(resp.read().decode("utf-8"))
        assert verify_data["ok"] is True
        assert verify_data["data"]["email_verified"] == 1

    # 3. Login via POST /api/auth/login and verify Set-Cookie
    login_payload = json.dumps({
        "email": "rose@badwolf.com",
        "password": "Password123!",
    }).encode("utf-8")

    login_req = urllib.request.Request(
        f"{base_url}/api/auth/login",
        data=login_payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(login_req) as resp:
        assert resp.status == 200
        login_data = json.loads(resp.read().decode("utf-8"))
        assert login_data["ok"] is True

        cookie_header = resp.headers.get("Set-Cookie")
        assert cookie_header is not None
        assert "session_id=" in cookie_header
        assert "HttpOnly" in cookie_header
        assert "SameSite=Strict" in cookie_header

        # Extract cookie value and CSRF token
        cookie_val = cookie_header.split(";")[0]
        csrf_token = login_data["data"]["csrf_token"]

    # 4. Access GET /api/auth/me without cookie -> 401 Unauthorized
    with pytest.raises(urllib.error.HTTPError) as exc_info:
        urllib.request.urlopen(f"{base_url}/api/auth/me")
    assert exc_info.value.code == 401

    # 5. Access GET /api/auth/me with session cookie -> 200 OK
    me_req = urllib.request.Request(
        f"{base_url}/api/auth/me",
        headers={"Cookie": cookie_val},
        method="GET",
    )
    with urllib.request.urlopen(me_req) as resp:
        assert resp.status == 200
        me_data = json.loads(resp.read().decode("utf-8"))
        assert me_data["ok"] is True
        assert me_data["data"]["email"] == "rose@badwolf.com"
        assert me_data["data"]["name"] == "Rose Tyler"

    # 6. Logout via POST /api/auth/logout with CSRF token -> clears cookie (Max-Age=0)
    logout_req = urllib.request.Request(
        f"{base_url}/api/auth/logout",
        headers={"Cookie": cookie_val, "X-CSRF-Token": csrf_token, "Content-Length": "0"},
        method="POST",
    )
    with urllib.request.urlopen(logout_req) as resp:
        assert resp.status == 200
        logout_cookie = resp.headers.get("Set-Cookie")
        assert logout_cookie is not None
        assert "Max-Age=0" in logout_cookie

    # 7. Access GET /api/auth/me with revoked session cookie -> 401 Unauthorized
    with pytest.raises(urllib.error.HTTPError) as exc_info:
        urllib.request.urlopen(me_req)
    assert exc_info.value.code == 401

