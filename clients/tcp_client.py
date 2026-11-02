"""
clients/tcp_client.py
=====================
Mini Swiggy V1 — TCP client library and CLI demo.

This module provides:
  1. SwiggyClient — a class that manages a TCP connection, sends
     newline-delimited JSON requests, and reads complete responses.
  2. A simple command-line demo that pings the server, lists
     restaurants, and lists a menu.

Why a class?
  Grouping the socket, host, and port together in one object makes it
  easy to send multiple requests over the same connection without
  reconnecting every time.

Usage (Windows PowerShell, server must already be running):
    python -m clients.tcp_client ping
    python -m clients.tcp_client list_restaurants
    python -m clients.tcp_client list_menu 1
    python -m clients.tcp_client list_menu 2
"""

import argparse
import json
import socket
import sys
import pathlib
import uuid

# Add project root to path so the module is importable from anywhere.
sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))

# -------------------------------------------------------------------
# Configuration defaults (must match the server)
# -------------------------------------------------------------------
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 5000

# How long to wait when connecting or reading a response.
CONNECT_TIMEOUT = 5.0
READ_TIMEOUT    = 10.0

# Maximum bytes to buffer while reading a single response.
MAX_RESPONSE_BYTES = 64 * 1024  # 64 KiB


# -------------------------------------------------------------------
# SwiggyClient
# -------------------------------------------------------------------
class SwiggyClient:
    """
    A simple TCP client for the Mini Swiggy V1 server.

    It handles:
      - Connecting to the server.
      - Encoding requests as UTF-8 newline-terminated JSON.
      - Reading complete newline-terminated responses (including partial
        delivery across multiple recv() calls).
      - Sending multiple requests over the same connection.
      - Clean shutdown.

    Example usage:
        with SwiggyClient() as client:
            response = client.send("ping", {})
            print(response)
    """

    def __init__(self, host: str = DEFAULT_HOST, port: int = DEFAULT_PORT):
        self.host = host
        self.port = port
        self._sock: socket.socket | None = None
        self._buffer: bytes = b""   # leftover bytes from a previous recv()

    # --- Context manager support (the 'with' statement) ---
    def __enter__(self) -> "SwiggyClient":
        self.connect()
        return self

    def __exit__(self, *args) -> None:
        self.close()

    # --- Connection management ---
    def connect(self) -> None:
        """
        Open a TCP connection to the server.

        Raises:
            ConnectionRefusedError: If the server is not running.
            socket.timeout:         If the server does not respond in time.
            OSError:                For other network problems.
        """
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.settimeout(CONNECT_TIMEOUT)
        try:
            self._sock.connect((self.host, self.port))
        except ConnectionRefusedError:
            self._sock.close()
            self._sock = None
            raise ConnectionRefusedError(
                f"Could not connect to server at {self.host}:{self.port}. "
                "Is the server running?"
            )
        except socket.timeout:
            self._sock.close()
            self._sock = None
            raise socket.timeout(
                f"Connection to {self.host}:{self.port} timed out."
            )
        # Switch to the read timeout after connecting.
        self._sock.settimeout(READ_TIMEOUT)

    def close(self) -> None:
        """Close the TCP connection."""
        if self._sock:
            try:
                self._sock.close()
            except OSError:
                pass
            self._sock = None

    # --- Sending and receiving ---
    def send(self, action: str, payload: dict,
             request_id: str | None = None) -> dict:
        """
        Send one request and return the parsed response dict.

        Args:
            action:     The action name (e.g. 'ping', 'list_restaurants').
            payload:    A dict of action-specific parameters.
            request_id: Optional ID; a UUID is generated if not provided.

        Returns:
            The server's response as a Python dict.

        Raises:
            RuntimeError:      If not connected.
            ConnectionError:   If the server closes the connection early.
            json.JSONDecodeError: If the response is not valid JSON.
        """
        if self._sock is None:
            raise RuntimeError("Not connected. Call connect() first.")

        if request_id is None:
            request_id = str(uuid.uuid4())[:8]   # short unique ID

        request = {"request_id": request_id, "action": action, "payload": payload}
        line = json.dumps(request, ensure_ascii=False) + "\n"

        # sendall() loops internally until all bytes are sent.
        self._sock.sendall(line.encode("utf-8"))

        # Read until we have a complete line (ends with '\n').
        return self._read_response()

    # --- Convenience methods for V1 & V2 actions ---
    def ping(self) -> dict:
        """Send a health-check ping."""
        return self.send("ping", {})

    def list_restaurants(self) -> dict:
        """List all restaurants in the system."""
        return self.send("list_restaurants", {})

    def list_menu(self, restaurant_id: int) -> dict:
        """List menu items for a specific restaurant."""
        return self.send("list_menu", {"restaurant_id": restaurant_id})

    def create_order(self, customer_id: int, restaurant_id: int,
                     items: list[dict]) -> dict:
        """
        Create a new food order.

        Args:
            customer_id:   ID of the customer user.
            restaurant_id: ID of the chosen restaurant.
            items:         List of dicts, e.g. [{"item_id": 1, "quantity": 2}].
        """
        return self.send("create_order", {
            "customer_id": customer_id,
            "restaurant_id": restaurant_id,
            "items": items,
        })

    def simulate_payment(self, order_id: int, simulate_status: str = "paid",
                         payment_method: str = "simulated_upi",
                         simulate_failure: bool = False,
                         customer_id: int | None = None) -> dict:
        """
        Simulate payment for an order.

        Args:
            order_id:         ID of the order to pay for.
            simulate_status:  'paid' or 'failed'.
            payment_method:   Payment method description.
            simulate_failure: If True, forces payment failure simulation.
            customer_id:      Optional customer user ID for ownership validation.
        """
        payload = {
            "order_id": order_id,
            "simulate_status": simulate_status,
            "payment_method": payment_method,
            "simulate_failure": simulate_failure,
        }
        if customer_id is not None:
            payload["customer_id"] = customer_id
        return self.send("simulate_payment", payload)

    def get_order(self, order_id: int) -> dict:
        """Retrieve details and item snapshots of an order."""
        return self.send("get_order", {"order_id": order_id})

    def get_order_history(self, customer_id: int) -> dict:
        """Retrieve full order history for a customer."""
        return self.send("get_order_history", {"customer_id": customer_id})

    # ---------------------------------------------------------------
    # V3 Seller Dashboard operations
    # ---------------------------------------------------------------
    def get_seller_profile(self, seller_id: int) -> dict:
        """Retrieve seller profile, restaurant info, and dashboard stats."""
        return self.send("get_seller_profile", {"seller_id": seller_id})

    def get_seller_orders(self, seller_id: int, status: str = "") -> dict:
        """Retrieve orders belonging to seller's restaurant with optional status filter."""
        payload = {"seller_id": seller_id}
        if status:
            payload["status"] = status
        return self.send("get_seller_orders", payload)

    def get_seller_order(self, seller_id: int, order_id: int) -> dict:
        """Retrieve specific order details with seller authorization."""
        return self.send("get_seller_order", {"seller_id": seller_id, "order_id": order_id})

    def update_order_status(
        self,
        seller_id: int,
        order_id: int,
        status: str,
        reason: str = "",
        simulate_refund_failure: bool = False,
    ) -> dict:
        """Advance or reject order lifecycle status."""
        payload = {
            "seller_id": seller_id,
            "order_id": order_id,
            "status": status,
        }
        if reason:
            payload["reason"] = reason
        if simulate_refund_failure:
            payload["simulate_refund_failure"] = simulate_refund_failure
        return self.send("update_order_status", payload)

    def get_seller_menu(self, seller_id: int) -> dict:
        """Retrieve menu items belonging to seller's restaurant."""
        return self.send("get_seller_menu", {"seller_id": seller_id})

    def add_menu_item(self, seller_id: int, name: str, price: float, description: str = "", is_available: int = 1) -> dict:
        """Add a new menu item to seller's restaurant."""
        return self.send("add_menu_item", {
            "seller_id": seller_id,
            "name": name,
            "price": price,
            "description": description,
            "is_available": is_available,
        })

    def update_menu_item(self, seller_id: int, item_id: int, **fields) -> dict:
        """Update fields of an existing menu item."""
        payload = {"seller_id": seller_id, "item_id": item_id}
        payload.update(fields)
        return self.send("update_menu_item", payload)

    def update_menu_item_availability(self, seller_id: int, item_id: int, is_available: int) -> dict:
        """Toggle availability of a menu item."""
        return self.send("update_menu_item_availability", {
            "seller_id": seller_id,
            "item_id": item_id,
            "is_available": is_available,
        })

    # ---------------------------------------------------------------
    # V3.2 Refund operations
    # ---------------------------------------------------------------
    def get_refund(
        self,
        order_id: int,
        customer_id: int | None = None,
        seller_id: int | None = None,
    ) -> dict:
        """Retrieve refund details for an order."""
        payload = {"order_id": order_id}
        if customer_id is not None:
            payload["customer_id"] = customer_id
        if seller_id is not None:
            payload["seller_id"] = seller_id
        return self.send("get_refund", payload)

    def get_refund_history(
        self,
        customer_id: int | None = None,
        seller_id: int | None = None,
    ) -> dict:
        """Retrieve refund history for customer or seller."""
        payload = {}
        if customer_id is not None:
            payload["customer_id"] = customer_id
        if seller_id is not None:
            payload["seller_id"] = seller_id
        return self.send("get_refund_history", payload)

    def retry_refund(
        self,
        order_id: int,
        customer_id: int | None = None,
        seller_id: int | None = None,
        simulate_failure: bool = False,
    ) -> dict:
        """Retry a failed refund."""
        payload = {"order_id": order_id, "simulate_failure": simulate_failure}
        if customer_id is not None:
            payload["customer_id"] = customer_id
        if seller_id is not None:
            payload["seller_id"] = seller_id
        return self.send("retry_refund", payload)

    def _read_response(self) -> dict:
        """
        Read bytes from the socket until a complete newline-terminated
        response is available, then parse and return it.

        TCP may deliver the response in multiple chunks. We accumulate
        chunks in self._buffer until a '\\n' appears.
        """
        while b"\n" not in self._buffer:
            try:
                chunk = self._sock.recv(4096)
            except socket.timeout:
                raise socket.timeout("Timed out waiting for server response.")

            if not chunk:
                raise ConnectionError(
                    "Server closed the connection before sending a response."
                )

            self._buffer += chunk

            if len(self._buffer) > MAX_RESPONSE_BYTES:
                raise ValueError(
                    f"Response exceeded {MAX_RESPONSE_BYTES} bytes without a newline."
                )

        # We have at least one complete line.
        line_bytes, self._buffer = self._buffer.split(b"\n", 1)
        return json.loads(line_bytes.decode("utf-8"))


# -------------------------------------------------------------------
# Pretty-print helper
# -------------------------------------------------------------------
def _print_response(response: dict) -> None:
    """Print a server response in a readable format."""
    print(json.dumps(response, indent=2, ensure_ascii=False))


# -------------------------------------------------------------------
# Helper to parse items CLI argument: "1:2,2:1" or '[{"item_id": 1, "quantity": 2}]'
# -------------------------------------------------------------------
def _parse_items_arg(raw: str) -> list[dict]:
    """Parse items from JSON or shorthand like '1:2,2:1'."""
    raw = raw.strip()
    if raw.startswith("["):
        return json.loads(raw)
    items = []
    for pair in raw.split(","):
        pair = pair.strip()
        if not pair:
            continue
        if ":" not in pair:
            raise ValueError(f"Invalid item specification '{pair}'. Use format 'item_id:quantity' (e.g. 1:2)")
        item_id_s, qty_s = pair.split(":", 1)
        items.append({"item_id": int(item_id_s.strip()), "quantity": int(qty_s.strip())})
    return items


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Mini Swiggy TCP Client (V1, V2 & V3)")
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    subparsers = parser.add_subparsers(dest="command", required=True)

    # V1 actions
    subparsers.add_parser("ping", help="Send a health-check ping")
    subparsers.add_parser("list_restaurants", help="List all restaurants")

    menu_p = subparsers.add_parser("list_menu", help="List a restaurant's menu")
    menu_p.add_argument("restaurant_id", type=int, help="Integer ID of the restaurant")

    # V2 actions
    order_p = subparsers.add_parser("create_order", help="Create a food order")
    order_p.add_argument("--customer-id", type=int, required=True, help="Customer user ID")
    order_p.add_argument("--restaurant-id", type=int, required=True, help="Restaurant ID")
    order_p.add_argument("--items", type=str, required=True,
                         help="Items as JSON or shorthand 'item_id:qty,item_id:qty' (e.g. 1:2,2:1)")

    pay_p = subparsers.add_parser("simulate_payment", help="Simulate payment for an order")
    pay_p.add_argument("order_id", type=int, help="Order ID to pay for")
    pay_p.add_argument("--customer-id", type=int, default=None, help="Customer user ID for ownership validation")
    pay_p.add_argument("--fail", action="store_true", help="Simulate payment failure")
    pay_p.add_argument("--method", default="simulated_upi", help="Payment method name")

    get_ord_p = subparsers.add_parser("get_order", help="Retrieve details of a single order")
    get_ord_p.add_argument("order_id", type=int, help="Order ID to retrieve")

    hist_p = subparsers.add_parser("get_order_history", help="Retrieve order history for a customer")
    hist_p.add_argument("customer_id", type=int, help="Customer user ID")

    # V3 Seller Dashboard actions
    seller_prof_p = subparsers.add_parser("get_seller_profile", help="Retrieve seller profile and stats")
    seller_prof_p.add_argument("seller_id", type=int, help="Seller user ID")

    seller_ords_p = subparsers.add_parser("get_seller_orders", help="Retrieve orders for seller's restaurant")
    seller_ords_p.add_argument("seller_id", type=int, help="Seller user ID")
    seller_ords_p.add_argument("--status", default="", help="Filter orders by status")

    seller_ord_p = subparsers.add_parser("get_seller_order", help="Retrieve a single order for seller's restaurant")
    seller_ord_p.add_argument("seller_id", type=int, help="Seller user ID")
    seller_ord_p.add_argument("order_id", type=int, help="Order ID to retrieve")

    upd_status_p = subparsers.add_parser("update_order_status", help="Update lifecycle status of an order")
    upd_status_p.add_argument("seller_id", type=int, help="Seller user ID")
    upd_status_p.add_argument("order_id", type=int, help="Order ID to update")
    upd_status_p.add_argument("status", type=str, help="Target status (accepted, preparing, ready_for_pickup, rejected)")
    upd_status_p.add_argument("--reason", type=str, default="", help="Optional rejection or cancellation reason")
    upd_status_p.add_argument("--fail-refund", action="store_true", help="Simulate refund failure upon rejection")

    seller_menu_p = subparsers.add_parser("get_seller_menu", help="Retrieve menu items for seller's restaurant")
    seller_menu_p.add_argument("seller_id", type=int, help="Seller user ID")

    add_item_p = subparsers.add_parser("add_menu_item", help="Add a new menu item to seller's restaurant")
    add_item_p.add_argument("seller_id", type=int, help="Seller user ID")
    add_item_p.add_argument("--name", required=True, type=str, help="Menu item name")
    add_item_p.add_argument("--price", required=True, type=float, help="Item price in INR")
    add_item_p.add_argument("--description", default="", type=str, help="Item description")
    add_item_p.add_argument("--available", type=int, default=1, choices=[0, 1], help="Availability (1 or 0)")

    upd_item_p = subparsers.add_parser("update_menu_item", help="Update details of an existing menu item")
    upd_item_p.add_argument("seller_id", type=int, help="Seller user ID")
    upd_item_p.add_argument("item_id", type=int, help="Menu item ID to update")
    upd_item_p.add_argument("--name", type=str, default=None, help="Updated item name")
    upd_item_p.add_argument("--price", type=float, default=None, help="Updated item price")
    upd_item_p.add_argument("--description", type=str, default=None, help="Updated description")
    upd_item_p.add_argument("--available", type=int, default=None, choices=[0, 1], help="Updated availability (1 or 0)")

    avail_p = subparsers.add_parser("update_menu_item_availability", help="Toggle availability of a menu item")
    avail_p.add_argument("seller_id", type=int, help="Seller user ID")
    avail_p.add_argument("item_id", type=int, help="Menu item ID")
    avail_p.add_argument("is_available", type=int, choices=[0, 1], help="Availability (1=available, 0=sold out)")

    refund_p = subparsers.add_parser("get_refund", help="Retrieve refund details for an order")
    refund_p.add_argument("order_id", type=int, help="Order ID")
    refund_p.add_argument("--customer-id", type=int, default=None, help="Customer ID for authorization")
    refund_p.add_argument("--seller-id", type=int, default=None, help="Seller ID for authorization")

    refund_hist_p = subparsers.add_parser("get_refund_history", help="Retrieve refund history")
    refund_hist_p.add_argument("--customer-id", type=int, default=None, help="Customer ID")
    refund_hist_p.add_argument("--seller-id", type=int, default=None, help="Seller ID")

    retry_ref_p = subparsers.add_parser("retry_refund", help="Retry a failed refund")
    retry_ref_p.add_argument("order_id", type=int, help="Order ID")
    retry_ref_p.add_argument("--customer-id", type=int, default=None, help="Customer ID")
    retry_ref_p.add_argument("--seller-id", type=int, default=None, help="Seller ID")
    retry_ref_p.add_argument("--fail", action="store_true", help="Simulate refund processing failure")

    return parser.parse_args(argv)


if __name__ == "__main__":
    args = _parse_args()

    try:
        with SwiggyClient(host=args.host, port=args.port) as client:
            if args.command == "ping":
                _print_response(client.ping())
            elif args.command == "list_restaurants":
                _print_response(client.list_restaurants())
            elif args.command == "list_menu":
                _print_response(client.list_menu(args.restaurant_id))
            elif args.command == "create_order":
                items = _parse_items_arg(args.items)
                _print_response(client.create_order(args.customer_id, args.restaurant_id, items))
            elif args.command == "simulate_payment":
                status = "failed" if args.fail else "paid"
                _print_response(client.simulate_payment(
                    args.order_id,
                    simulate_status=status,
                    payment_method=args.method,
                    simulate_failure=args.fail,
                    customer_id=args.customer_id,
                ))
            elif args.command == "get_order":
                _print_response(client.get_order(args.order_id))
            elif args.command == "get_order_history":
                _print_response(client.get_order_history(args.customer_id))
            elif args.command == "get_seller_profile":
                _print_response(client.get_seller_profile(args.seller_id))
            elif args.command == "get_seller_orders":
                _print_response(client.get_seller_orders(args.seller_id, status=args.status))
            elif args.command == "get_seller_order":
                _print_response(client.get_seller_order(args.seller_id, args.order_id))
            elif args.command == "update_order_status":
                _print_response(client.update_order_status(
                    args.seller_id,
                    args.order_id,
                    args.status,
                    reason=args.reason,
                    simulate_refund_failure=args.fail_refund,
                ))
            elif args.command == "get_seller_menu":
                _print_response(client.get_seller_menu(args.seller_id))
            elif args.command == "add_menu_item":
                _print_response(client.add_menu_item(
                    args.seller_id,
                    name=args.name,
                    price=args.price,
                    description=args.description,
                    is_available=args.available,
                ))
            elif args.command == "update_menu_item":
                fields = {}
                if args.name is not None:
                    fields["name"] = args.name
                if args.price is not None:
                    fields["price"] = args.price
                if args.description is not None:
                    fields["description"] = args.description
                if args.available is not None:
                    fields["is_available"] = args.available
                _print_response(client.update_menu_item(args.seller_id, args.item_id, **fields))
            elif args.command == "update_menu_item_availability":
                _print_response(client.update_menu_item_availability(args.seller_id, args.item_id, args.is_available))
            elif args.command == "get_refund":
                _print_response(client.get_refund(
                    args.order_id,
                    customer_id=args.customer_id,
                    seller_id=args.seller_id,
                ))
            elif args.command == "get_refund_history":
                _print_response(client.get_refund_history(
                    customer_id=args.customer_id,
                    seller_id=args.seller_id,
                ))
            elif args.command == "retry_refund":
                _print_response(client.retry_refund(
                    args.order_id,
                    customer_id=args.customer_id,
                    seller_id=args.seller_id,
                    simulate_failure=args.fail,
                ))
    except ConnectionRefusedError as exc:
        print(f"[client] ERROR: {exc}")
        sys.exit(1)
    except socket.timeout as exc:
        print(f"[client] TIMEOUT: {exc}")
        sys.exit(1)
    except Exception as exc:
        print(f"[client] Unexpected error: {exc}")
        sys.exit(1)

