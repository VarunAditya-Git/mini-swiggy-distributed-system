"""
tests/test_v2_order_handler.py
==============================
Unit tests for Mini Swiggy V2 core ordering and payment actions:
  - create_order
  - simulate_payment
  - get_order
  - get_order_history

These tests test the application logic directly via handle_request()
without starting a TCP socket server.
"""

import pytest
import database.db as db_module
from database.db import init_db, get_connection
from database.seed import seed_data
from server.request_handler import (
    handle_request,
    ERR_INVALID_PAYLOAD,
    ERR_INVALID_REQUEST,
    ERR_NOT_FOUND,
    ERR_ITEM_UNAVAILABLE,
    ERR_RESTAURANT_CLOSED,
    ERR_PAYMENT_ALREADY_COMPLETED,
)


# -------------------------------------------------------------------
# Fixtures
# -------------------------------------------------------------------
@pytest.fixture
def seeded_db(tmp_path, monkeypatch):
    """
    Provide an isolated, seeded SQLite database for each test.
    Monkeypatches db_module.DB_PATH so queries hit the temporary file.
    """
    temp_db = tmp_path / "test_v2.db"
    monkeypatch.setattr(db_module, "DB_PATH", temp_db)
    init_db()
    seed_data()
    yield temp_db


# -------------------------------------------------------------------
# Tests for Action: create_order
# -------------------------------------------------------------------
class TestCreateOrder:

    def test_valid_order_creation(self, seeded_db):
        """Valid order creation calculates total on server and snapshots prices."""
        req = {
            "request_id": "ord-01",
            "action": "create_order",
            "payload": {
                "customer_id": 1,
                "restaurant_id": 1,
                "items": [
                    {"item_id": 1, "quantity": 2},  # Chicken Biryani @ 180 = 360
                    {"item_id": 2, "quantity": 1},  # Veg Biryani @ 120 = 120
                ],
            },
        }
        res = handle_request(req)
        assert res["ok"] is True
        assert res["request_id"] == "ord-01"

        data = res["data"]
        assert data["customer_id"] == 1
        assert data["restaurant_id"] == 1
        assert data["restaurant_name"] == "Hyderabad Spice House"
        assert data["total_amount"] == 480.0
        assert data["order_status"] == "placed"
        assert data["payment_status"] == "pending"
        assert len(data["items"]) == 2

        # Verify persisted in database
        with get_connection() as conn:
            ord_row = conn.execute(
                "SELECT * FROM orders WHERE order_id = ?;", (data["order_id"],)
            ).fetchone()
            assert ord_row is not None
            assert ord_row["total_amount"] == 480.0
            assert ord_row["order_status"] == "placed"
            assert ord_row["payment_status"] == "pending"

            item_rows = conn.execute(
                "SELECT * FROM order_items WHERE order_id = ? ORDER BY item_id;",
                (data["order_id"],),
            ).fetchall()
            assert len(item_rows) == 2
            assert item_rows[0]["quantity"] == 2
            assert item_rows[0]["unit_price"] == 180.0
            assert item_rows[1]["quantity"] == 1
            assert item_rows[1]["unit_price"] == 120.0

    def test_empty_cart_rejected(self, seeded_db):
        """An empty items list must be rejected."""
        req = {
            "request_id": "ord-empty",
            "action": "create_order",
            "payload": {"customer_id": 1, "restaurant_id": 1, "items": []},
        }
        res = handle_request(req)
        assert res["ok"] is False
        assert res["error"]["code"] == ERR_INVALID_PAYLOAD
        assert "empty" in res["error"]["message"].lower()

    def test_missing_items_payload(self, seeded_db):
        """Missing items in payload must be rejected."""
        req = {
            "request_id": "ord-no-items",
            "action": "create_order",
            "payload": {"customer_id": 1, "restaurant_id": 1},
        }
        res = handle_request(req)
        assert res["ok"] is False
        assert res["error"]["code"] == ERR_INVALID_PAYLOAD

    def test_invalid_quantity_zero(self, seeded_db):
        """Quantity 0 must be rejected."""
        req = {
            "request_id": "ord-qty-0",
            "action": "create_order",
            "payload": {
                "customer_id": 1,
                "restaurant_id": 1,
                "items": [{"item_id": 1, "quantity": 0}],
            },
        }
        res = handle_request(req)
        assert res["ok"] is False
        assert res["error"]["code"] == ERR_INVALID_PAYLOAD
        assert "positive integer" in res["error"]["message"]

    def test_invalid_quantity_negative(self, seeded_db):
        """Negative quantity must be rejected."""
        req = {
            "request_id": "ord-qty-neg",
            "action": "create_order",
            "payload": {
                "customer_id": 1,
                "restaurant_id": 1,
                "items": [{"item_id": 1, "quantity": -3}],
            },
        }
        res = handle_request(req)
        assert res["ok"] is False
        assert res["error"]["code"] == ERR_INVALID_PAYLOAD

    def test_invalid_quantity_float(self, seeded_db):
        """Float quantity must be rejected."""
        req = {
            "request_id": "ord-qty-flt",
            "action": "create_order",
            "payload": {
                "customer_id": 1,
                "restaurant_id": 1,
                "items": [{"item_id": 1, "quantity": 1.5}],
            },
        }
        res = handle_request(req)
        assert res["ok"] is False
        assert res["error"]["code"] == ERR_INVALID_PAYLOAD

    def test_nonexistent_customer(self, seeded_db):
        """Nonexistent customer ID returns NOT_FOUND."""
        req = {
            "request_id": "ord-no-cust",
            "action": "create_order",
            "payload": {
                "customer_id": 9999,
                "restaurant_id": 1,
                "items": [{"item_id": 1, "quantity": 1}],
            },
        }
        res = handle_request(req)
        assert res["ok"] is False
        assert res["error"]["code"] == ERR_NOT_FOUND

    def test_inactive_customer_rejected(self, seeded_db):
        """Customer with is_active=0 cannot place orders."""
        with get_connection() as conn:
            conn.execute("UPDATE users SET is_active = 0 WHERE user_id = 1;")

        req = {
            "request_id": "ord-inactive-cust",
            "action": "create_order",
            "payload": {
                "customer_id": 1,
                "restaurant_id": 1,
                "items": [{"item_id": 1, "quantity": 1}],
            },
        }
        res = handle_request(req)
        assert res["ok"] is False
        assert res["error"]["code"] == ERR_INVALID_REQUEST
        assert "inactive" in res["error"]["message"]

    def test_nonexistent_restaurant(self, seeded_db):
        """Nonexistent restaurant ID returns NOT_FOUND."""
        req = {
            "request_id": "ord-no-rest",
            "action": "create_order",
            "payload": {
                "customer_id": 1,
                "restaurant_id": 9999,
                "items": [{"item_id": 1, "quantity": 1}],
            },
        }
        res = handle_request(req)
        assert res["ok"] is False
        assert res["error"]["code"] == ERR_NOT_FOUND

    def test_closed_restaurant_rejected(self, seeded_db):
        """Closed restaurant (is_open=0) cannot accept orders."""
        with get_connection() as conn:
            conn.execute("UPDATE restaurants SET is_open = 0 WHERE restaurant_id = 1;")

        req = {
            "request_id": "ord-closed-rest",
            "action": "create_order",
            "payload": {
                "customer_id": 1,
                "restaurant_id": 1,
                "items": [{"item_id": 1, "quantity": 1}],
            },
        }
        res = handle_request(req)
        assert res["ok"] is False
        assert res["error"]["code"] == ERR_RESTAURANT_CLOSED

    def test_nonexistent_menu_item(self, seeded_db):
        """Ordering a menu item that doesn't exist returns NOT_FOUND."""
        req = {
            "request_id": "ord-no-item",
            "action": "create_order",
            "payload": {
                "customer_id": 1,
                "restaurant_id": 1,
                "items": [{"item_id": 9999, "quantity": 1}],
            },
        }
        res = handle_request(req)
        assert res["ok"] is False
        assert res["error"]["code"] == ERR_NOT_FOUND

    def test_item_from_wrong_restaurant_rejected(self, seeded_db):
        """Items in the cart must belong to the selected restaurant."""
        # Item 3 is Masala Dosa from restaurant 2 (Dosa Corner)
        req = {
            "request_id": "ord-wrong-rest",
            "action": "create_order",
            "payload": {
                "customer_id": 1,
                "restaurant_id": 1,  # Hyderabad Spice House
                "items": [{"item_id": 3, "quantity": 1}],
            },
        }
        res = handle_request(req)
        assert res["ok"] is False
        assert res["error"]["code"] == ERR_INVALID_REQUEST
        assert "not restaurant 1" in res["error"]["message"]

    def test_unavailable_item_rejected(self, seeded_db):
        """Ordering an unavailable item (is_available=0) is rejected."""
        with get_connection() as conn:
            conn.execute("UPDATE menu_items SET is_available = 0 WHERE item_id = 1;")

        req = {
            "request_id": "ord-unavail",
            "action": "create_order",
            "payload": {
                "customer_id": 1,
                "restaurant_id": 1,
                "items": [{"item_id": 1, "quantity": 1}],
            },
        }
        res = handle_request(req)
        assert res["ok"] is False
        assert res["error"]["code"] == ERR_ITEM_UNAVAILABLE

    def test_server_ignores_client_tampered_price(self, seeded_db):
        """Server MUST calculate total from SQLite prices, ignoring client price tampering."""
        req = {
            "request_id": "ord-tamper",
            "action": "create_order",
            "payload": {
                "customer_id": 1,
                "restaurant_id": 1,
                "items": [
                    {"item_id": 1, "quantity": 2, "price": 0.01},  # Attempting to pay 0.01
                ],
            },
        }
        res = handle_request(req)
        assert res["ok"] is True
        # Official price in DB is 180.0, so 2 * 180.0 = 360.0
        assert res["data"]["total_amount"] == 360.0
        assert res["data"]["items"][0]["unit_price"] == 180.0

    def test_duplicate_items_consolidated(self, seeded_db):
        """Duplicate item_ids in the cart are consolidated and quantities summed."""
        req = {
            "request_id": "ord-dup",
            "action": "create_order",
            "payload": {
                "customer_id": 1,
                "restaurant_id": 1,
                "items": [
                    {"item_id": 1, "quantity": 1},
                    {"item_id": 1, "quantity": 2},
                ],
            },
        }
        res = handle_request(req)
        assert res["ok"] is True
        data = res["data"]
        # Consolidated quantity is 3; 3 * 180.0 = 540.0
        assert data["total_amount"] == 540.0
        assert len(data["items"]) == 1
        assert data["items"][0]["quantity"] == 3

    def test_price_snapshot_preserved_after_future_menu_price_change(self, seeded_db):
        """A future price change in menu_items must not alter the unit_price of already-placed orders."""
        # 1. Place order at original price (180.0)
        req = {
            "request_id": "ord-snap",
            "action": "create_order",
            "payload": {
                "customer_id": 1,
                "restaurant_id": 1,
                "items": [{"item_id": 1, "quantity": 1}],
            },
        }
        res = handle_request(req)
        order_id = res["data"]["order_id"]
        assert res["data"]["total_amount"] == 180.0

        # 2. Restaurant changes Chicken Biryani price from 180 to 250
        with get_connection() as conn:
            conn.execute("UPDATE menu_items SET price = 250.0 WHERE item_id = 1;")

        # 3. Retrieve order — order snapshot price and total must remain 180.0
        get_res = handle_request({
            "request_id": "ord-get",
            "action": "get_order",
            "payload": {"order_id": order_id},
        })
        assert get_res["ok"] is True
        assert get_res["data"]["total_amount"] == 180.0
        assert get_res["data"]["items"][0]["unit_price"] == 180.0

    def test_database_transaction_rollback_on_partial_failure(self, seeded_db):
        """If an error occurs while writing order_items, the order row must be rolled back."""
        # Create a trigger that aborts when inserting into order_items for item_id 2
        with get_connection() as conn:
            conn.execute("""
                CREATE TRIGGER fail_item_2 BEFORE INSERT ON order_items
                WHEN NEW.item_id = 2
                BEGIN
                    SELECT RAISE(ABORT, 'Simulated failure during order item insert');
                END;
            """)

        req = {
            "request_id": "ord-rollback",
            "action": "create_order",
            "payload": {
                "customer_id": 1,
                "restaurant_id": 1,
                "items": [
                    {"item_id": 1, "quantity": 1},
                    {"item_id": 2, "quantity": 1},
                ],
            },
        }
        res = handle_request(req)
        assert res["ok"] is False
        assert res["error"]["code"] == "DB_ERROR"

        # Verify no orphan order was left behind in the orders table
        with get_connection() as conn:
            orders = conn.execute("SELECT * FROM orders;").fetchall()
            assert len(orders) == 0


# -------------------------------------------------------------------
# Tests for Action: simulate_payment
# -------------------------------------------------------------------
class TestSimulatePayment:

    def _create_sample_order(self) -> int:
        res = handle_request({
            "request_id": "setup",
            "action": "create_order",
            "payload": {
                "customer_id": 1,
                "restaurant_id": 1,
                "items": [{"item_id": 1, "quantity": 1}],
            },
        })
        return res["data"]["order_id"]

    def test_successful_payment(self, seeded_db):
        """Successful payment marks order as accepted and payment as paid."""
        order_id = self._create_sample_order()

        pay_req = {
            "request_id": "pay-01",
            "action": "simulate_payment",
            "payload": {
                "order_id": order_id,
                "payment_method": "upi",
                "simulate_status": "paid",
            },
        }
        res = handle_request(pay_req)
        assert res["ok"] is True
        data = res["data"]
        assert data["order_id"] == order_id
        assert data["payment_status"] == "paid"
        assert data["order_status"] == "accepted"
        assert data["amount"] == 180.0
        assert data["transaction_ref"].startswith("TXN-SIM-")

        # Verify DB state
        with get_connection() as conn:
            ord_row = conn.execute(
                "SELECT order_status, payment_status FROM orders WHERE order_id = ?;",
                (order_id,),
            ).fetchone()
            assert ord_row["payment_status"] == "paid"
            assert ord_row["order_status"] == "accepted"

            pay_row = conn.execute(
                "SELECT * FROM payments WHERE order_id = ?;", (order_id,)
            ).fetchone()
            assert pay_row["payment_status"] == "paid"
            assert pay_row["amount"] == 180.0

    def test_failed_payment_simulation(self, seeded_db):
        """Failed payment records failure and DOES NOT mark order as paid."""
        order_id = self._create_sample_order()

        pay_req = {
            "request_id": "pay-fail",
            "action": "simulate_payment",
            "payload": {
                "order_id": order_id,
                "simulate_status": "failed",
            },
        }
        res = handle_request(pay_req)
        assert res["ok"] is True
        assert res["data"]["payment_status"] == "failed"
        assert res["data"]["order_status"] == "placed"  # remains placed

        # Verify DB: MUST NOT be paid!
        with get_connection() as conn:
            ord_row = conn.execute(
                "SELECT order_status, payment_status FROM orders WHERE order_id = ?;",
                (order_id,),
            ).fetchone()
            assert ord_row["payment_status"] == "failed"
            assert ord_row["order_status"] == "placed"

            pay_row = conn.execute(
                "SELECT payment_status FROM payments WHERE order_id = ?;", (order_id,)
            ).fetchone()
            assert pay_row["payment_status"] == "failed"

    def test_retry_payment_after_failure(self, seeded_db):
        """A customer can retry payment after an initial failure."""
        order_id = self._create_sample_order()

        # 1. Fail first
        handle_request({
            "request_id": "p1",
            "action": "simulate_payment",
            "payload": {"order_id": order_id, "simulate_failure": True},
        })

        # 2. Retry with success
        res = handle_request({
            "request_id": "p2",
            "action": "simulate_payment",
            "payload": {"order_id": order_id, "simulate_status": "paid"},
        })
        assert res["ok"] is True
        assert res["data"]["payment_status"] == "paid"
        assert res["data"]["order_status"] == "accepted"

    def test_repeated_payment_on_already_paid_order_rejected(self, seeded_db):
        """An already-paid order cannot be charged a second time (idempotent / duplicate guard)."""
        order_id = self._create_sample_order()

        # 1. First payment succeeds
        res1 = handle_request({
            "request_id": "p1",
            "action": "simulate_payment",
            "payload": {"order_id": order_id},
        })
        assert res1["ok"] is True
        assert res1["data"]["payment_status"] == "paid"

        # 2. Second payment attempt on same order must be rejected
        res2 = handle_request({
            "request_id": "p2",
            "action": "simulate_payment",
            "payload": {"order_id": order_id},
        })
        assert res2["ok"] is False
        assert res2["error"]["code"] == ERR_PAYMENT_ALREADY_COMPLETED
        assert "already been paid" in res2["error"]["message"]

    def test_invalid_order_id(self, seeded_db):
        """Payment for a non-existent order returns NOT_FOUND."""
        res = handle_request({
            "request_id": "pay-no-ord",
            "action": "simulate_payment",
            "payload": {"order_id": 9999},
        })
        assert res["ok"] is False
        assert res["error"]["code"] == ERR_NOT_FOUND

    def test_missing_order_id(self, seeded_db):
        """Payload missing order_id returns INVALID_PAYLOAD."""
        res = handle_request({
            "request_id": "pay-no-id",
            "action": "simulate_payment",
            "payload": {},
        })
        assert res["ok"] is False
        assert res["error"]["code"] == ERR_INVALID_PAYLOAD

    def test_string_order_id_rejected(self, seeded_db):
        """String order_id returns INVALID_PAYLOAD."""
        res = handle_request({
            "request_id": "pay-str-id",
            "action": "simulate_payment",
            "payload": {"order_id": "1"},
        })
        assert res["ok"] is False
        assert res["error"]["code"] == ERR_INVALID_PAYLOAD

    def test_payment_for_cancelled_order_rejected(self, seeded_db):
        """Cancelled orders cannot be paid for."""
        order_id = self._create_sample_order()
        with get_connection() as conn:
            conn.execute("UPDATE orders SET order_status = 'cancelled' WHERE order_id = ?;", (order_id,))

        res = handle_request({
            "request_id": "pay-canc",
            "action": "simulate_payment",
            "payload": {"order_id": order_id},
        })
        assert res["ok"] is False
        assert res["error"]["code"] == ERR_INVALID_REQUEST
        assert "cancelled" in res["error"]["message"]


# -------------------------------------------------------------------
# Tests for Action: get_order
# -------------------------------------------------------------------
class TestGetOrder:

    def test_get_existing_order_with_items_and_payment(self, seeded_db):
        """Retrieves full order snapshot including items and payment details."""
        # Create order
        create_res = handle_request({
            "request_id": "c1",
            "action": "create_order",
            "payload": {
                "customer_id": 1,
                "restaurant_id": 1,
                "items": [{"item_id": 1, "quantity": 2}],
            },
        })
        order_id = create_res["data"]["order_id"]

        # Pay for order
        handle_request({
            "request_id": "p1",
            "action": "simulate_payment",
            "payload": {"order_id": order_id, "payment_method": "upi"},
        })

        # Get order
        res = handle_request({
            "request_id": "g1",
            "action": "get_order",
            "payload": {"order_id": order_id},
        })
        assert res["ok"] is True
        data = res["data"]
        assert data["order_id"] == order_id
        assert data["customer_id"] == 1
        assert data["customer_name"] == "Alice Customer"
        assert data["restaurant_name"] == "Hyderabad Spice House"
        assert data["total_amount"] == 360.0
        assert data["order_status"] == "accepted"
        assert data["payment_status"] == "paid"
        assert len(data["items"]) == 1
        assert data["items"][0]["name"] == "Chicken Biryani"
        assert data["items"][0]["quantity"] == 2
        assert data["items"][0]["unit_price"] == 180.0
        assert data["items"][0]["subtotal"] == 360.0

        # Payment record is populated
        assert data["payment"] is not None
        assert data["payment"]["payment_status"] == "paid"
        assert data["payment"]["amount"] == 360.0

    def test_get_order_before_payment(self, seeded_db):
        """Order before payment has payment=None and payment_status='pending'."""
        create_res = handle_request({
            "request_id": "c1",
            "action": "create_order",
            "payload": {
                "customer_id": 1,
                "restaurant_id": 1,
                "items": [{"item_id": 1, "quantity": 1}],
            },
        })
        order_id = create_res["data"]["order_id"]

        res = handle_request({
            "request_id": "g1",
            "action": "get_order",
            "payload": {"order_id": order_id},
        })
        assert res["ok"] is True
        assert res["data"]["payment"] is None
        assert res["data"]["payment_status"] == "pending"
        assert res["data"]["order_status"] == "placed"

    def test_get_nonexistent_order(self, seeded_db):
        """Non-existent order returns NOT_FOUND."""
        res = handle_request({
            "request_id": "g-none",
            "action": "get_order",
            "payload": {"order_id": 9999},
        })
        assert res["ok"] is False
        assert res["error"]["code"] == ERR_NOT_FOUND


# -------------------------------------------------------------------
# Tests for Action: get_order_history
# -------------------------------------------------------------------
class TestGetOrderHistory:

    def test_order_history_predictable_order(self, seeded_db):
        """Customer order history is returned with predictable newest-first ordering."""
        # Create 3 orders
        for idx in range(3):
            handle_request({
                "request_id": f"ord-{idx}",
                "action": "create_order",
                "payload": {
                    "customer_id": 1,
                    "restaurant_id": 1,
                    "items": [{"item_id": 1, "quantity": idx + 1}],
                },
            })

        res = handle_request({
            "request_id": "hist-01",
            "action": "get_order_history",
            "payload": {"customer_id": 1},
        })
        assert res["ok"] is True
        orders = res["data"]
        assert len(orders) == 3

        # Verify ordering: newest order_id first
        assert orders[0]["order_id"] > orders[1]["order_id"] > orders[2]["order_id"]
        assert orders[0]["items"][0]["quantity"] == 3
        assert orders[1]["items"][0]["quantity"] == 2
        assert orders[2]["items"][0]["quantity"] == 1

    def test_customer_with_no_orders_returns_empty_list(self, seeded_db):
        """Customer 2 (or admin) with no orders returns empty list."""
        # User 4 (Dave Admin) has no orders
        res = handle_request({
            "request_id": "hist-empty",
            "action": "get_order_history",
            "payload": {"customer_id": 4},
        })
        assert res["ok"] is True
        assert res["data"] == []

    def test_nonexistent_customer_returns_not_found(self, seeded_db):
        """Requesting order history for non-existent customer returns NOT_FOUND."""
        res = handle_request({
            "request_id": "hist-none",
            "action": "get_order_history",
            "payload": {"customer_id": 9999},
        })
        assert res["ok"] is False
        assert res["error"]["code"] == ERR_NOT_FOUND
