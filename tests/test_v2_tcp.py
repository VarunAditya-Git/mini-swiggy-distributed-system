"""
tests/test_v2_tcp.py
====================
Integration tests for Mini Swiggy V2 core ordering and payment over real TCP sockets.

Tests cover:
  - create_order over TCP (valid, invalid, empty cart, wrong restaurant)
  - simulate_payment over TCP (success, failure, repeated payment rejection)
  - get_order over TCP
  - get_order_history over TCP
  - End-to-end full customer lifecycle over a single persistent TCP connection
"""

import socket
import sys
import threading
import pathlib
import pytest

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))

import database.db as db_module
from database.db import init_db
from database.seed import seed_data
from clients.tcp_client import SwiggyClient
from server.tcp_server import serve_client


# -------------------------------------------------------------------
# Helper: ephemeral server thread
# -------------------------------------------------------------------
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
    """
    Spins up a real TCP server on an ephemeral port backed by a freshly seeded database.
    Yields (host, port). Cleaned up automatically.
    """
    temp_db = tmp_path / "test_tcp_v2.db"
    monkeypatch.setattr(db_module, "DB_PATH", temp_db)
    init_db()
    seed_data()

    thread, port = _start_server_thread(HOST, 0, temp_db, monkeypatch)
    yield HOST, port


# -------------------------------------------------------------------
# Test: create_order over TCP
# -------------------------------------------------------------------
class TestCreateOrderOverTCP:

    def test_create_order_success(self, running_server):
        host, port = running_server
        with SwiggyClient(host=host, port=port) as client:
            res = client.create_order(
                customer_id=1,
                restaurant_id=1,
                items=[{"item_id": 1, "quantity": 2}, {"item_id": 2, "quantity": 1}],
            )
            assert res["ok"] is True
            data = res["data"]
            assert data["customer_id"] == 1
            assert data["restaurant_id"] == 1
            assert data["total_amount"] == 480.0
            assert data["order_status"] == "placed"
            assert data["payment_status"] == "pending"
            assert len(data["items"]) == 2

    def test_create_order_empty_cart_rejected(self, running_server):
        host, port = running_server
        with SwiggyClient(host=host, port=port) as client:
            res = client.create_order(customer_id=1, restaurant_id=1, items=[])
            assert res["ok"] is False
            assert res["error"]["code"] == "INVALID_PAYLOAD"

    def test_create_order_wrong_restaurant_item_rejected(self, running_server):
        host, port = running_server
        with SwiggyClient(host=host, port=port) as client:
            # Item 3 belongs to restaurant 2, but we order from restaurant 1
            res = client.create_order(
                customer_id=1,
                restaurant_id=1,
                items=[{"item_id": 3, "quantity": 1}],
            )
            assert res["ok"] is False
            assert res["error"]["code"] == "INVALID_REQUEST"


# -------------------------------------------------------------------
# Test: simulate_payment over TCP
# -------------------------------------------------------------------
class TestSimulatePaymentOverTCP:

    def test_successful_payment(self, running_server):
        host, port = running_server
        with SwiggyClient(host=host, port=port) as client:
            ord_res = client.create_order(
                customer_id=1,
                restaurant_id=1,
                items=[{"item_id": 1, "quantity": 1}],
            )
            order_id = ord_res["data"]["order_id"]

            pay_res = client.simulate_payment(order_id=order_id, payment_method="upi")
            assert pay_res["ok"] is True
            assert pay_res["data"]["payment_status"] == "paid"
            assert pay_res["data"]["order_status"] == "accepted"
            assert pay_res["data"]["amount"] == 180.0

    def test_failed_payment_simulation(self, running_server):
        host, port = running_server
        with SwiggyClient(host=host, port=port) as client:
            ord_res = client.create_order(
                customer_id=1,
                restaurant_id=1,
                items=[{"item_id": 1, "quantity": 1}],
            )
            order_id = ord_res["data"]["order_id"]

            pay_res = client.simulate_payment(order_id=order_id, simulate_failure=True)
            assert pay_res["ok"] is True
            assert pay_res["data"]["payment_status"] == "failed"
            assert pay_res["data"]["order_status"] == "placed"

    def test_repeated_payment_rejected(self, running_server):
        host, port = running_server
        with SwiggyClient(host=host, port=port) as client:
            ord_res = client.create_order(
                customer_id=1,
                restaurant_id=1,
                items=[{"item_id": 1, "quantity": 1}],
            )
            order_id = ord_res["data"]["order_id"]

            # Pay once
            p1 = client.simulate_payment(order_id=order_id)
            assert p1["ok"] is True

            # Attempt to pay again
            p2 = client.simulate_payment(order_id=order_id)
            assert p2["ok"] is False
            assert p2["error"]["code"] == "PAYMENT_ALREADY_COMPLETED"

    def test_payment_invalid_order_id(self, running_server):
        host, port = running_server
        with SwiggyClient(host=host, port=port) as client:
            res = client.simulate_payment(order_id=9999)
            assert res["ok"] is False
            assert res["error"]["code"] == "NOT_FOUND"


# -------------------------------------------------------------------
# Test: get_order and get_order_history over TCP
# -------------------------------------------------------------------
class TestOrderRetrievalOverTCP:

    def test_get_order(self, running_server):
        host, port = running_server
        with SwiggyClient(host=host, port=port) as client:
            ord_res = client.create_order(
                customer_id=1,
                restaurant_id=1,
                items=[{"item_id": 1, "quantity": 2}],
            )
            order_id = ord_res["data"]["order_id"]

            client.simulate_payment(order_id=order_id)

            get_res = client.get_order(order_id=order_id)
            assert get_res["ok"] is True
            assert get_res["data"]["order_id"] == order_id
            assert get_res["data"]["payment_status"] == "paid"
            assert get_res["data"]["items"][0]["quantity"] == 2

    def test_get_order_history(self, running_server):
        host, port = running_server
        with SwiggyClient(host=host, port=port) as client:
            # Place two orders for customer 1
            client.create_order(
                customer_id=1,
                restaurant_id=1,
                items=[{"item_id": 1, "quantity": 1}],
            )
            client.create_order(
                customer_id=1,
                restaurant_id=1,
                items=[{"item_id": 2, "quantity": 2}],
            )

            hist_res = client.get_order_history(customer_id=1)
            assert hist_res["ok"] is True
            orders = hist_res["data"]
            assert len(orders) == 2
            # Check newest first
            assert orders[0]["order_id"] > orders[1]["order_id"]


# -------------------------------------------------------------------
# Test: Full End-to-End lifecycle over a SINGLE persistent TCP connection
# -------------------------------------------------------------------
class TestEndToEndLifecycleOverSingleConnection:

    def test_complete_ordering_flow(self, running_server):
        """
        Simulate a real customer journey over ONE socket connection:
          1. list_restaurants
          2. list_menu
          3. create_order
          4. simulate_payment
          5. get_order
          6. get_order_history
        """
        host, port = running_server
        with SwiggyClient(host=host, port=port) as client:
            # 1. Discover restaurants
            rests = client.list_restaurants()
            assert rests["ok"] is True
            assert len(rests["data"]) == 3
            first_rest_id = rests["data"][0]["restaurant_id"]

            # 2. View menu
            menu = client.list_menu(first_rest_id)
            assert menu["ok"] is True
            assert len(menu["data"]) == 2
            first_item = menu["data"][0]

            # 3. Create order
            order = client.create_order(
                customer_id=1,
                restaurant_id=first_rest_id,
                items=[{"item_id": first_item["item_id"], "quantity": 3}],
            )
            assert order["ok"] is True
            order_id = order["data"]["order_id"]
            expected_total = round(first_item["price"] * 3, 2)
            assert order["data"]["total_amount"] == expected_total
            assert order["data"]["order_status"] == "placed"

            # 4. Simulate payment
            payment = client.simulate_payment(order_id=order_id, payment_method="simulated_card")
            assert payment["ok"] is True
            assert payment["data"]["payment_status"] == "paid"
            assert payment["data"]["order_status"] == "accepted"

            # 5. Check order status
            fetch = client.get_order(order_id=order_id)
            assert fetch["ok"] is True
            assert fetch["data"]["order_status"] == "accepted"
            assert fetch["data"]["payment"]["payment_status"] == "paid"

            # 6. Check order history
            hist = client.get_order_history(customer_id=1)
            assert hist["ok"] is True
            assert len(hist["data"]) >= 1
            assert hist["data"][0]["order_id"] == order_id
