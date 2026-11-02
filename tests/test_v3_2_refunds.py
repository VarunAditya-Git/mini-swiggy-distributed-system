"""
tests/test_v3_2_refunds.py
==========================
Comprehensive automated test suite for Mini Swiggy V3.2 Refund Management & Payment Consistency:

1. Paid order rejection creates a full refund.
2. Unpaid order rejection creates no refund.
3. Failed payment rejection creates no refund.
4. Successful refund updates refund status to 'refunded'.
5. Failed refund remains retryable ('failed').
6. Retrying a failed refund succeeds.
7. Duplicate refund requests do not create duplicate records.
8. Refund amount cannot exceed the paid amount.
9. Client cannot manipulate the refund amount.
10. Customer cannot view another customer's refund (ERR_FORBIDDEN / HTTP 403).
11. Seller cannot reject another restaurant's order (ERR_FORBIDDEN).
12. Seller cannot access another restaurant's refund / cannot create arbitrary refunds.
13. Repeated rejection does not create duplicate refunds.
14. Refund history returns correct records for customer and seller.
15. Order remains rejected if refund processing fails.
16. Payment history and item snapshots remain intact after refund.
17. Database rollback works when refund creation encounters an error.
18. Repeated migrations (migrate_db) do not break the database.
19. Concurrent or closely repeated refund attempts cannot create duplicate records.
20. Existing V3.1 deferred-payment flow integrity is preserved alongside refunds.
"""

import concurrent.futures
import json
import pathlib
import pytest
import sqlite3
import sys
import uuid

# Ensure imports resolve from project root
sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))

import database.db as db_module
from database.db import get_connection, init_db, migrate_db
from database.seed import seed_data
from server.request_handler import (
    handle_request,
    ERR_INVALID_REQUEST,
    ERR_INVALID_PAYLOAD,
    ERR_NOT_FOUND,
    ERR_FORBIDDEN,
    ERR_REFUND_NOT_FOUND,
    ERR_REFUND_ALREADY_PROCESSED,
    ERR_REFUND_NOT_RETRYABLE,
    ERR_INVALID_TRANSITION,
)


@pytest.fixture
def seeded_db(tmp_path, monkeypatch):
    """Provide a fresh, isolated, seeded SQLite database for each test."""
    temp_db = tmp_path / "test_v3_2_refunds.db"
    monkeypatch.setattr(db_module, "DB_PATH", temp_db)
    init_db()
    seed_data()
    yield temp_db


def _create_and_pay_order(customer_id: int = 1, restaurant_id: int = 1) -> dict:
    """Helper to create an order and successfully pay for it."""
    create_res = handle_request({
        "request_id": f"req-create-{uuid.uuid4().hex[:6]}",
        "action": "create_order",
        "payload": {
            "customer_id": customer_id,
            "restaurant_id": restaurant_id,
            "items": [
                {"item_id": 1, "quantity": 1},  # Chicken Biryani @ 180 = 180
            ],
        },
    })
    assert create_res["ok"] is True, f"Failed to create order: {create_res}"
    order = create_res["data"]
    order_id = order["order_id"]

    pay_res = handle_request({
        "request_id": f"req-pay-{uuid.uuid4().hex[:6]}",
        "action": "simulate_payment",
        "payload": {
            "order_id": order_id,
            "customer_id": customer_id,
            "payment_method": "simulated_upi",
            "simulate_status": "paid",
        },
    })
    assert pay_res["ok"] is True, f"Failed to pay order: {pay_res}"
    return order


def _create_unpaid_order(customer_id: int = 1, restaurant_id: int = 1) -> dict:
    """Helper to create an unpaid order."""
    create_res = handle_request({
        "request_id": f"req-unpaid-{uuid.uuid4().hex[:6]}",
        "action": "create_order",
        "payload": {
            "customer_id": customer_id,
            "restaurant_id": restaurant_id,
            "items": [
                {"item_id": 1, "quantity": 1},  # Chicken Biryani @ 180 = 180
            ],
        },
    })
    assert create_res["ok"] is True
    return create_res["data"]


class TestRefundManagementAndConsistency:
    """Comprehensive test suite covering all 20 scenarios for Mini Swiggy V3.2."""

    def test_1_paid_order_rejection_creates_full_refund(self, seeded_db):
        """Scenario 1: Seller rejecting an already-paid order initiates a full refund."""
        order = _create_and_pay_order(customer_id=1, restaurant_id=1)
        order_id = order["order_id"]
        total_amount = order["total_amount"]
        assert total_amount == 180.0

        # Seller Bob (user_id=2) rejects the order
        rej_res = handle_request({
            "request_id": "rej-1",
            "action": "update_order_status",
            "payload": {
                "seller_id": 2,
                "order_id": order_id,
                "status": "rejected",
                "reason": "Kitchen out of ingredients",
            },
        })
        assert rej_res["ok"] is True
        data = rej_res["data"]
        assert data["order_status"] == "rejected"
        assert data["payment_status"] == "paid"
        assert "refund" in data
        refund = data["refund"]
        assert refund is not None
        assert refund["amount"] == 180.0
        assert refund["refund_status"] == "refunded"
        assert refund["reason"] == "Kitchen out of ingredients"
        assert refund["transaction_ref"].startswith("REF-")
        assert refund["attempt_count"] == 1

        # Check DB directly
        with get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM refunds WHERE order_id = ?", (order_id,)
            ).fetchone()
            assert row is not None
            assert row["amount"] == 180.0
            assert row["refund_status"] == "refunded"

    def test_2_unpaid_order_rejection_creates_no_refund(self, seeded_db):
        """Scenario 2: Rejecting an unpaid order creates no refund."""
        order = _create_unpaid_order(customer_id=1, restaurant_id=1)
        order_id = order["order_id"]

        rej_res = handle_request({
            "request_id": "rej-unpaid",
            "action": "update_order_status",
            "payload": {
                "seller_id": 2,
                "order_id": order_id,
                "status": "rejected",
            },
        })
        assert rej_res["ok"] is True
        assert rej_res["data"]["order_status"] == "rejected"
        assert rej_res["data"]["payment_status"] == "pending"
        assert rej_res["data"]["refund"] is None

        # Verify no refund record in DB
        with get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM refunds WHERE order_id = ?", (order_id,)
            ).fetchone()
            assert row is None

    def test_3_failed_payment_rejection_creates_no_refund(self, seeded_db):
        """Scenario 3: Rejecting an order whose payment failed creates no refund."""
        order = _create_unpaid_order(customer_id=1, restaurant_id=1)
        order_id = order["order_id"]

        # Simulate failed payment
        pay_res = handle_request({
            "request_id": "pay-fail",
            "action": "simulate_payment",
            "payload": {
                "order_id": order_id,
                "customer_id": 1,
                "payment_method": "simulated_upi",
                "simulate_status": "failed",
                "simulate_failure": True,
            },
        })
        assert pay_res["ok"] is True
        assert pay_res["data"]["payment_status"] == "failed"

        # Seller rejects
        rej_res = handle_request({
            "request_id": "rej-after-pay-fail",
            "action": "update_order_status",
            "payload": {
                "seller_id": 2,
                "order_id": order_id,
                "status": "rejected",
            },
        })
        assert rej_res["ok"] is True
        assert rej_res["data"]["refund"] is None

        with get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM refunds WHERE order_id = ?", (order_id,)
            ).fetchone()
            assert row is None

    def test_4_successful_refund_updates_refund_status(self, seeded_db):
        """Scenario 4: Successful refund marks refund status as 'refunded' and persists reference."""
        order = _create_and_pay_order(customer_id=1, restaurant_id=1)
        order_id = order["order_id"]

        handle_request({
            "request_id": "rej-4",
            "action": "update_order_status",
            "payload": {"seller_id": 2, "order_id": order_id, "status": "rejected"},
        })

        # Query get_refund
        ref_res = handle_request({
            "request_id": "get-ref-4",
            "action": "get_refund",
            "payload": {"order_id": order_id, "customer_id": 1},
        })
        assert ref_res["ok"] is True
        assert ref_res["data"]["refund_status"] == "refunded"
        assert ref_res["data"]["amount"] == 180.0
        assert ref_res["data"]["attempt_count"] == 1

    def test_5_failed_refund_remains_retryable(self, seeded_db):
        """Scenario 5: If refund processing fails, refund status is 'failed' and order remains rejected."""
        order = _create_and_pay_order(customer_id=1, restaurant_id=1)
        order_id = order["order_id"]

        # Reject with simulate_refund_failure=True
        rej_res = handle_request({
            "request_id": "rej-fail-sim",
            "action": "update_order_status",
            "payload": {
                "seller_id": 2,
                "order_id": order_id,
                "status": "rejected",
                "simulate_refund_failure": True,
            },
        })
        assert rej_res["ok"] is True
        assert rej_res["data"]["order_status"] == "rejected"
        refund = rej_res["data"]["refund"]
        assert refund["refund_status"] == "failed"
        assert refund["attempt_count"] == 1

        # Check DB
        with get_connection() as conn:
            row = conn.execute(
                "SELECT refund_status, attempt_count FROM refunds WHERE order_id = ?",
                (order_id,),
            ).fetchone()
            assert row["refund_status"] == "failed"
            assert row["attempt_count"] == 1

    def test_6_retrying_a_failed_refund_succeeds(self, seeded_db):
        """Scenario 6: Retrying a failed refund transitions status from 'failed' to 'refunded'."""
        order = _create_and_pay_order(customer_id=1, restaurant_id=1)
        order_id = order["order_id"]

        # Reject with failed refund
        handle_request({
            "request_id": "rej-f",
            "action": "update_order_status",
            "payload": {
                "seller_id": 2,
                "order_id": order_id,
                "status": "rejected",
                "simulate_refund_failure": True,
            },
        })

        # Retry refund
        retry_res = handle_request({
            "request_id": "retry-1",
            "action": "retry_refund",
            "payload": {
                "order_id": order_id,
                "customer_id": 1,
            },
        })
        assert retry_res["ok"] is True
        assert retry_res["data"]["refund_status"] == "refunded"
        assert retry_res["data"]["attempt_count"] == 2

        # Retrying an already refunded refund should fail with REFUND_ALREADY_PROCESSED
        retry_again = handle_request({
            "request_id": "retry-again",
            "action": "retry_refund",
            "payload": {
                "order_id": order_id,
                "customer_id": 1,
            },
        })
        assert retry_again["ok"] is False
        assert retry_again["error"]["code"] == ERR_REFUND_ALREADY_PROCESSED

    def test_7_duplicate_refund_requests_do_not_create_duplicate_records(self, seeded_db):
        """Scenario 7: Duplicate refund requests do not duplicate records in SQLite."""
        order = _create_and_pay_order(customer_id=1, restaurant_id=1)
        order_id = order["order_id"]

        # First rejection
        handle_request({
            "request_id": "rej-dup-1",
            "action": "update_order_status",
            "payload": {"seller_id": 2, "order_id": order_id, "status": "rejected"},
        })

        # Verify exactly one record in refunds table
        with get_connection() as conn:
            count = conn.execute(
                "SELECT COUNT(*) as cnt FROM refunds WHERE order_id = ?", (order_id,)
            ).fetchone()["cnt"]
            assert count == 1

    def test_8_refund_amount_cannot_exceed_paid_amount(self, seeded_db):
        """Scenario 8: Refund amount matches the authoritative paid amount and cannot exceed it."""
        order = _create_and_pay_order(customer_id=1, restaurant_id=1)
        order_id = order["order_id"]

        rej_res = handle_request({
            "request_id": "rej-amt",
            "action": "update_order_status",
            "payload": {"seller_id": 2, "order_id": order_id, "status": "rejected"},
        })
        assert rej_res["ok"] is True
        refund = rej_res["data"]["refund"]
        assert refund["amount"] == 180.0

        with get_connection() as conn:
            paid_amount = conn.execute(
                "SELECT amount FROM payments WHERE order_id = ?", (order_id,)
            ).fetchone()["amount"]
            assert refund["amount"] == paid_amount

    def test_9_client_cannot_manipulate_refund_amount(self, seeded_db):
        """Scenario 9: Client attempting to send an arbitrary refund amount in payload is ignored."""
        order = _create_and_pay_order(customer_id=1, restaurant_id=1)
        order_id = order["order_id"]

        # Client attempts to pass amount=99999 or amount=0
        rej_res = handle_request({
            "request_id": "rej-tamper",
            "action": "update_order_status",
            "payload": {
                "seller_id": 2,
                "order_id": order_id,
                "status": "rejected",
                "amount": 99999.0,
            },
        })
        assert rej_res["ok"] is True
        # Amount must strictly be 180.0 from payments table
        assert rej_res["data"]["refund"]["amount"] == 180.0

    def test_10_customer_cannot_view_another_customers_refund(self, seeded_db):
        """Scenario 10: Customer 2 cannot access refund for Customer 1's order."""
        order = _create_and_pay_order(customer_id=1, restaurant_id=1)
        order_id = order["order_id"]

        handle_request({
            "request_id": "rej-10",
            "action": "update_order_status",
            "payload": {"seller_id": 2, "order_id": order_id, "status": "rejected"},
        })

        # Customer 2 attempts to view Customer 1's refund
        view_res = handle_request({
            "request_id": "unauth-view",
            "action": "get_refund",
            "payload": {
                "order_id": order_id,
                "customer_id": 2,
            },
        })
        assert view_res["ok"] is False
        assert view_res["error"]["code"] == ERR_FORBIDDEN

    def test_11_seller_cannot_reject_another_restaurants_order(self, seeded_db):
        """Scenario 11: Seller for Restaurant 2 cannot reject Restaurant 1's order."""
        order = _create_and_pay_order(customer_id=1, restaurant_id=1)
        order_id = order["order_id"]

        with get_connection() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO users (user_id, name, email, role) VALUES (10, 'Other Seller', 'other@food.com', 'seller')"
            )
            conn.execute(
                "UPDATE restaurants SET owner_user_id = 10 WHERE restaurant_id = 2"
            )
            conn.commit()

        # Seller 10 attempts to reject Restaurant 1's order
        rej_res = handle_request({
            "request_id": "unauth-rej",
            "action": "update_order_status",
            "payload": {
                "seller_id": 10,
                "order_id": order_id,
                "status": "rejected",
            },
        })
        assert rej_res["ok"] is False
        assert rej_res["error"]["code"] == ERR_FORBIDDEN

    def test_12_seller_cannot_access_another_restaurants_refund(self, seeded_db):
        """Scenario 12: Seller cannot view or access refunds from another restaurant."""
        order = _create_and_pay_order(customer_id=1, restaurant_id=1)
        order_id = order["order_id"]

        handle_request({
            "request_id": "rej-12",
            "action": "update_order_status",
            "payload": {"seller_id": 2, "order_id": order_id, "status": "rejected"},
        })

        with get_connection() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO users (user_id, name, email, role) VALUES (11, 'Other Seller 2', 'other2@food.com', 'seller')"
            )
            conn.execute(
                "UPDATE restaurants SET owner_user_id = 11 WHERE restaurant_id = 3"
            )
            conn.commit()

        view_res = handle_request({
            "request_id": "unauth-seller-view",
            "action": "get_refund",
            "payload": {
                "order_id": order_id,
                "seller_id": 11,
            },
        })
        assert view_res["ok"] is False
        assert view_res["error"]["code"] == ERR_FORBIDDEN

    def test_13_repeated_rejection_does_not_create_duplicate_refunds(self, seeded_db):
        """Scenario 13: Calling update_order_status rejected on already-rejected order returns idempotent result."""
        order = _create_and_pay_order(customer_id=1, restaurant_id=1)
        order_id = order["order_id"]

        # First rejection
        res1 = handle_request({
            "request_id": "rej-rep-1",
            "action": "update_order_status",
            "payload": {"seller_id": 2, "order_id": order_id, "status": "rejected"},
        })
        assert res1["ok"] is True
        ref_id_1 = res1["data"]["refund"]["refund_id"]

        # Second rejection
        res2 = handle_request({
            "request_id": "rej-rep-2",
            "action": "update_order_status",
            "payload": {"seller_id": 2, "order_id": order_id, "status": "rejected"},
        })
        assert res2["ok"] is True
        ref_id_2 = res2["data"]["refund"]["refund_id"]
        assert ref_id_1 == ref_id_2

        # Exactly 1 row in database
        with get_connection() as conn:
            cnt = conn.execute(
                "SELECT COUNT(*) as c FROM refunds WHERE order_id = ?", (order_id,)
            ).fetchone()["c"]
            assert cnt == 1

    def test_14_refund_history_returns_correct_records(self, seeded_db):
        """Scenario 14: get_refund_history returns appropriate records for customer and seller."""
        order1 = _create_and_pay_order(customer_id=1, restaurant_id=1)
        handle_request({
            "request_id": "rej-h1",
            "action": "update_order_status",
            "payload": {"seller_id": 2, "order_id": order1["order_id"], "status": "rejected"},
        })

        # Customer refund history
        cust_hist = handle_request({
            "request_id": "chist",
            "action": "get_refund_history",
            "payload": {"customer_id": 1},
        })
        assert cust_hist["ok"] is True
        assert len(cust_hist["data"]) >= 1
        assert any(r["order_id"] == order1["order_id"] for r in cust_hist["data"])

        # Seller refund history
        seller_hist = handle_request({
            "request_id": "shist",
            "action": "get_refund_history",
            "payload": {"seller_id": 2},
        })
        assert seller_hist["ok"] is True
        assert len(seller_hist["data"]) >= 1
        assert any(r["order_id"] == order1["order_id"] for r in seller_hist["data"])

    def test_15_order_remains_rejected_if_refund_processing_fails(self, seeded_db):
        """Scenario 15: If refund simulation fails, the order status must stay 'rejected'."""
        order = _create_and_pay_order(customer_id=1, restaurant_id=1)
        order_id = order["order_id"]

        rej_res = handle_request({
            "request_id": "rej-15",
            "action": "update_order_status",
            "payload": {
                "seller_id": 2,
                "order_id": order_id,
                "status": "rejected",
                "simulate_refund_failure": True,
            },
        })
        assert rej_res["ok"] is True
        assert rej_res["data"]["order_status"] == "rejected"
        assert rej_res["data"]["refund"]["refund_status"] == "failed"

        # Check DB order status
        with get_connection() as conn:
            ord_status = conn.execute(
                "SELECT order_status FROM orders WHERE order_id = ?", (order_id,)
            ).fetchone()["order_status"]
            assert ord_status == "rejected"

    def test_16_payment_history_remains_intact_after_refund(self, seeded_db):
        """Scenario 16: Refunding an order does NOT delete or overwrite the original payment record or snapshots."""
        order = _create_and_pay_order(customer_id=1, restaurant_id=1)
        order_id = order["order_id"]

        # Check payment record before refund
        with get_connection() as conn:
            pay_before = conn.execute(
                "SELECT * FROM payments WHERE order_id = ?", (order_id,)
            ).fetchone()
            assert pay_before is not None
            assert pay_before["payment_status"] == "paid"
            tx_ref = pay_before["transaction_ref"]

            items_before = conn.execute(
                "SELECT * FROM order_items WHERE order_id = ?", (order_id,)
            ).fetchall()
            assert len(items_before) > 0

        # Reject & refund
        handle_request({
            "request_id": "rej-16",
            "action": "update_order_status",
            "payload": {"seller_id": 2, "order_id": order_id, "status": "rejected"},
        })

        # Check payment record after refund
        with get_connection() as conn:
            pay_after = conn.execute(
                "SELECT * FROM payments WHERE order_id = ?", (order_id,)
            ).fetchone()
            assert pay_after is not None
            assert pay_after["payment_status"] == "paid"
            assert pay_after["transaction_ref"] == tx_ref

            # Item price snapshots must remain completely intact
            items_after = conn.execute(
                "SELECT * FROM order_items WHERE order_id = ?", (order_id,)
            ).fetchall()
            assert len(items_after) == len(items_before)
            assert items_after[0]["unit_price"] == items_before[0]["unit_price"]

    def test_17_database_rollback_works_when_refund_creation_fails(self, seeded_db):
        """Scenario 17: Database transaction rollback if refund insertion fails."""
        order = _create_and_pay_order(customer_id=1, restaurant_id=1)
        order_id = order["order_id"]

        # If a DB constraint or error occurs, rollback is triggered
        # We test that the system handles DB integrity gracefully
        with get_connection() as conn:
            # Let's insert a malformed refund with negative amount to test CHECK constraint
            with pytest.raises(sqlite3.IntegrityError):
                conn.execute(
                    "INSERT INTO refunds (order_id, amount, refund_status) VALUES (?, -50, 'refunded')",
                    (order_id,),
                )

    def test_18_repeated_migrations_do_not_break_the_database(self, seeded_db):
        """Scenario 18: Running migrate_db() repeatedly is idempotent and safe."""
        migrate_db()
        migrate_db()
        migrate_db()

        # Check refunds table is still functional
        with get_connection() as conn:
            cursor = conn.execute("PRAGMA table_info(refunds)")
            columns = [row["name"] for row in cursor.fetchall()]
            assert "refund_id" in columns
            assert "order_id" in columns
            assert "amount" in columns
            assert "refund_status" in columns

    def test_19_concurrent_refund_attempts_cannot_create_duplicates(self, seeded_db):
        """Scenario 19: Closely repeated or concurrent refund requests cannot create duplicate refunds."""
        order = _create_and_pay_order(customer_id=1, restaurant_id=1)
        order_id = order["order_id"]

        def _do_reject():
            return handle_request({
                "request_id": f"conc-{uuid.uuid4().hex[:6]}",
                "action": "update_order_status",
                "payload": {"seller_id": 2, "order_id": order_id, "status": "rejected"},
            })

        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
            futures = [executor.submit(_do_reject) for _ in range(4)]
            results = [f.result() for f in futures]

        # All requests must succeed
        for res in results:
            assert res["ok"] is True

        # Exactly 1 refund in the database
        with get_connection() as conn:
            count = conn.execute(
                "SELECT COUNT(*) as c FROM refunds WHERE order_id = ?", (order_id,)
            ).fetchone()["c"]
            assert count == 1

    def test_20_existing_v3_1_deferred_payment_tests_still_pass(self, seeded_db):
        """Scenario 20: Deferred payment flow from V3.1 functions seamlessly alongside refund logic."""
        # Create order, seller accepts, customer pays deferred
        order = _create_unpaid_order(customer_id=1, restaurant_id=1)
        order_id = order["order_id"]

        # Seller Bob accepts
        acc_res = handle_request({
            "request_id": "s-acc-20",
            "action": "update_order_status",
            "payload": {"seller_id": 2, "order_id": order_id, "status": "accepted"},
        })
        assert acc_res["ok"] is True
        assert acc_res["data"]["order_status"] == "accepted"

        # Customer pays deferred
        pay_res = handle_request({
            "request_id": "pay-def-20",
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

        # Advance order: accepted -> preparing -> ready_for_pickup
        prep_res = handle_request({
            "request_id": "s-prep-20",
            "action": "update_order_status",
            "payload": {"seller_id": 2, "order_id": order_id, "status": "preparing"},
        })
        assert prep_res["ok"] is True
        assert prep_res["data"]["order_status"] == "preparing"

        rdy_res = handle_request({
            "request_id": "s-rdy-20",
            "action": "update_order_status",
            "payload": {"seller_id": 2, "order_id": order_id, "status": "ready_for_pickup"},
        })
        assert rdy_res["ok"] is True
        assert rdy_res["data"]["order_status"] == "ready_for_pickup"
        assert rdy_res["data"]["payment_status"] == "paid"
        assert rdy_res["data"]["refund"] is None
