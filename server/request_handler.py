"""
server/request_handler.py
==========================
Handles validated JSON requests and returns structured JSON responses.

This module is PURE APPLICATION LOGIC — it knows nothing about sockets
or networking. It receives a parsed Python dict and returns a Python dict.
Keeping it separate makes it easy to test without starting a real server.

Supported actions (V1):
  - ping               : health check
  - list_restaurants   : return all restaurants from the database
  - list_menu          : return menu items for a given restaurant_id

Supported actions (V2):
  - create_order       : validate cart, calculate price on server, save order atomically
  - simulate_payment   : simulate and record order payment, idempotent
  - get_order          : retrieve details and status of a specific order
  - get_order_history  : retrieve order history for a customer (predictable ordering)
"""

import sqlite3
import uuid
from database.db import get_connection


# -------------------------------------------------------------------
# Error codes — using string constants avoids typos
# -------------------------------------------------------------------
ERR_UNKNOWN_ACTION            = "UNKNOWN_ACTION"
ERR_INVALID_REQUEST           = "INVALID_REQUEST"
ERR_INVALID_PAYLOAD           = "INVALID_PAYLOAD"
ERR_NOT_FOUND                 = "NOT_FOUND"
ERR_ITEM_UNAVAILABLE          = "ITEM_UNAVAILABLE"
ERR_RESTAURANT_CLOSED         = "RESTAURANT_CLOSED"
ERR_PAYMENT_ALREADY_COMPLETED = "PAYMENT_ALREADY_COMPLETED"
ERR_DB_ERROR                  = "DB_ERROR"


# -------------------------------------------------------------------
# Response builders
# -------------------------------------------------------------------
def _ok(request_id: str, data: object) -> dict:
    """Build a successful response dict."""
    return {"request_id": request_id, "ok": True, "data": data}


def _err(request_id: str, code: str, message: str) -> dict:
    """Build an error response dict."""
    return {
        "request_id": request_id,
        "ok": False,
        "error": {"code": code, "message": message},
    }


# -------------------------------------------------------------------
# Action: ping (V1)
# -------------------------------------------------------------------
def _handle_ping(request_id: str, payload: dict) -> dict:
    """
    Simple health check.

    Request:  {"request_id": "r1", "action": "ping", "payload": {}}
    Response: {"request_id": "r1", "ok": true, "data": {"status": "ok"}}
    """
    return _ok(request_id, {"status": "ok"})


# -------------------------------------------------------------------
# Action: list_restaurants (V1)
# -------------------------------------------------------------------
def _handle_list_restaurants(request_id: str, payload: dict) -> dict:
    """
    Return all restaurants from the database.

    We read approval_status and is_open from the actual schema so the
    response reflects the real database state — nothing is hardcoded.
    """
    try:
        with get_connection() as conn:
            rows = conn.execute(
                """
                SELECT restaurant_id, name, description,
                       approval_status, is_open
                FROM restaurants
                ORDER BY restaurant_id;
                """
            ).fetchall()

        restaurants = [
            {
                "restaurant_id":   row["restaurant_id"],
                "name":            row["name"],
                "description":     row["description"],
                "approval_status": row["approval_status"],
                "is_open":         bool(row["is_open"]),
            }
            for row in rows
        ]
        return _ok(request_id, restaurants)

    except sqlite3.Error as exc:
        # Never expose raw DB exceptions to the client.
        return _err(request_id, ERR_DB_ERROR, "Database error while listing restaurants.")


# -------------------------------------------------------------------
# Action: list_menu (V1)
# -------------------------------------------------------------------
def _handle_list_menu(request_id: str, payload: dict) -> dict:
    """
    Return menu items for one restaurant.

    The payload must contain:
      restaurant_id (int) — the restaurant to query.

    Distinguishing cases:
      - restaurant_id is not an int           → INVALID_PAYLOAD
      - restaurant_id refers to no restaurant → NOT_FOUND
      - restaurant exists but has no items    → ok, empty list
    """
    # --- Validate payload ---
    if "restaurant_id" not in payload:
        return _err(request_id, ERR_INVALID_PAYLOAD,
                    "payload must contain 'restaurant_id'.")

    rid = payload["restaurant_id"]
    if not isinstance(rid, int) or isinstance(rid, bool):
        return _err(request_id, ERR_INVALID_PAYLOAD,
                    "'restaurant_id' must be an integer.")

    try:
        with get_connection() as conn:
            # First check that the restaurant itself exists.
            restaurant = conn.execute(
                "SELECT restaurant_id FROM restaurants WHERE restaurant_id = ?;",
                (rid,),
            ).fetchone()

            if restaurant is None:
                return _err(request_id, ERR_NOT_FOUND,
                            f"No restaurant found with id {rid}.")

            # Restaurant exists — fetch its menu items.
            rows = conn.execute(
                """
                SELECT item_id, restaurant_id, name, description,
                       price, is_available
                FROM menu_items
                WHERE restaurant_id = ?
                ORDER BY item_id;
                """,
                (rid,),
            ).fetchall()

        items = [
            {
                "item_id":       row["item_id"],
                "restaurant_id": row["restaurant_id"],
                "name":          row["name"],
                "description":   row["description"],
                "price":         row["price"],
                "is_available":  bool(row["is_available"]),
            }
            for row in rows
        ]
        return _ok(request_id, items)

    except sqlite3.Error as exc:
        return _err(request_id, ERR_DB_ERROR, "Database error while listing menu.")


# -------------------------------------------------------------------
# Action: create_order (V2)
# -------------------------------------------------------------------
def _handle_create_order(request_id: str, payload: dict) -> dict:
    """
    Create a new food order.

    Validation rules:
      1. customer_id (int) must be provided, must exist in users, and must be active.
      2. restaurant_id (int) must be provided, must exist in restaurants, and must be open.
      3. items (list of dicts) must be non-empty.
      4. Each item must have:
         - item_id (int): must exist in menu_items, belong to restaurant_id, and be available.
         - quantity (int): must be an integer > 0.
      5. Server-calculated total:
         - Prices are ALWAYS retrieved from the SQLite menu_items table. Any 'price' field sent by
           the client is completely ignored.
         - Each line item stores a unit_price snapshot at the time of order creation.
      6. Atomicity:
         - Order and order_items are saved inside a single database transaction. If any part
           fails, the transaction is rolled back so no partial order is saved.
    """
    if "customer_id" not in payload:
        return _err(request_id, ERR_INVALID_PAYLOAD, "payload must contain 'customer_id'.")
    customer_id = payload["customer_id"]
    if not isinstance(customer_id, int) or isinstance(customer_id, bool):
        return _err(request_id, ERR_INVALID_PAYLOAD, "'customer_id' must be an integer.")

    if "restaurant_id" not in payload:
        return _err(request_id, ERR_INVALID_PAYLOAD, "payload must contain 'restaurant_id'.")
    restaurant_id = payload["restaurant_id"]
    if not isinstance(restaurant_id, int) or isinstance(restaurant_id, bool):
        return _err(request_id, ERR_INVALID_PAYLOAD, "'restaurant_id' must be an integer.")

    if "items" not in payload:
        return _err(request_id, ERR_INVALID_PAYLOAD, "payload must contain 'items'.")
    items_raw = payload["items"]
    if not isinstance(items_raw, list):
        return _err(request_id, ERR_INVALID_PAYLOAD, "'items' must be a list of order items.")
    if len(items_raw) == 0:
        return _err(request_id, ERR_INVALID_PAYLOAD, "Order cart cannot be empty. Must include at least one item.")

    # Validate individual item formats and consolidate duplicate item_ids
    consolidated: dict[int, int] = {}
    for idx, item in enumerate(items_raw):
        if not isinstance(item, dict):
            return _err(request_id, ERR_INVALID_PAYLOAD, f"Item at index {idx} must be a JSON object.")
        if "item_id" not in item:
            return _err(request_id, ERR_INVALID_PAYLOAD, f"Item at index {idx} is missing 'item_id'.")
        item_id = item["item_id"]
        if not isinstance(item_id, int) or isinstance(item_id, bool):
            return _err(request_id, ERR_INVALID_PAYLOAD, f"'item_id' at index {idx} must be an integer.")

        if "quantity" not in item:
            return _err(request_id, ERR_INVALID_PAYLOAD, f"Item at index {idx} is missing 'quantity'.")
        qty = item["quantity"]
        if not isinstance(qty, int) or isinstance(qty, bool) or qty <= 0:
            return _err(request_id, ERR_INVALID_PAYLOAD, f"Quantity for item {item_id} must be a positive integer.")

        consolidated[item_id] = consolidated.get(item_id, 0) + qty

    try:
        with get_connection() as conn:
            # 1. Verify customer exists and is active
            cust = conn.execute(
                "SELECT user_id, name, is_active FROM users WHERE user_id = ?;",
                (customer_id,),
            ).fetchone()
            if cust is None:
                return _err(request_id, ERR_NOT_FOUND, f"No customer found with id {customer_id}.")
            if not cust["is_active"]:
                return _err(request_id, ERR_INVALID_REQUEST, f"Customer account {customer_id} is inactive.")

            # 2. Verify restaurant exists and is open
            rest = conn.execute(
                "SELECT restaurant_id, name, is_open FROM restaurants WHERE restaurant_id = ?;",
                (restaurant_id,),
            ).fetchone()
            if rest is None:
                return _err(request_id, ERR_NOT_FOUND, f"No restaurant found with id {restaurant_id}.")
            if not rest["is_open"]:
                return _err(request_id, ERR_RESTAURANT_CLOSED, f"Restaurant '{rest['name']}' is currently closed.")

            # 3. Verify menu items exist, belong to this restaurant, and are available
            validated_items = []
            total_amount = 0.0

            for item_id, quantity in consolidated.items():
                menu_row = conn.execute(
                    """
                    SELECT item_id, restaurant_id, name, price, is_available
                    FROM menu_items
                    WHERE item_id = ?;
                    """,
                    (item_id,),
                ).fetchone()

                if menu_row is None:
                    return _err(request_id, ERR_NOT_FOUND, f"Menu item {item_id} not found.")

                if menu_row["restaurant_id"] != restaurant_id:
                    return _err(
                        request_id,
                        ERR_INVALID_REQUEST,
                        f"Menu item {item_id} ('{menu_row['name']}') belongs to restaurant {menu_row['restaurant_id']}, not restaurant {restaurant_id}.",
                    )

                if not menu_row["is_available"]:
                    return _err(
                        request_id,
                        ERR_ITEM_UNAVAILABLE,
                        f"Menu item {item_id} ('{menu_row['name']}') is currently unavailable.",
                    )

                # Snapshot the official database price (ignore any client price)
                unit_price = float(menu_row["price"])
                subtotal = round(unit_price * quantity, 2)
                total_amount += subtotal

                validated_items.append({
                    "item_id": item_id,
                    "name": menu_row["name"],
                    "quantity": quantity,
                    "unit_price": unit_price,
                    "subtotal": subtotal,
                })

            total_amount = round(total_amount, 2)

            # 4. Atomic insert of order and order items inside a transaction
            cursor = conn.execute(
                """
                INSERT INTO orders (customer_id, restaurant_id, total_amount, order_status, payment_status)
                VALUES (?, ?, ?, 'placed', 'pending');
                """,
                (customer_id, restaurant_id, total_amount),
            )
            order_id = cursor.lastrowid

            for it in validated_items:
                conn.execute(
                    """
                    INSERT INTO order_items (order_id, item_id, quantity, unit_price)
                    VALUES (?, ?, ?, ?);
                    """,
                    (order_id, it["item_id"], it["quantity"], it["unit_price"]),
                )

        return _ok(request_id, {
            "order_id": order_id,
            "customer_id": customer_id,
            "restaurant_id": restaurant_id,
            "restaurant_name": rest["name"],
            "total_amount": total_amount,
            "order_status": "placed",
            "payment_status": "pending",
            "items": validated_items,
        })

    except sqlite3.Error as exc:
        return _err(request_id, ERR_DB_ERROR, f"Database error while creating order: {exc}")


# -------------------------------------------------------------------
# Action: simulate_payment (V2)
# -------------------------------------------------------------------
def _handle_simulate_payment(request_id: str, payload: dict) -> dict:
    """
    Simulate a payment for an existing order.

    Rules:
      1. order_id must be provided and must exist in orders.
      2. If the order is already marked 'paid', reject to prevent duplicate charges.
      3. If the order is 'cancelled', reject payment.
      4. Support simulating payment failure via simulate_status='failed' or simulate_failure=True.
         On failure:
           - Record payment_status='failed' in payments and orders.
           - Ensure order is NOT marked as 'paid' (order_status remains 'placed').
      5. On success:
           - Record payment_status='paid' in payments and orders.
           - Update order_status='accepted'.
      6. Payment records are persisted in the payments table with transaction_ref.
    """
    if "order_id" not in payload:
        return _err(request_id, ERR_INVALID_PAYLOAD, "payload must contain 'order_id'.")
    order_id = payload["order_id"]
    if not isinstance(order_id, int) or isinstance(order_id, bool):
        return _err(request_id, ERR_INVALID_PAYLOAD, "'order_id' must be an integer.")

    try:
        with get_connection() as conn:
            order = conn.execute(
                """
                SELECT order_id, customer_id, restaurant_id, total_amount,
                       order_status, payment_status
                FROM orders
                WHERE order_id = ?;
                """,
                (order_id,),
            ).fetchone()

            if order is None:
                return _err(request_id, ERR_NOT_FOUND, f"No order found with id {order_id}.")

            if order["payment_status"] == "paid":
                return _err(
                    request_id,
                    ERR_PAYMENT_ALREADY_COMPLETED,
                    f"Order {order_id} has already been paid. Duplicate payment is not allowed.",
                )

            if order["order_status"] == "cancelled":
                return _err(
                    request_id,
                    ERR_INVALID_REQUEST,
                    f"Cannot process payment for cancelled order {order_id}.",
                )

            sim_status = str(payload.get("simulate_status", "")).lower()
            sim_failure = payload.get("simulate_failure", False)
            is_failure = (sim_failure is True) or (sim_status in ("failed", "fail"))

            payment_method = str(payload.get("payment_method", "simulated_upi"))
            amount = float(order["total_amount"])
            txn_ref = f"TXN-SIM-{order_id}-{uuid.uuid4().hex[:8].upper()}"

            if is_failure:
                target_payment_status = "failed"
                target_order_status = order["order_status"]
            else:
                target_payment_status = "paid"
                target_order_status = "accepted"

            # Persist in payments table (upsert handles retry after previous failed attempt)
            conn.execute(
                """
                INSERT INTO payments (order_id, amount, payment_status, transaction_ref)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(order_id) DO UPDATE SET
                    amount = excluded.amount,
                    payment_status = excluded.payment_status,
                    transaction_ref = excluded.transaction_ref;
                """,
                (order_id, amount, target_payment_status, txn_ref),
            )

            # Update orders table
            conn.execute(
                """
                UPDATE orders
                SET payment_status = ?, order_status = ?
                WHERE order_id = ?;
                """,
                (target_payment_status, target_order_status, order_id),
            )

            payment_row = conn.execute(
                "SELECT payment_id FROM payments WHERE order_id = ?;",
                (order_id,),
            ).fetchone()
            payment_id = payment_row["payment_id"]

        return _ok(request_id, {
            "payment_id": payment_id,
            "order_id": order_id,
            "amount": amount,
            "payment_status": target_payment_status,
            "order_status": target_order_status,
            "transaction_ref": txn_ref,
            "payment_method": payment_method,
        })

    except sqlite3.Error as exc:
        return _err(request_id, ERR_DB_ERROR, f"Database error while processing payment: {exc}")


# -------------------------------------------------------------------
# Action: get_order (V2)
# -------------------------------------------------------------------
def _handle_get_order(request_id: str, payload: dict) -> dict:
    """
    Retrieve details, item snapshots, and payment info of a single order.
    """
    if "order_id" not in payload:
        return _err(request_id, ERR_INVALID_PAYLOAD, "payload must contain 'order_id'.")
    order_id = payload["order_id"]
    if not isinstance(order_id, int) or isinstance(order_id, bool):
        return _err(request_id, ERR_INVALID_PAYLOAD, "'order_id' must be an integer.")

    try:
        with get_connection() as conn:
            order = conn.execute(
                """
                SELECT o.order_id, o.customer_id, u.name AS customer_name, u.email AS customer_email,
                       o.restaurant_id, r.name AS restaurant_name,
                       o.total_amount, o.order_status, o.payment_status, o.created_at
                FROM orders o
                JOIN users u ON o.customer_id = u.user_id
                JOIN restaurants r ON o.restaurant_id = r.restaurant_id
                WHERE o.order_id = ?;
                """,
                (order_id,),
            ).fetchone()

            if order is None:
                return _err(request_id, ERR_NOT_FOUND, f"No order found with id {order_id}.")

            item_rows = conn.execute(
                """
                SELECT oi.order_item_id, oi.item_id, mi.name, oi.quantity, oi.unit_price,
                       ROUND(oi.quantity * oi.unit_price, 2) AS subtotal
                FROM order_items oi
                JOIN menu_items mi ON oi.item_id = mi.item_id
                WHERE oi.order_id = ?
                ORDER BY oi.order_item_id;
                """,
                (order_id,),
            ).fetchall()

            items = [
                {
                    "order_item_id": row["order_item_id"],
                    "item_id": row["item_id"],
                    "name": row["name"],
                    "quantity": row["quantity"],
                    "unit_price": row["unit_price"],
                    "subtotal": row["subtotal"],
                }
                for row in item_rows
            ]

            payment_row = conn.execute(
                """
                SELECT payment_id, amount, payment_status, transaction_ref, created_at
                FROM payments
                WHERE order_id = ?;
                """,
                (order_id,),
            ).fetchone()

            payment = None
            if payment_row is not None:
                payment = {
                    "payment_id": payment_row["payment_id"],
                    "amount": payment_row["amount"],
                    "payment_status": payment_row["payment_status"],
                    "transaction_ref": payment_row["transaction_ref"],
                    "created_at": payment_row["created_at"],
                }

        return _ok(request_id, {
            "order_id": order["order_id"],
            "customer_id": order["customer_id"],
            "customer_name": order["customer_name"],
            "customer_email": order["customer_email"],
            "restaurant_id": order["restaurant_id"],
            "restaurant_name": order["restaurant_name"],
            "total_amount": float(order["total_amount"]),
            "order_status": order["order_status"],
            "payment_status": order["payment_status"],
            "created_at": order["created_at"],
            "items": items,
            "payment": payment,
        })

    except sqlite3.Error as exc:
        return _err(request_id, ERR_DB_ERROR, f"Database error while retrieving order: {exc}")


# -------------------------------------------------------------------
# Action: get_order_history (V2)
# -------------------------------------------------------------------
def _handle_get_order_history(request_id: str, payload: dict) -> dict:
    """
    Retrieve order history for a customer with predictable ordering (newest first).
    """
    if "customer_id" not in payload:
        return _err(request_id, ERR_INVALID_PAYLOAD, "payload must contain 'customer_id'.")
    customer_id = payload["customer_id"]
    if not isinstance(customer_id, int) or isinstance(customer_id, bool):
        return _err(request_id, ERR_INVALID_PAYLOAD, "'customer_id' must be an integer.")

    try:
        with get_connection() as conn:
            user = conn.execute(
                "SELECT user_id, name, email FROM users WHERE user_id = ?;",
                (customer_id,),
            ).fetchone()

            if user is None:
                return _err(request_id, ERR_NOT_FOUND, f"No customer found with id {customer_id}.")

            order_rows = conn.execute(
                """
                SELECT o.order_id, o.customer_id, o.restaurant_id, r.name AS restaurant_name,
                       o.total_amount, o.order_status, o.payment_status, o.created_at
                FROM orders o
                JOIN restaurants r ON o.restaurant_id = r.restaurant_id
                WHERE o.customer_id = ?
                ORDER BY o.order_id DESC;
                """,
                (customer_id,),
            ).fetchall()

            orders = []
            for ord_row in order_rows:
                oid = ord_row["order_id"]
                item_rows = conn.execute(
                    """
                    SELECT oi.order_item_id, oi.item_id, mi.name, oi.quantity, oi.unit_price,
                           ROUND(oi.quantity * oi.unit_price, 2) AS subtotal
                    FROM order_items oi
                    JOIN menu_items mi ON oi.item_id = mi.item_id
                    WHERE oi.order_id = ?
                    ORDER BY oi.order_item_id;
                    """,
                    (oid,),
                ).fetchall()

                orders.append({
                    "order_id": ord_row["order_id"],
                    "customer_id": ord_row["customer_id"],
                    "restaurant_id": ord_row["restaurant_id"],
                    "restaurant_name": ord_row["restaurant_name"],
                    "total_amount": float(ord_row["total_amount"]),
                    "order_status": ord_row["order_status"],
                    "payment_status": ord_row["payment_status"],
                    "created_at": ord_row["created_at"],
                    "items": [
                        {
                            "item_id": it["item_id"],
                            "name": it["name"],
                            "quantity": it["quantity"],
                            "unit_price": it["unit_price"],
                            "subtotal": it["subtotal"],
                        }
                        for it in item_rows
                    ],
                })

        return _ok(request_id, orders)

    except sqlite3.Error as exc:
        return _err(request_id, ERR_DB_ERROR, f"Database error while retrieving order history: {exc}")


# -------------------------------------------------------------------
# Dispatch table — maps action name → handler function
# -------------------------------------------------------------------
_HANDLERS = {
    "ping":              _handle_ping,
    "list_restaurants":  _handle_list_restaurants,
    "list_menu":         _handle_list_menu,
    "create_order":      _handle_create_order,
    "simulate_payment":  _handle_simulate_payment,
    "get_order":         _handle_get_order,
    "get_order_history": _handle_get_order_history,
}


# -------------------------------------------------------------------
# Public entry point — called by tcp_server.py
# -------------------------------------------------------------------
def handle_request(raw: dict) -> dict:
    """
    Validate a parsed JSON request dict and dispatch to the right handler.

    Validation performed here:
      1. raw must be a dict (JSON object).
      2. 'action' field must be present and a string.
      3. 'request_id' should be present; we fall back to 'unknown' if missing.
      4. 'payload' must be present and a dict; defaults to {} if absent.

    Args:
        raw: The already-parsed Python dict from the client JSON.

    Returns:
        A response dict (to be JSON-serialised and sent back).
    """
    # Safety: should already be a dict, but guard anyway.
    if not isinstance(raw, dict):
        return _err("unknown", ERR_INVALID_REQUEST, "Request must be a JSON object.")

    # Extract request_id first so it can be echoed in error responses.
    request_id = raw.get("request_id", "unknown")
    if not isinstance(request_id, str):
        request_id = "unknown"

    # Validate 'action'.
    action = raw.get("action")
    if not action or not isinstance(action, str):
        return _err(request_id, ERR_INVALID_REQUEST,
                    "'action' field is required and must be a string.")

    # Validate 'payload'.
    payload = raw.get("payload", {})
    if not isinstance(payload, dict):
        return _err(request_id, ERR_INVALID_PAYLOAD,
                    "'payload' must be a JSON object.")

    # Dispatch.
    handler = _HANDLERS.get(action)
    if handler is None:
        return _err(request_id, ERR_UNKNOWN_ACTION,
                    f"Unknown action '{action}'. "
                    f"Supported: {sorted(_HANDLERS.keys())}.")

    return handler(request_id, payload)

