"""
tests/test_v3_seller_handler.py
===============================
Comprehensive test suite for Mini Swiggy V3 Seller Dashboard backend logic:
  1. Seller Authorization & Multi-Tenant Isolation
  2. Order Lifecycle Management & State Transitions
  3. Menu Management & Real-Time Availability
  4. Snapshot Price Immutability
  5. Unavailable Item Order Prevention
"""

import pathlib
import pytest
import sqlite3
import sys

# Ensure imports resolve from project root
sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))

from database.db import get_connection, init_db
from database.seed import seed_data
from server.request_handler import handle_request


@pytest.fixture
def db(tmp_path, monkeypatch):
    """Provide a fresh, seeded temporary SQLite database for each test."""
    import database.db as db_module

    temp_db = tmp_path / "test_v3_swiggy.db"
    monkeypatch.setattr(db_module, "DB_PATH", temp_db)
    init_db()
    seed_data()

    # Seed an extra restaurant owned by a second seller to verify multi-tenant isolation
    with get_connection() as conn:
        # Create Seller 2 (Eve)
        conn.execute(
            "INSERT INTO users (name, email, role, is_active) VALUES (?, ?, ?, ?);",
            ("Eve Seller", "eve@example.com", "seller", 1),
        )
        eve_id = conn.execute("SELECT user_id FROM users WHERE email = 'eve@example.com';").fetchone()["user_id"]

        # Assign Dosa Corner (restaurant_id 2) to Eve
        conn.execute(
            "UPDATE restaurants SET owner_user_id = ? WHERE restaurant_id = 2;",
            (eve_id,),
        )

        # Create an inactive seller
        conn.execute(
            "INSERT INTO users (name, email, role, is_active) VALUES (?, ?, ?, ?);",
            ("Inactive Bob", "inactive@example.com", "seller", 0),
        )

        # Create a seller without any restaurant
        conn.execute(
            "INSERT INTO users (name, email, role, is_active) VALUES (?, ?, ?, ?);",
            ("Homeless Seller", "homeless@example.com", "seller", 1),
        )

    yield temp_db


# =====================================================================
# 1. Seller Authorization Tests
# =====================================================================
class TestSellerAuthorization:

    def test_seller_can_access_own_profile(self, db):
        """Bob Seller (ID: 2) can successfully retrieve their profile and restaurant stats."""
        req = {
            "request_id": "r-auth-1",
            "action": "get_seller_profile",
            "payload": {"seller_id": 2},
        }
        res = handle_request(req)
        assert res["ok"] is True
        data = res["data"]
        assert data["seller"]["user_id"] == 2
        assert data["seller"]["name"] == "Bob Seller"
        assert data["restaurant"]["name"] == "Hyderabad Spice House"
        assert "stats" in data
        assert data["stats"]["menu_items_count"] == 2

    def test_alias_get_seller_restaurant(self, db):
        """Action 'get_seller_restaurant' works as an alias for get_seller_profile."""
        req = {
            "request_id": "r-auth-alias",
            "action": "get_seller_restaurant",
            "payload": {"seller_id": 2},
        }
        res = handle_request(req)
        assert res["ok"] is True
        assert res["data"]["restaurant"]["name"] == "Hyderabad Spice House"

    def test_customer_role_cannot_access_seller_endpoints(self, db):
        """Alice Customer (ID: 1) must be rejected with UNAUTHORIZED_ROLE."""
        req = {
            "request_id": "r-auth-2",
            "action": "get_seller_profile",
            "payload": {"seller_id": 1},
        }
        res = handle_request(req)
        assert res["ok"] is False
        assert res["error"]["code"] == "UNAUTHORIZED_ROLE"

    def test_delivery_role_cannot_access_seller_endpoints(self, db):
        """Carol Delivery (ID: 3) must be rejected with UNAUTHORIZED_ROLE."""
        req = {
            "request_id": "r-auth-3",
            "action": "get_seller_profile",
            "payload": {"seller_id": 3},
        }
        res = handle_request(req)
        assert res["ok"] is False
        assert res["error"]["code"] == "UNAUTHORIZED_ROLE"

    def test_admin_role_cannot_access_seller_endpoints(self, db):
        """Dave Admin (ID: 4) must be rejected with UNAUTHORIZED_ROLE."""
        req = {
            "request_id": "r-auth-4",
            "action": "get_seller_profile",
            "payload": {"seller_id": 4},
        }
        res = handle_request(req)
        assert res["ok"] is False
        assert res["error"]["code"] == "UNAUTHORIZED_ROLE"

    def test_nonexistent_seller_returns_not_found(self, db):
        """Seller ID 9999 does not exist -> NOT_FOUND."""
        req = {
            "request_id": "r-auth-5",
            "action": "get_seller_profile",
            "payload": {"seller_id": 9999},
        }
        res = handle_request(req)
        assert res["ok"] is False
        assert res["error"]["code"] == "NOT_FOUND"

    def test_inactive_seller_rejected(self, db):
        """Inactive seller account must be rejected with FORBIDDEN."""
        with get_connection() as conn:
            uid = conn.execute("SELECT user_id FROM users WHERE email = 'inactive@example.com';").fetchone()["user_id"]
        req = {
            "request_id": "r-auth-6",
            "action": "get_seller_profile",
            "payload": {"seller_id": uid},
        }
        res = handle_request(req)
        assert res["ok"] is False
        assert res["error"]["code"] == "FORBIDDEN"

    def test_seller_without_restaurant_rejected(self, db):
        """Seller with no associated restaurant must be rejected with FORBIDDEN."""
        with get_connection() as conn:
            uid = conn.execute("SELECT user_id FROM users WHERE email = 'homeless@example.com';").fetchone()["user_id"]
        req = {
            "request_id": "r-auth-7",
            "action": "get_seller_profile",
            "payload": {"seller_id": uid},
        }
        res = handle_request(req)
        assert res["ok"] is False
        assert res["error"]["code"] == "FORBIDDEN"

    def test_seller_cannot_view_another_restaurants_order(self, db):
        """Bob Seller (owns Restaurant 1) cannot view Order placed at Restaurant 2."""
        # Create an order at Restaurant 2 (owned by Eve)
        create_res = handle_request({
            "request_id": "c-ord-1",
            "action": "create_order",
            "payload": {
                "customer_id": 1,
                "restaurant_id": 2,
                "items": [{"item_id": 3, "quantity": 1}],
            },
        })
        assert create_res["ok"] is True
        order_id = create_res["data"]["order_id"]

        # Bob (ID: 2) attempts to fetch this order
        req = {
            "request_id": "r-auth-cross-order",
            "action": "get_seller_order",
            "payload": {"seller_id": 2, "order_id": order_id},
        }
        res = handle_request(req)
        assert res["ok"] is False
        assert res["error"]["code"] == "FORBIDDEN"
        assert "does not belong to your restaurant" in res["error"]["message"]

    def test_seller_cannot_update_another_restaurants_order(self, db):
        """Bob Seller cannot accept, reject, or update orders belonging to Restaurant 2."""
        create_res = handle_request({
            "request_id": "c-ord-2",
            "action": "create_order",
            "payload": {
                "customer_id": 1,
                "restaurant_id": 2,
                "items": [{"item_id": 3, "quantity": 1}],
            },
        })
        order_id = create_res["data"]["order_id"]

        # Bob tries to accept it
        req = {
            "request_id": "r-auth-cross-update",
            "action": "update_order_status",
            "payload": {"seller_id": 2, "order_id": order_id, "status": "accepted"},
        }
        res = handle_request(req)
        assert res["ok"] is False
        assert res["error"]["code"] == "FORBIDDEN"

    def test_seller_cannot_edit_another_restaurants_menu_item(self, db):
        """Bob Seller cannot edit items belonging to Restaurant 2 (item_id: 3)."""
        req = {
            "request_id": "r-auth-cross-menu",
            "action": "update_menu_item",
            "payload": {
                "seller_id": 2,
                "item_id": 3,  # Belongs to Dosa Corner (restaurant_id 2)
                "name": "Hacked Dosa",
                "price": 10.0,
            },
        }
        res = handle_request(req)
        assert res["ok"] is False
        assert res["error"]["code"] == "FORBIDDEN"

    def test_seller_cannot_toggle_another_restaurants_menu_availability(self, db):
        """Bob Seller cannot toggle availability for Restaurant 2's items."""
        req = {
            "request_id": "r-auth-cross-avail",
            "action": "update_menu_item_availability",
            "payload": {"seller_id": 2, "item_id": 3, "is_available": 0},
        }
        res = handle_request(req)
        assert res["ok"] is False
        assert res["error"]["code"] == "FORBIDDEN"


# =====================================================================
# 2. Order Management & Lifecycle Transition Tests
# =====================================================================
class TestOrderLifecycleManagement:

    def _create_order_for_bob(self):
        res = handle_request({
            "request_id": "c-ord",
            "action": "create_order",
            "payload": {
                "customer_id": 1,
                "restaurant_id": 1,
                "items": [{"item_id": 1, "quantity": 2}],
            },
        })
        assert res["ok"] is True
        return res["data"]["order_id"]

    def test_seller_can_view_incoming_orders(self, db):
        """Bob Seller can view orders placed at Hyderabad Spice House."""
        order_id = self._create_order_for_bob()

        req = {
            "request_id": "r-view-orders",
            "action": "get_seller_orders",
            "payload": {"seller_id": 2},
        }
        res = handle_request(req)
        assert res["ok"] is True
        orders = res["data"]
        assert len(orders) >= 1
        found = next((o for o in orders if o["order_id"] == order_id), None)
        assert found is not None
        assert found["customer_name"] == "Alice Customer"
        assert found["total_amount"] == 360.0
        assert found["order_status"] == "placed"
        assert len(found["items"]) == 1
        assert found["items"][0]["name"] == "Chicken Biryani"

    def test_seller_accepts_pending_order(self, db):
        """Transition: placed/pending -> accepted."""
        order_id = self._create_order_for_bob()

        res = handle_request({
            "request_id": "r-accept",
            "action": "update_order_status",
            "payload": {"seller_id": 2, "order_id": order_id, "status": "accepted"},
        })
        assert res["ok"] is True
        assert res["data"]["order_status"] == "accepted"

        # Verify DB persisted
        with get_connection() as conn:
            row = conn.execute("SELECT order_status FROM orders WHERE order_id = ?;", (order_id,)).fetchone()
            assert row["order_status"] == "accepted"

    def test_seller_rejects_pending_order(self, db):
        """Transition: placed/pending -> rejected."""
        order_id = self._create_order_for_bob()

        res = handle_request({
            "request_id": "r-reject",
            "action": "update_order_status",
            "payload": {"seller_id": 2, "order_id": order_id, "status": "rejected"},
        })
        assert res["ok"] is True
        assert res["data"]["order_status"] == "rejected"

        # Verify DB persisted
        with get_connection() as conn:
            row = conn.execute("SELECT order_status FROM orders WHERE order_id = ?;", (order_id,)).fetchone()
            assert row["order_status"] == "rejected"

    def test_complete_kitchen_lifecycle(self, db):
        """Complete happy path: placed -> accepted -> preparing -> ready_for_pickup."""
        order_id = self._create_order_for_bob()

        # Step 1: Accept
        r1 = handle_request({
            "request_id": "r-cycle-1",
            "action": "update_order_status",
            "payload": {"seller_id": 2, "order_id": order_id, "status": "accepted"},
        })
        assert r1["ok"] is True
        assert r1["data"]["order_status"] == "accepted"

        # Step 2: Preparing
        r2 = handle_request({
            "request_id": "r-cycle-2",
            "action": "update_order_status",
            "payload": {"seller_id": 2, "order_id": order_id, "status": "preparing"},
        })
        assert r2["ok"] is True
        assert r2["data"]["order_status"] == "preparing"

        # Step 3: Ready for pickup
        r3 = handle_request({
            "request_id": "r-cycle-3",
            "action": "update_order_status",
            "payload": {"seller_id": 2, "order_id": order_id, "status": "ready_for_pickup"},
        })
        assert r3["ok"] is True
        assert r3["data"]["order_status"] == "ready_for_pickup"

    def test_cannot_prepare_order_before_acceptance(self, db):
        """A placed/pending order cannot transition directly to 'preparing'."""
        order_id = self._create_order_for_bob()

        res = handle_request({
            "request_id": "r-invalid-prep",
            "action": "update_order_status",
            "payload": {"seller_id": 2, "order_id": order_id, "status": "preparing"},
        })
        assert res["ok"] is False
        assert res["error"]["code"] == "INVALID_TRANSITION"
        assert "must be accepted before preparation" in res["error"]["message"]

    def test_rejected_order_cannot_be_prepared(self, db):
        """A rejected order cannot transition to 'preparing'."""
        order_id = self._create_order_for_bob()

        # Reject
        handle_request({
            "request_id": "r-rej",
            "action": "update_order_status",
            "payload": {"seller_id": 2, "order_id": order_id, "status": "rejected"},
        })

        # Attempt to prepare
        res = handle_request({
            "request_id": "r-prep-rej",
            "action": "update_order_status",
            "payload": {"seller_id": 2, "order_id": order_id, "status": "preparing"},
        })
        assert res["ok"] is False
        assert res["error"]["code"] == "INVALID_TRANSITION"
        assert "already rejected" in res["error"]["message"]

    def test_rejected_order_cannot_be_accepted(self, db):
        """A rejected order cannot transition back to 'accepted'."""
        order_id = self._create_order_for_bob()

        # Reject
        handle_request({
            "request_id": "r-rej-2",
            "action": "update_order_status",
            "payload": {"seller_id": 2, "order_id": order_id, "status": "rejected"},
        })

        # Attempt to accept
        res = handle_request({
            "request_id": "r-acc-rej",
            "action": "update_order_status",
            "payload": {"seller_id": 2, "order_id": order_id, "status": "accepted"},
        })
        assert res["ok"] is False
        assert res["error"]["code"] == "INVALID_TRANSITION"

    def test_cannot_reject_order_already_in_preparation(self, db):
        """An order currently in preparation cannot be rejected."""
        order_id = self._create_order_for_bob()
        handle_request({
            "request_id": "r-acc-3",
            "action": "update_order_status",
            "payload": {"seller_id": 2, "order_id": order_id, "status": "accepted"},
        })
        handle_request({
            "request_id": "r-prep-3",
            "action": "update_order_status",
            "payload": {"seller_id": 2, "order_id": order_id, "status": "preparing"},
        })

        res = handle_request({
            "request_id": "r-rej-prep",
            "action": "update_order_status",
            "payload": {"seller_id": 2, "order_id": order_id, "status": "rejected"},
        })
        assert res["ok"] is False
        assert res["error"]["code"] == "INVALID_TRANSITION"
        assert "being prepared" in res["error"]["message"]

    def test_cannot_update_ready_order(self, db):
        """An order marked ready_for_pickup cannot be moved back or rejected."""
        order_id = self._create_order_for_bob()
        handle_request({
            "request_id": "r-acc-4",
            "action": "update_order_status",
            "payload": {"seller_id": 2, "order_id": order_id, "status": "accepted"},
        })
        handle_request({
            "request_id": "r-prep-4",
            "action": "update_order_status",
            "payload": {"seller_id": 2, "order_id": order_id, "status": "preparing"},
        })
        handle_request({
            "request_id": "r-rdy-4",
            "action": "update_order_status",
            "payload": {"seller_id": 2, "order_id": order_id, "status": "ready_for_pickup"},
        })

        # Try to reject
        res = handle_request({
            "request_id": "r-rej-rdy",
            "action": "update_order_status",
            "payload": {"seller_id": 2, "order_id": order_id, "status": "rejected"},
        })
        assert res["ok"] is False
        assert res["error"]["code"] == "INVALID_TRANSITION"

    def test_orders_filtering_by_status(self, db):
        """Filter incoming orders by status."""
        o1 = self._create_order_for_bob()
        o2 = self._create_order_for_bob()

        # Accept o2
        handle_request({
            "request_id": "r-acc-o2",
            "action": "update_order_status",
            "payload": {"seller_id": 2, "order_id": o2, "status": "accepted"},
        })

        # Filter pending
        res_pending = handle_request({
            "request_id": "r-filt-pend",
            "action": "get_seller_orders",
            "payload": {"seller_id": 2, "status": "pending"},
        })
        assert res_pending["ok"] is True
        ids = [o["order_id"] for o in res_pending["data"]]
        assert o1 in ids
        assert o2 not in ids

        # Filter accepted
        res_acc = handle_request({
            "request_id": "r-filt-acc",
            "action": "get_seller_orders",
            "payload": {"seller_id": 2, "status": "accepted"},
        })
        assert res_acc["ok"] is True
        ids_acc = [o["order_id"] for o in res_acc["data"]]
        assert o2 in ids_acc
        assert o1 not in ids_acc


# =====================================================================
# 3. Menu Management Tests
# =====================================================================
class TestSellerMenuManagement:

    def test_seller_can_view_menu(self, db):
        """Bob Seller can view all menu items belonging to Hyderabad Spice House."""
        req = {
            "request_id": "r-menu-1",
            "action": "get_seller_menu",
            "payload": {"seller_id": 2},
        }
        res = handle_request(req)
        assert res["ok"] is True
        items = res["data"]
        assert len(items) == 2
        names = {i["name"] for i in items}
        assert "Chicken Biryani" in names
        assert "Veg Biryani" in names

    def test_seller_can_add_menu_item(self, db):
        """Bob Seller can add a new menu item to their restaurant."""
        req = {
            "request_id": "r-add-item",
            "action": "add_menu_item",
            "payload": {
                "seller_id": 2,
                "name": "Mutton Biryani",
                "description": "Fragrant basmati rice with slow-cooked mutton.",
                "price": 280.0,
                "is_available": 1,
            },
        }
        res = handle_request(req)
        assert res["ok"] is True
        data = res["data"]
        assert data["name"] == "Mutton Biryani"
        assert data["price"] == 280.0
        assert data["restaurant_id"] == 1
        assert "item_id" in data

        # Verify item shows up in seller menu
        menu_res = handle_request({
            "request_id": "r-verify-menu",
            "action": "get_seller_menu",
            "payload": {"seller_id": 2},
        })
        assert any(i["name"] == "Mutton Biryani" for i in menu_res["data"])

    def test_add_menu_item_validation_empty_name(self, db):
        """Adding a menu item with an empty or whitespace name is rejected."""
        req = {
            "request_id": "r-bad-name",
            "action": "add_menu_item",
            "payload": {"seller_id": 2, "name": "   ", "price": 150.0},
        }
        res = handle_request(req)
        assert res["ok"] is False
        assert res["error"]["code"] == "INVALID_PAYLOAD"

    @pytest.mark.parametrize("bad_price", [0, -50, -0.01, "free", None, True])
    def test_add_menu_item_validation_invalid_price(self, db, bad_price):
        """Adding a menu item with zero, negative, or invalid price is rejected."""
        req = {
            "request_id": "r-bad-price",
            "action": "add_menu_item",
            "payload": {"seller_id": 2, "name": "Gulab Jamun", "price": bad_price},
        }
        res = handle_request(req)
        assert res["ok"] is False
        assert res["error"]["code"] == "INVALID_PAYLOAD"

    def test_seller_can_edit_menu_item_name_and_price(self, db):
        """Bob Seller can edit existing menu item's name and price."""
        req = {
            "request_id": "r-edit-item",
            "action": "update_menu_item",
            "payload": {
                "seller_id": 2,
                "item_id": 1,
                "name": "Special Chicken Biryani",
                "price": 195.0,
                "description": "Premium chicken pieces in saffron basmati rice.",
            },
        }
        res = handle_request(req)
        assert res["ok"] is True
        data = res["data"]
        assert data["name"] == "Special Chicken Biryani"
        assert data["price"] == 195.0

        # Verify DB updated
        with get_connection() as conn:
            row = conn.execute("SELECT name, price FROM menu_items WHERE item_id = 1;").fetchone()
            assert row["name"] == "Special Chicken Biryani"
            assert row["price"] == 195.0

    def test_price_change_preserves_past_order_snapshots(self, db):
        """Editing menu price must NOT alter past order snapshots in order_items."""
        # 1. Place order at original price (₹180.0)
        c_res = handle_request({
            "request_id": "c-ord-snap",
            "action": "create_order",
            "payload": {
                "customer_id": 1,
                "restaurant_id": 1,
                "items": [{"item_id": 1, "quantity": 1}],
            },
        })
        order_id = c_res["data"]["order_id"]
        assert c_res["data"]["total_amount"] == 180.0

        # 2. Seller increases price to ₹250.0
        handle_request({
            "request_id": "r-edit-price",
            "action": "update_menu_item",
            "payload": {"seller_id": 2, "item_id": 1, "price": 250.0},
        })

        # 3. Retrieve past order receipt
        ord_res = handle_request({
            "request_id": "r-get-snap-order",
            "action": "get_order",
            "payload": {"order_id": order_id},
        })
        assert ord_res["ok"] is True
        # Past order snapshot remains ₹180.0!
        assert ord_res["data"]["total_amount"] == 180.0
        assert ord_res["data"]["items"][0]["unit_price"] == 180.0

    def test_seller_can_toggle_availability(self, db):
        """Bob Seller can toggle item availability between available and sold out."""
        # Toggle off
        r1 = handle_request({
            "request_id": "r-avail-off",
            "action": "update_menu_item_availability",
            "payload": {"seller_id": 2, "item_id": 1, "is_available": 0},
        })
        assert r1["ok"] is True
        assert r1["data"]["is_available"] == 0

        # Verify customer cannot order it
        order_res = handle_request({
            "request_id": "c-try-order-unavail",
            "action": "create_order",
            "payload": {
                "customer_id": 1,
                "restaurant_id": 1,
                "items": [{"item_id": 1, "quantity": 1}],
            },
        })
        assert order_res["ok"] is False
        assert order_res["error"]["code"] == "ITEM_UNAVAILABLE"

        # Toggle back on
        r2 = handle_request({
            "request_id": "r-avail-on",
            "action": "update_menu_item_availability",
            "payload": {"seller_id": 2, "item_id": 1, "is_available": 1},
        })
        assert r2["ok"] is True
        assert r2["data"]["is_available"] == 1

        # Verify customer can now order it
        order_res_2 = handle_request({
            "request_id": "c-try-order-avail",
            "action": "create_order",
            "payload": {
                "customer_id": 1,
                "restaurant_id": 1,
                "items": [{"item_id": 1, "quantity": 1}],
            },
        })
        assert order_res_2["ok"] is True


# =====================================================================
# 4. Payment & Order Lifecycle Regression Tests
# =====================================================================
class TestPaymentOrderLifecycleIntegration:

    def _create_order(self):
        res = handle_request({
            "request_id": "c-ord-reg",
            "action": "create_order",
            "payload": {
                "customer_id": 1,
                "restaurant_id": 1,
                "items": [{"item_id": 1, "quantity": 1}],
            },
        })
        assert res["ok"] is True
        return res["data"]["order_id"]

    def test_payment_after_seller_rejection_must_fail(self, db):
        """Payment after seller rejection must be rejected and preserve rejected state."""
        order_id = self._create_order()

        # Seller rejects the order
        rej_res = handle_request({
            "request_id": "s-rej",
            "action": "update_order_status",
            "payload": {"seller_id": 2, "order_id": order_id, "status": "rejected"},
        })
        assert rej_res["ok"] is True

        # Customer attempts to simulate payment
        pay_res = handle_request({
            "request_id": "c-pay-rej",
            "action": "simulate_payment",
            "payload": {"order_id": order_id},
        })
        assert pay_res["ok"] is False
        assert pay_res["error"]["code"] == "INVALID_REQUEST"
        assert "Cannot process payment for rejected order" in pay_res["error"]["message"]

        # Verify DB: order remains rejected, payment remains pending
        with get_connection() as conn:
            row = conn.execute(
                "SELECT order_status, payment_status FROM orders WHERE order_id = ?;",
                (order_id,),
            ).fetchone()
            assert row["order_status"] == "rejected"
            assert row["payment_status"] == "pending"

            # Payments table must NOT have a paid record
            pay_row = conn.execute("SELECT * FROM payments WHERE order_id = ?;", (order_id,)).fetchone()
            assert pay_row is None

    def test_payment_while_preparing_preserves_preparing_status(self, db):
        """Payment while preparing must succeed in payment but preserve preparing status."""
        order_id = self._create_order()

        # Seller accepts and starts preparing
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

        # Customer pays
        pay_res = handle_request({
            "request_id": "c-pay-prep",
            "action": "simulate_payment",
            "payload": {"order_id": order_id},
        })
        assert pay_res["ok"] is True
        assert pay_res["data"]["payment_status"] == "paid"
        assert pay_res["data"]["order_status"] == "preparing"

        # Verify DB: remains preparing
        with get_connection() as conn:
            row = conn.execute(
                "SELECT order_status, payment_status FROM orders WHERE order_id = ?;",
                (order_id,),
            ).fetchone()
            assert row["order_status"] == "preparing"
            assert row["payment_status"] == "paid"

    def test_payment_while_ready_for_pickup_preserves_ready_status(self, db):
        """Payment while ready_for_pickup must succeed and preserve ready_for_pickup status."""
        order_id = self._create_order()

        # Advance to ready_for_pickup
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
        handle_request({
            "request_id": "s-rdy",
            "action": "update_order_status",
            "payload": {"seller_id": 2, "order_id": order_id, "status": "ready_for_pickup"},
        })

        # Customer pays
        pay_res = handle_request({
            "request_id": "c-pay-rdy",
            "action": "simulate_payment",
            "payload": {"order_id": order_id},
        })
        assert pay_res["ok"] is True
        assert pay_res["data"]["payment_status"] == "paid"
        assert pay_res["data"]["order_status"] == "ready_for_pickup"

        with get_connection() as conn:
            row = conn.execute(
                "SELECT order_status, payment_status FROM orders WHERE order_id = ?;",
                (order_id,),
            ).fetchone()
            assert row["order_status"] == "ready_for_pickup"
            assert row["payment_status"] == "paid"

    def test_payment_while_accepted_preserves_accepted_status(self, db):
        """Payment while accepted preserves accepted status."""
        order_id = self._create_order()

        handle_request({
            "request_id": "s-acc",
            "action": "update_order_status",
            "payload": {"seller_id": 2, "order_id": order_id, "status": "accepted"},
        })

        pay_res = handle_request({
            "request_id": "c-pay-acc",
            "action": "simulate_payment",
            "payload": {"order_id": order_id},
        })
        assert pay_res["ok"] is True
        assert pay_res["data"]["payment_status"] == "paid"
        assert pay_res["data"]["order_status"] == "accepted"

        with get_connection() as conn:
            row = conn.execute(
                "SELECT order_status, payment_status FROM orders WHERE order_id = ?;",
                (order_id,),
            ).fetchone()
            assert row["order_status"] == "accepted"
            assert row["payment_status"] == "paid"

    def test_failed_payment_never_changes_order_status(self, db):
        """Failed payment must record failed payment status without altering order status."""
        order_id = self._create_order()

        # Advance to preparing
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

        # Simulate failed payment
        pay_res = handle_request({
            "request_id": "c-pay-fail",
            "action": "simulate_payment",
            "payload": {"order_id": order_id, "simulate_failure": True},
        })
        assert pay_res["ok"] is True
        assert pay_res["data"]["payment_status"] == "failed"
        assert pay_res["data"]["order_status"] == "preparing"

        with get_connection() as conn:
            row = conn.execute(
                "SELECT order_status, payment_status FROM orders WHERE order_id = ?;",
                (order_id,),
            ).fetchone()
            assert row["order_status"] == "preparing"
            assert row["payment_status"] == "failed"

    def test_existing_successful_payment_on_placed_advances_to_accepted(self, db):
        """Payment on a placed order transitions order_status to accepted."""
        order_id = self._create_order()

        pay_res = handle_request({
            "request_id": "c-pay-placed",
            "action": "simulate_payment",
            "payload": {"order_id": order_id},
        })
        assert pay_res["ok"] is True
        assert pay_res["data"]["payment_status"] == "paid"
        assert pay_res["data"]["order_status"] == "accepted"

        with get_connection() as conn:
            row = conn.execute(
                "SELECT order_status, payment_status FROM orders WHERE order_id = ?;",
                (order_id,),
            ).fetchone()
            assert row["order_status"] == "accepted"
            assert row["payment_status"] == "paid"

    def test_duplicate_successful_payments_prevented(self, db):
        """Duplicate payment on an already paid order must be rejected."""
        order_id = self._create_order()

        # First payment succeeds
        p1 = handle_request({
            "request_id": "c-pay-1",
            "action": "simulate_payment",
            "payload": {"order_id": order_id},
        })
        assert p1["ok"] is True

        # Second payment fails
        p2 = handle_request({
            "request_id": "c-pay-2",
            "action": "simulate_payment",
            "payload": {"order_id": order_id},
        })
        assert p2["ok"] is False
        assert p2["error"]["code"] == "PAYMENT_ALREADY_COMPLETED"
        assert "Duplicate payment is not allowed" in p2["error"]["message"]

