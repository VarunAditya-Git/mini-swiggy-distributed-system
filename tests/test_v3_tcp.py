"""
tests/test_v3_tcp.py
====================
Integration tests for Mini Swiggy V3 Seller Dashboard operations over real TCP sockets:
  - Seller profile and restaurant retrieval over TCP
  - Incoming orders retrieval and status filtering over TCP
  - Order status transitions (Accept, Prepare, Ready for Pickup, Reject) over TCP
  - Menu management (add, edit, toggle availability) over TCP
  - Seller authorization enforcement over TCP
  - Multi-tenant restaurant isolation over TCP
"""

import pathlib
import pytest
import socket
import sys
import threading

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))

import database.db as db_module
from database.db import get_connection, init_db
from database.seed import seed_data
from clients.tcp_client import SwiggyClient
from server.tcp_server import serve_client


def _start_server_thread(host: str, port: int, db_path, monkeypatch) -> tuple[threading.Thread, int]:
    """Start the TCP server on an ephemeral OS-assigned port."""
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


HOST = "127.0.0.1"


@pytest.fixture
def running_server(tmp_path, monkeypatch):
    """Start an ephemeral TCP server backed by a temporary SQLite database."""
    temp_db = tmp_path / "test_v3_tcp.db"
    monkeypatch.setattr(db_module, "DB_PATH", temp_db)
    init_db()
    seed_data()

    # Seed extra seller Eve (ID: 5) owning Dosa Corner (Restaurant 2)
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO users (name, email, role, is_active) VALUES (?, ?, ?, ?);",
            ("Eve Seller", "eve@example.com", "seller", 1),
        )
        eve_id = conn.execute("SELECT user_id FROM users WHERE email = 'eve@example.com';").fetchone()["user_id"]
        conn.execute("UPDATE restaurants SET owner_user_id = ? WHERE restaurant_id = 2;", (eve_id,))

    thread, port = _start_server_thread(HOST, 0, temp_db, monkeypatch)
    yield HOST, port
    thread.join(timeout=3.0)


class TestSellerOperationsOverTCP:

    def test_get_seller_profile_over_tcp(self, running_server):
        """Seller can fetch profile and stats over raw TCP socket."""
        host, port = running_server
        with SwiggyClient(host=host, port=port) as client:
            res = client.get_seller_profile(2)
            assert res["ok"] is True
            assert res["data"]["seller"]["name"] == "Bob Seller"
            assert res["data"]["restaurant"]["name"] == "Hyderabad Spice House"
            assert res["data"]["stats"]["total_orders"] == 0

    def test_complete_kitchen_order_flow_over_tcp(self, running_server):
        """Customer creates order -> Seller accepts -> Seller prepares -> Seller marks ready over TCP."""
        host, port = running_server
        with SwiggyClient(host=host, port=port) as client:
            # 1. Customer creates order
            ord_res = client.create_order(
                customer_id=1,
                restaurant_id=1,
                items=[{"item_id": 1, "quantity": 1}],
            )
            assert ord_res["ok"] is True
            order_id = ord_res["data"]["order_id"]
            assert ord_res["data"]["order_status"] == "placed"

            # 2. Seller views orders
            seller_orders = client.get_seller_orders(seller_id=2)
            assert seller_orders["ok"] is True
            assert len(seller_orders["data"]) == 1
            assert seller_orders["data"][0]["order_id"] == order_id

            # 3. Seller accepts order
            acc_res = client.update_order_status(seller_id=2, order_id=order_id, status="accepted")
            assert acc_res["ok"] is True
            assert acc_res["data"]["order_status"] == "accepted"

            # 4. Seller starts preparation
            prep_res = client.update_order_status(seller_id=2, order_id=order_id, status="preparing")
            assert prep_res["ok"] is True
            assert prep_res["data"]["order_status"] == "preparing"

            # 5. Seller marks ready for pickup
            ready_res = client.update_order_status(seller_id=2, order_id=order_id, status="ready_for_pickup")
            assert ready_res["ok"] is True
            assert ready_res["data"]["order_status"] == "ready_for_pickup"

            # 6. Customer retrieves updated status
            cust_view = client.get_order(order_id)
            assert cust_view["ok"] is True
            assert cust_view["data"]["order_status"] == "ready_for_pickup"

    def test_seller_reject_order_over_tcp(self, running_server):
        """Seller rejects an incoming order over TCP."""
        host, port = running_server
        with SwiggyClient(host=host, port=port) as client:
            ord_res = client.create_order(
                customer_id=1,
                restaurant_id=1,
                items=[{"item_id": 2, "quantity": 1}],
            )
            order_id = ord_res["data"]["order_id"]

            rej_res = client.update_order_status(seller_id=2, order_id=order_id, status="rejected")
            assert rej_res["ok"] is True
            assert rej_res["data"]["order_status"] == "rejected"

            # Verify cannot transition rejected order to preparing
            bad_prep = client.update_order_status(seller_id=2, order_id=order_id, status="preparing")
            assert bad_prep["ok"] is False
            assert bad_prep["error"]["code"] == "INVALID_TRANSITION"

    def test_menu_management_over_tcp(self, running_server):
        """Seller adds, updates, and toggles availability over TCP socket."""
        host, port = running_server
        with SwiggyClient(host=host, port=port) as client:
            # 1. Add menu item
            add_res = client.add_menu_item(
                seller_id=2,
                name="Chicken 65",
                price=160.0,
                description="Crispy spicy fried chicken.",
                is_available=1,
            )
            assert add_res["ok"] is True
            item_id = add_res["data"]["item_id"]
            assert add_res["data"]["name"] == "Chicken 65"

            # 2. Update price and description
            edit_res = client.update_menu_item(
                seller_id=2,
                item_id=item_id,
                price=175.0,
                description="Special Hyderabadi Chicken 65.",
            )
            assert edit_res["ok"] is True
            assert edit_res["data"]["price"] == 175.0

            # 3. Toggle availability off
            avail_res = client.update_menu_item_availability(seller_id=2, item_id=item_id, is_available=0)
            assert avail_res["ok"] is True
            assert avail_res["data"]["is_available"] == 0

            # 4. Ordering unavailable item is rejected over TCP
            order_fail = client.create_order(
                customer_id=1,
                restaurant_id=1,
                items=[{"item_id": item_id, "quantity": 1}],
            )
            assert order_fail["ok"] is False
            assert order_fail["error"]["code"] == "ITEM_UNAVAILABLE"

    def test_seller_authorization_over_tcp(self, running_server):
        """Non-seller and cross-restaurant access over TCP are rejected."""
        host, port = running_server
        with SwiggyClient(host=host, port=port) as client:
            # Alice Customer attempts seller profile -> UNAUTHORIZED_ROLE
            res_cust = client.get_seller_profile(1)
            assert res_cust["ok"] is False
            assert res_cust["error"]["code"] == "UNAUTHORIZED_ROLE"

            # Bob Seller attempts to view Restaurant 2 order -> FORBIDDEN
            ord2 = client.create_order(
                customer_id=1,
                restaurant_id=2,
                items=[{"item_id": 3, "quantity": 1}],
            )
            order_id = ord2["data"]["order_id"]

            res_forbidden = client.get_seller_order(seller_id=2, order_id=order_id)
            assert res_forbidden["ok"] is False
            assert res_forbidden["error"]["code"] == "FORBIDDEN"
