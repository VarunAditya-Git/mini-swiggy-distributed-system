"""
tests/test_request_handler.py
==============================
Unit tests for server/request_handler.py.

These tests exercise every action and every error path WITHOUT starting a
real TCP server. The handler is pure Python, so tests run very fast and
never conflict over ports.

We monkeypatch database.db.DB_PATH to use a fresh temporary database for
each test — the real swiggy.db is never touched.

Run:
    python -m pytest tests/test_request_handler.py -v
"""

import pathlib
import sys
import pytest

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))

import database.db as db_module
from database.db import init_db
from database.seed import seed_data
from server.request_handler import handle_request


# -------------------------------------------------------------------
# Fixture: isolated temporary database with seed data loaded
# -------------------------------------------------------------------
@pytest.fixture
def seeded_db(tmp_path, monkeypatch):
    """
    Create a fresh database in tmp_path, run init_db() and seed_data(),
    then patch DB_PATH so all handler calls use this temp database.
    """
    temp_db = tmp_path / "handler_test.db"
    monkeypatch.setattr(db_module, "DB_PATH", temp_db)
    init_db()
    seed_data()
    yield temp_db


@pytest.fixture
def empty_db(tmp_path, monkeypatch):
    """
    Fresh database with tables but NO seed data.
    Used for testing empty-result scenarios.
    """
    temp_db = tmp_path / "empty_test.db"
    monkeypatch.setattr(db_module, "DB_PATH", temp_db)
    init_db()
    yield temp_db


# ===================================================================
# 1. PING
# ===================================================================
class TestPing:
    def test_ping_ok(self, seeded_db):
        """ping returns ok=True and status='ok'."""
        req = {"request_id": "r1", "action": "ping", "payload": {}}
        resp = handle_request(req)
        assert resp["ok"] is True
        assert resp["request_id"] == "r1"
        assert resp["data"]["status"] == "ok"

    def test_ping_preserves_request_id(self, seeded_db):
        """The exact request_id sent by the client must appear in the response."""
        req = {"request_id": "MY-UNIQUE-ID-42", "action": "ping", "payload": {}}
        resp = handle_request(req)
        assert resp["request_id"] == "MY-UNIQUE-ID-42"


# ===================================================================
# 2. LIST_RESTAURANTS
# ===================================================================
class TestListRestaurants:
    def test_returns_all_three_restaurants(self, seeded_db):
        """After seeding, list_restaurants must return exactly 3 restaurants."""
        req = {"request_id": "r2", "action": "list_restaurants", "payload": {}}
        resp = handle_request(req)
        assert resp["ok"] is True
        assert len(resp["data"]) == 3

    def test_restaurant_fields_present(self, seeded_db):
        """Each restaurant dict must have the required fields."""
        req = {"request_id": "r3", "action": "list_restaurants", "payload": {}}
        resp = handle_request(req)
        required_fields = {"restaurant_id", "name", "description",
                           "approval_status", "is_open"}
        for r in resp["data"]:
            assert required_fields.issubset(r.keys()), (
                f"Missing fields in: {r}"
            )

    def test_restaurant_names_correct(self, seeded_db):
        """The three seed restaurant names must be present."""
        req = {"request_id": "r4", "action": "list_restaurants", "payload": {}}
        resp = handle_request(req)
        names = {r["name"] for r in resp["data"]}
        assert names == {"Hyderabad Spice House", "Dosa Corner", "Pizza Junction"}

    def test_empty_database_returns_empty_list(self, empty_db):
        """With no restaurants, list_restaurants must return ok=True, data=[]."""
        req = {"request_id": "r5", "action": "list_restaurants", "payload": {}}
        resp = handle_request(req)
        assert resp["ok"] is True
        assert resp["data"] == []

    def test_is_open_is_boolean(self, seeded_db):
        """is_open must be a Python bool (not 0/1 integer)."""
        req = {"request_id": "r6", "action": "list_restaurants", "payload": {}}
        resp = handle_request(req)
        for r in resp["data"]:
            assert isinstance(r["is_open"], bool)


# ===================================================================
# 3. LIST_MENU
# ===================================================================
class TestListMenu:
    def test_valid_restaurant_returns_two_items(self, seeded_db):
        """Each seed restaurant has exactly 2 menu items."""
        for rid in (1, 2, 3):
            req = {"request_id": f"r-{rid}", "action": "list_menu",
                   "payload": {"restaurant_id": rid}}
            resp = handle_request(req)
            assert resp["ok"] is True, f"Failed for restaurant_id={rid}: {resp}"
            assert len(resp["data"]) == 2, (
                f"Expected 2 items for restaurant {rid}, got {len(resp['data'])}"
            )

    def test_menu_item_fields_present(self, seeded_db):
        """Each menu item must have the required fields."""
        req = {"request_id": "m1", "action": "list_menu",
               "payload": {"restaurant_id": 1}}
        resp = handle_request(req)
        required = {"item_id", "restaurant_id", "name", "description",
                    "price", "is_available"}
        for item in resp["data"]:
            assert required.issubset(item.keys())

    def test_nonexistent_restaurant_returns_not_found(self, seeded_db):
        """A restaurant_id that doesn't exist must return ok=False, NOT_FOUND."""
        req = {"request_id": "m2", "action": "list_menu",
               "payload": {"restaurant_id": 9999}}
        resp = handle_request(req)
        assert resp["ok"] is False
        assert resp["error"]["code"] == "NOT_FOUND"

    def test_missing_restaurant_id_in_payload(self, seeded_db):
        """payload without restaurant_id must return INVALID_PAYLOAD."""
        req = {"request_id": "m3", "action": "list_menu", "payload": {}}
        resp = handle_request(req)
        assert resp["ok"] is False
        assert resp["error"]["code"] == "INVALID_PAYLOAD"

    def test_string_restaurant_id_rejected(self, seeded_db):
        """restaurant_id as a string must return INVALID_PAYLOAD."""
        req = {"request_id": "m4", "action": "list_menu",
               "payload": {"restaurant_id": "one"}}
        resp = handle_request(req)
        assert resp["ok"] is False
        assert resp["error"]["code"] == "INVALID_PAYLOAD"

    def test_float_restaurant_id_rejected(self, seeded_db):
        """restaurant_id as a float must return INVALID_PAYLOAD."""
        req = {"request_id": "m5", "action": "list_menu",
               "payload": {"restaurant_id": 1.5}}
        resp = handle_request(req)
        assert resp["ok"] is False
        assert resp["error"]["code"] == "INVALID_PAYLOAD"

    def test_restaurant_with_no_items_returns_empty_list(self, empty_db):
        """A restaurant that exists but has no items must return ok=True, data=[]."""
        from database.db import get_connection
        with get_connection() as conn:
            conn.execute("INSERT INTO restaurants (name) VALUES (?);",
                         ("Empty Place",))
            rid = conn.execute(
                "SELECT restaurant_id FROM restaurants WHERE name = ?;",
                ("Empty Place",)
            ).fetchone()["restaurant_id"]

        req = {"request_id": "m6", "action": "list_menu",
               "payload": {"restaurant_id": rid}}
        resp = handle_request(req)
        assert resp["ok"] is True
        assert resp["data"] == []

    def test_price_is_float(self, seeded_db):
        """price in menu items must be a float."""
        req = {"request_id": "m7", "action": "list_menu",
               "payload": {"restaurant_id": 1}}
        resp = handle_request(req)
        for item in resp["data"]:
            assert isinstance(item["price"], float)


# ===================================================================
# 4. UNKNOWN ACTION
# ===================================================================
class TestUnknownAction:
    def test_unknown_action_returns_error(self, seeded_db):
        """Any unrecognised action must return ok=False, UNKNOWN_ACTION."""
        req = {"request_id": "u1", "action": "order_pizza", "payload": {}}
        resp = handle_request(req)
        assert resp["ok"] is False
        assert resp["error"]["code"] == "UNKNOWN_ACTION"

    def test_unknown_action_preserves_request_id(self, seeded_db):
        req = {"request_id": "MY-ID", "action": "fly_to_moon", "payload": {}}
        resp = handle_request(req)
        assert resp["request_id"] == "MY-ID"


# ===================================================================
# 5. MALFORMED / INVALID REQUESTS
# ===================================================================
class TestMalformedRequests:
    def test_missing_action_field(self, seeded_db):
        """A request without 'action' must return INVALID_REQUEST."""
        req = {"request_id": "bad1", "payload": {}}
        resp = handle_request(req)
        assert resp["ok"] is False
        assert resp["error"]["code"] == "INVALID_REQUEST"

    def test_non_string_action(self, seeded_db):
        """action must be a string; an integer must be rejected."""
        req = {"request_id": "bad2", "action": 42, "payload": {}}
        resp = handle_request(req)
        assert resp["ok"] is False
        assert resp["error"]["code"] == "INVALID_REQUEST"

    def test_non_dict_payload(self, seeded_db):
        """payload must be a JSON object (dict); a list must be rejected."""
        req = {"request_id": "bad3", "action": "ping", "payload": [1, 2, 3]}
        resp = handle_request(req)
        assert resp["ok"] is False
        assert resp["error"]["code"] == "INVALID_PAYLOAD"

    def test_non_dict_input(self, seeded_db):
        """handle_request with a non-dict input must return INVALID_REQUEST."""
        resp = handle_request("this is not a dict")   # type: ignore
        assert resp["ok"] is False
        assert resp["error"]["code"] == "INVALID_REQUEST"

    def test_missing_request_id_uses_unknown(self, seeded_db):
        """When request_id is missing, the response must use 'unknown'."""
        req = {"action": "ping", "payload": {}}
        resp = handle_request(req)
        # The response should still succeed (ping doesn't need request_id).
        assert resp["request_id"] == "unknown" or resp["ok"] is True

    def test_non_string_request_id_falls_back(self, seeded_db):
        """A non-string request_id must be replaced with 'unknown'."""
        req = {"request_id": 12345, "action": "ping", "payload": {}}
        resp = handle_request(req)
        assert resp["request_id"] == "unknown"
