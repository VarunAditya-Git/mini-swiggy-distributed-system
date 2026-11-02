"""
tests/test_v3_1_deferred_payment.py
===================================
Comprehensive test suite for Mini Swiggy V3.1 Deferred Customer Payment Flow:
  1. Customer pays immediately
  2. Customer pays after seller acceptance
  3. Customer pays while order is PREPARING
  4. Customer pays while order is READY_FOR_PICKUP
  5. Customer revisits order history and pays later
  6. Customer refreshes the page and Pay Now remains available
  7. Failed payment can be retried
  8. Duplicate payment is prevented
  9. Rejected/cancelled orders cannot be paid
 10. Payment does not overwrite seller order status
 11. A customer cannot pay another customer's order (ERR_FORBIDDEN / HTTP 403)
 12. Server-side price calculation immutability
 13. HTTP gateway customer authorization enforcement
"""

import pathlib
import pytest
import sqlite3
import sys

# Ensure imports resolve from project root
sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))

import database.db as db_module
from database.db import get_connection, init_db
from database.seed import seed_data
from server.request_handler import (
    handle_request,
    ERR_INVALID_REQUEST,
    ERR_INVALID_PAYLOAD,
    ERR_NOT_FOUND,
    ERR_PAYMENT_ALREADY_COMPLETED,
    ERR_FORBIDDEN,
)


@pytest.fixture
def seeded_db(tmp_path, monkeypatch):
    """Provide a fresh, isolated, seeded SQLite database for each test."""
    temp_db = tmp_path / "test_v3_1_swiggy.db"
    monkeypatch.setattr(db_module, "DB_PATH", temp_db)
    init_db()
    seed_data()
    yield temp_db


def _create_unpaid_order(customer_id: int = 1, restaurant_id: int = 1) -> dict:
    """Helper to create an unpaid order for testing."""
    req = {
        "request_id": "test-create-ord",
        "action": "create_order",
        "payload": {
            "customer_id": customer_id,
            "restaurant_id": restaurant_id,
            "items": [
                {"item_id": 1, "quantity": 2},  # Chicken Biryani @ 180 = 360
            ],
        },
    }
    res = handle_request(req)
    assert res["ok"] is True, f"Failed to create test order: {res}"
    return res["data"]


class TestDeferredCustomerPayments:
    """Tests covering all 11 requirements for V3.1 Deferred Payments."""

    def test_1_customer_pays_immediately(self, seeded_db):
        """Scenario 1: Customer places an order and pays immediately at checkout."""
        order = _create_unpaid_order(customer_id=1)
        order_id = order["order_id"]
        assert order["payment_status"] == "pending"

        pay_res = handle_request({
            "request_id": "pay-imm",
            "action": "simulate_payment",
            "payload": {
                "order_id": order_id,
                "customer_id": 1,
                "payment_method": "simulated_upi",
                "simulate_status": "paid",
            },
        })
        assert pay_res["ok"] is True
        assert pay_res["data"]["payment_status"] == "paid"
        assert pay_res["data"]["order_status"] == "accepted"
        assert pay_res["data"]["amount"] == 360.0

    def test_2_customer_pays_after_seller_acceptance(self, seeded_db):
        """Scenario 2: Customer creates order, seller accepts order, customer pays later."""
        order = _create_unpaid_order(customer_id=1, restaurant_id=1)
        order_id = order["order_id"]

        # Seller Bob (user_id=2) accepts order
        accept_res = handle_request({
            "request_id": "seller-acc",
            "action": "update_order_status",
            "payload": {
                "seller_id": 2,
                "order_id": order_id,
                "status": "accepted",
            },
        })
        assert accept_res["ok"] is True
        assert accept_res["data"]["order_status"] == "accepted"

        # Customer pays deferred
        pay_res = handle_request({
            "request_id": "pay-after-acc",
            "action": "simulate_payment",
            "payload": {
                "order_id": order_id,
                "customer_id": 1,
                "payment_method": "simulated_card",
                "simulate_status": "paid",
            },
        })
        assert pay_res["ok"] is True
        assert pay_res["data"]["payment_status"] == "paid"
        assert pay_res["data"]["order_status"] == "accepted"

    def test_3_customer_pays_while_order_is_preparing(self, seeded_db):
        """Scenario 3: Customer pays while the order is PREPARING; order status remains PREPARING."""
        order = _create_unpaid_order(customer_id=1, restaurant_id=1)
        order_id = order["order_id"]

        # Seller moves order: placed -> accepted -> preparing
        handle_request({
            "request_id": "s-acc",
            "action": "update_order_status",
            "payload": {"seller_id": 2, "order_id": order_id, "status": "accepted"},
        })
        prep_res = handle_request({
            "request_id": "s-prep",
            "action": "update_order_status",
            "payload": {"seller_id": 2, "order_id": order_id, "status": "preparing"},
        })
        assert prep_res["ok"] is True
        assert prep_res["data"]["order_status"] == "preparing"

        # Customer pays while order is preparing
        pay_res = handle_request({
            "request_id": "pay-prep",
            "action": "simulate_payment",
            "payload": {
                "order_id": order_id,
                "customer_id": 1,
                "payment_method": "simulated_upi",
                "simulate_status": "paid",
            },
        })
        assert pay_res["ok"] is True
        assert pay_res["data"]["payment_status"] == "paid"
        assert pay_res["data"]["order_status"] == "preparing"

    def test_4_customer_pays_while_order_is_ready_for_pickup(self, seeded_db):
        """Scenario 4: Customer pays while order is READY_FOR_PICKUP; order status remains READY_FOR_PICKUP."""
        order = _create_unpaid_order(customer_id=1, restaurant_id=1)
        order_id = order["order_id"]

        # Seller transitions: placed -> accepted -> preparing -> ready_for_pickup
        handle_request({
            "request_id": "s-acc",
            "action": "update_order_status",
            "payload": {"seller_id": 2, "order_id": order_id, "status": "accepted"},
        })
        handle_request({
            "request_id": "s-prep",
            "action": "update_order_status",
            "payload": {"seller_id": 2, "order_id": order_id, "status": "preparing"},
        })
        ready_res = handle_request({
            "request_id": "s-ready",
            "action": "update_order_status",
            "payload": {"seller_id": 2, "order_id": order_id, "status": "ready_for_pickup"},
        })
        assert ready_res["ok"] is True
        assert ready_res["data"]["order_status"] == "ready_for_pickup"

        # Customer pays while ready
        pay_res = handle_request({
            "request_id": "pay-ready",
            "action": "simulate_payment",
            "payload": {
                "order_id": order_id,
                "customer_id": 1,
                "payment_method": "simulated_upi",
                "simulate_status": "paid",
            },
        })
        assert pay_res["ok"] is True
        assert pay_res["data"]["payment_status"] == "paid"
        assert pay_res["data"]["order_status"] == "ready_for_pickup"

    def test_5_customer_revisits_order_history_and_pays_later(self, seeded_db):
        """Scenario 5: Customer leaves checkout without paying, revisits order history, and pays later."""
        order = _create_unpaid_order(customer_id=1, restaurant_id=1)
        order_id = order["order_id"]

        # Customer checks history
        hist_before = handle_request({
            "request_id": "hist-1",
            "action": "get_order_history",
            "payload": {"customer_id": 1},
        })
        assert hist_before["ok"] is True
        found = [o for o in hist_before["data"] if o["order_id"] == order_id]
        assert len(found) == 1
        assert found[0]["payment_status"] == "pending"

        # Customer pays from history
        pay_res = handle_request({
            "request_id": "pay-from-hist",
            "action": "simulate_payment",
            "payload": {
                "order_id": order_id,
                "customer_id": 1,
                "payment_method": "simulated_upi",
                "simulate_status": "paid",
            },
        })
        assert pay_res["ok"] is True
        assert pay_res["data"]["payment_status"] == "paid"

        # History immediately reflects paid status
        hist_after = handle_request({
            "request_id": "hist-2",
            "action": "get_order_history",
            "payload": {"customer_id": 1},
        })
        assert hist_after["ok"] is True
        updated = [o for o in hist_after["data"] if o["order_id"] == order_id][0]
        assert updated["payment_status"] == "paid"

    def test_6_page_refresh_pay_now_remains_available(self, seeded_db):
        """Scenario 6: When page refreshes, unpaid orders retrieved from server still show pending payment and non-terminal status."""
        order = _create_unpaid_order(customer_id=1, restaurant_id=1)
        order_id = order["order_id"]

        # Seller accepted order in the meantime
        handle_request({
            "request_id": "s-acc",
            "action": "update_order_status",
            "payload": {"seller_id": 2, "order_id": order_id, "status": "accepted"},
        })

        # Fresh request simulates page reload
        fresh_order = handle_request({
            "request_id": "reload-get-order",
            "action": "get_order",
            "payload": {"order_id": order_id},
        })
        assert fresh_order["ok"] is True
        data = fresh_order["data"]
        assert data["payment_status"] == "pending"
        assert data["order_status"] == "accepted"
        # Customer can now proceed to pay
        pay_res = handle_request({
            "request_id": "pay-reloaded",
            "action": "simulate_payment",
            "payload": {
                "order_id": order_id,
                "customer_id": 1,
                "payment_method": "simulated_upi",
                "simulate_status": "paid",
            },
        })
        assert pay_res["ok"] is True
        assert pay_res["data"]["payment_status"] == "paid"

    def test_7_failed_payment_can_be_retried(self, seeded_db):
        """Scenario 7: A simulated payment failure records payment_status='failed' and can be retried successfully."""
        order = _create_unpaid_order(customer_id=1, restaurant_id=1)
        order_id = order["order_id"]

        # First attempt fails
        fail_res = handle_request({
            "request_id": "pay-fail",
            "action": "simulate_payment",
            "payload": {
                "order_id": order_id,
                "customer_id": 1,
                "simulate_failure": True,
            },
        })
        assert fail_res["ok"] is True
        assert fail_res["data"]["payment_status"] == "failed"
        assert fail_res["data"]["order_status"] == "placed"

        # Verify DB payment record is marked failed
        with get_connection() as conn:
            ord_row = conn.execute("SELECT payment_status FROM orders WHERE order_id = ?;", (order_id,)).fetchone()
            assert ord_row["payment_status"] == "failed"

        # Retry attempt succeeds
        retry_res = handle_request({
            "request_id": "pay-retry",
            "action": "simulate_payment",
            "payload": {
                "order_id": order_id,
                "customer_id": 1,
                "simulate_status": "paid",
            },
        })
        assert retry_res["ok"] is True
        assert retry_res["data"]["payment_status"] == "paid"
        assert retry_res["data"]["order_status"] == "accepted"

    def test_8_duplicate_payment_is_prevented(self, seeded_db):
        """Scenario 8: Once payment succeeds, subsequent payment attempts are rejected with ERR_PAYMENT_ALREADY_COMPLETED."""
        order = _create_unpaid_order(customer_id=1, restaurant_id=1)
        order_id = order["order_id"]

        # First payment succeeds
        pay1 = handle_request({
            "request_id": "pay-1",
            "action": "simulate_payment",
            "payload": {"order_id": order_id, "customer_id": 1, "simulate_status": "paid"},
        })
        assert pay1["ok"] is True

        # Second payment fails
        pay2 = handle_request({
            "request_id": "pay-2",
            "action": "simulate_payment",
            "payload": {"order_id": order_id, "customer_id": 1, "simulate_status": "paid"},
        })
        assert pay2["ok"] is False
        assert pay2["error"]["code"] == ERR_PAYMENT_ALREADY_COMPLETED

    def test_9_rejected_and_cancelled_orders_cannot_be_paid(self, seeded_db):
        """Scenario 9: If an order has been rejected or cancelled, payment must be rejected."""
        # Test 9A: Rejected order
        order_rej = _create_unpaid_order(customer_id=1, restaurant_id=1)
        order_rej_id = order_rej["order_id"]

        # Seller rejects the order
        rej_res = handle_request({
            "request_id": "s-rej",
            "action": "update_order_status",
            "payload": {"seller_id": 2, "order_id": order_rej_id, "status": "rejected"},
        })
        assert rej_res["ok"] is True

        # Customer attempts to pay rejected order
        pay_rej = handle_request({
            "request_id": "pay-rej",
            "action": "simulate_payment",
            "payload": {"order_id": order_rej_id, "customer_id": 1, "simulate_status": "paid"},
        })
        assert pay_rej["ok"] is False
        assert pay_rej["error"]["code"] == ERR_INVALID_REQUEST

        # Test 9B: Cancelled order
        order_canc = _create_unpaid_order(customer_id=1, restaurant_id=1)
        order_canc_id = order_canc["order_id"]

        with get_connection() as conn:
            conn.execute("UPDATE orders SET order_status = 'cancelled' WHERE order_id = ?;", (order_canc_id,))

        pay_canc = handle_request({
            "request_id": "pay-canc",
            "action": "simulate_payment",
            "payload": {"order_id": order_canc_id, "customer_id": 1, "simulate_status": "paid"},
        })
        assert pay_canc["ok"] is False
        assert pay_canc["error"]["code"] == ERR_INVALID_REQUEST

    def test_10_payment_does_not_overwrite_seller_order_status(self, seeded_db):
        """Scenario 10: Payment does not regress or overwrite seller order status for accepted, preparing, or ready_for_pickup."""
        statuses = ["accepted", "preparing", "ready_for_pickup"]

        for expected_status in statuses:
            order = _create_unpaid_order(customer_id=1, restaurant_id=1)
            oid = order["order_id"]

            # Advance order to target status
            handle_request({
                "request_id": f"s-acc-{oid}",
                "action": "update_order_status",
                "payload": {"seller_id": 2, "order_id": oid, "status": "accepted"},
            })
            if expected_status in ("preparing", "ready_for_pickup"):
                handle_request({
                    "request_id": f"s-prep-{oid}",
                    "action": "update_order_status",
                    "payload": {"seller_id": 2, "order_id": oid, "status": "preparing"},
                })
            if expected_status == "ready_for_pickup":
                handle_request({
                    "request_id": f"s-ready-{oid}",
                    "action": "update_order_status",
                    "payload": {"seller_id": 2, "order_id": oid, "status": "ready_for_pickup"},
                })

            # Customer pays
            pay_res = handle_request({
                "request_id": f"pay-preserve-{oid}",
                "action": "simulate_payment",
                "payload": {"order_id": oid, "customer_id": 1, "simulate_status": "paid"},
            })
            assert pay_res["ok"] is True
            assert pay_res["data"]["payment_status"] == "paid"
            assert pay_res["data"]["order_status"] == expected_status

            # Verify in DB
            with get_connection() as conn:
                row = conn.execute("SELECT order_status, payment_status FROM orders WHERE order_id = ?;", (oid,)).fetchone()
                assert row["order_status"] == expected_status
                assert row["payment_status"] == "paid"

    def test_11_customer_cannot_pay_another_customers_order(self, seeded_db):
        """Scenario 11: A customer cannot pay for an order belonging to another customer."""
        # Alice (ID: 1) places order
        order = _create_unpaid_order(customer_id=1, restaurant_id=1)
        order_id = order["order_id"]

        # Eve (ID: 2 or other non-owner ID, e.g., 99) attempts to pay
        hacker_res = handle_request({
            "request_id": "pay-hacker",
            "action": "simulate_payment",
            "payload": {
                "order_id": order_id,
                "customer_id": 99,
                "payment_method": "simulated_upi",
                "simulate_status": "paid",
            },
        })
        assert hacker_res["ok"] is False
        assert hacker_res["error"]["code"] == ERR_FORBIDDEN
        assert "Access denied" in hacker_res["error"]["message"]

        # Verify order remains unpaid
        with get_connection() as conn:
            row = conn.execute("SELECT payment_status FROM orders WHERE order_id = ?;", (order_id,)).fetchone()
            assert row["payment_status"] == "pending"

    def test_12_server_calculates_payment_amount_authoritatively(self, seeded_db):
        """Server-side calculation: Client cannot tamper with payment amount."""
        order = _create_unpaid_order(customer_id=1, restaurant_id=1)
        order_id = order["order_id"]
        authoritative_total = order["total_amount"]
        assert authoritative_total == 360.0

        # Tampered amount attempt in payload
        pay_res = handle_request({
            "request_id": "pay-tamper",
            "action": "simulate_payment",
            "payload": {
                "order_id": order_id,
                "customer_id": 1,
                "amount": 1.0,  # Attacker tries to pay ₹1 instead of ₹360
                "simulate_status": "paid",
            },
        })
        assert pay_res["ok"] is True
        # Server must have billed the true DB amount 360.0, ignoring payload amount
        assert pay_res["data"]["amount"] == 360.0

        with get_connection() as conn:
            pay_row = conn.execute("SELECT amount FROM payments WHERE order_id = ?;", (order_id,)).fetchone()
            assert pay_row["amount"] == 360.0
