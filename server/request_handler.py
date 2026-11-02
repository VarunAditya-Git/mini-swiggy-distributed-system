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
from server.auth import (
    hash_password,
    verify_password,
    validate_password,
    validate_email,
    generate_token,
    hash_token,
)
from server.email_adapter import default_email_adapter


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
ERR_UNAUTHORIZED_ROLE         = "UNAUTHORIZED_ROLE"
ERR_FORBIDDEN                 = "FORBIDDEN"
ERR_INVALID_TRANSITION        = "INVALID_TRANSITION"
ERR_REFUND_NOT_FOUND          = "REFUND_NOT_FOUND"
ERR_REFUND_ALREADY_PROCESSED  = "REFUND_ALREADY_PROCESSED"
ERR_REFUND_NOT_RETRYABLE      = "REFUND_NOT_RETRYABLE"
ERR_DB_ERROR                  = "DB_ERROR"

# V4.1 Auth error codes
ERR_INVALID_CREDENTIALS       = "INVALID_CREDENTIALS"
ERR_EMAIL_ALREADY_EXISTS      = "EMAIL_ALREADY_EXISTS"
ERR_EMAIL_NOT_VERIFIED        = "EMAIL_NOT_VERIFIED"
ERR_ACCOUNT_DEACTIVATED       = "ACCOUNT_DEACTIVATED"
ERR_INVALID_TOKEN             = "INVALID_TOKEN"
ERR_TOKEN_ALREADY_USED        = "TOKEN_ALREADY_USED"
ERR_TOKEN_EXPIRED             = "TOKEN_EXPIRED"
ERR_UNAUTHORIZED              = "UNAUTHORIZED"


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

            if order["order_status"] in ("rejected", "cancelled"):
                return _err(
                    request_id,
                    ERR_INVALID_REQUEST,
                    f"Cannot process payment for {order['order_status']} order {order_id}.",
                )

            if "customer_id" in payload and payload["customer_id"] is not None:
                cust_id = payload["customer_id"]
                if not isinstance(cust_id, int) or isinstance(cust_id, bool):
                    return _err(request_id, ERR_INVALID_PAYLOAD, "'customer_id' must be an integer.")
                if order["customer_id"] != cust_id:
                    return _err(
                        request_id,
                        ERR_FORBIDDEN,
                        f"Access denied: order {order_id} does not belong to customer {cust_id}.",
                    )

            sim_status = str(payload.get("simulate_status", "")).lower()
            sim_failure = payload.get("simulate_failure", False)
            is_failure = (sim_failure is True) or (sim_status in ("failed", "fail"))

            payment_method = str(payload.get("payment_method", "simulated_upi"))
            amount = float(order["total_amount"])
            txn_ref = f"TXN-SIM-{order_id}-{uuid.uuid4().hex[:8].upper()}"

            cur_order_status = order["order_status"]

            if is_failure:
                target_payment_status = "failed"
                target_order_status = cur_order_status
            else:
                target_payment_status = "paid"
                # If order was placed/pending, successful payment advances to accepted.
                # If already accepted, preparing, or ready_for_pickup, preserve current status.
                if cur_order_status in ("placed", "pending"):
                    target_order_status = "accepted"
                else:
                    target_order_status = cur_order_status

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

            refund_row = conn.execute(
                """
                SELECT refund_id, order_id, amount, refund_status, reason,
                       transaction_ref, attempt_count, created_at, updated_at
                FROM refunds
                WHERE order_id = ?;
                """,
                (order_id,),
            ).fetchone()

            refund = None
            if refund_row is not None:
                refund = {
                    "refund_id": refund_row["refund_id"],
                    "order_id": refund_row["order_id"],
                    "amount": float(refund_row["amount"]),
                    "refund_status": refund_row["refund_status"],
                    "reason": refund_row["reason"],
                    "transaction_ref": refund_row["transaction_ref"],
                    "attempt_count": refund_row["attempt_count"],
                    "created_at": refund_row["created_at"],
                    "updated_at": refund_row["updated_at"],
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
            "refund": refund,
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

                refund_row = conn.execute(
                    """
                    SELECT refund_id, order_id, amount, refund_status, reason,
                           transaction_ref, attempt_count, created_at, updated_at
                    FROM refunds
                    WHERE order_id = ?;
                    """,
                    (oid,),
                ).fetchone()

                refund = None
                if refund_row is not None:
                    refund = {
                        "refund_id": refund_row["refund_id"],
                        "order_id": refund_row["order_id"],
                        "amount": float(refund_row["amount"]),
                        "refund_status": refund_row["refund_status"],
                        "reason": refund_row["reason"],
                        "transaction_ref": refund_row["transaction_ref"],
                        "attempt_count": refund_row["attempt_count"],
                        "created_at": refund_row["created_at"],
                        "updated_at": refund_row["updated_at"],
                    }

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
                    "refund": refund,
                })

        return _ok(request_id, orders)

    except sqlite3.Error as exc:
        return _err(request_id, ERR_DB_ERROR, f"Database error while retrieving order history: {exc}")


# -------------------------------------------------------------------
# Helper: Seller Authentication & Restaurant Verification (V3)
# -------------------------------------------------------------------
def _authenticate_seller(
    conn: sqlite3.Connection,
    request_id: str,
    seller_id: object,
) -> tuple[dict | None, dict | None, dict | None]:
    """
    Verify seller development identity and restaurant ownership.

    Returns:
        (restaurant_dict, user_dict, None) on success, or
        (None, None, error_response_dict) on failure.
    """
    if seller_id is None:
        return None, None, _err(request_id, ERR_INVALID_PAYLOAD, "payload must contain 'seller_id'.")
    if not isinstance(seller_id, int) or isinstance(seller_id, bool):
        return None, None, _err(request_id, ERR_INVALID_PAYLOAD, "'seller_id' must be an integer.")

    user = conn.execute(
        "SELECT user_id, name, email, role, is_active FROM users WHERE user_id = ?;",
        (seller_id,),
    ).fetchone()

    if user is None:
        return None, None, _err(request_id, ERR_NOT_FOUND, f"No user found with id {seller_id}.")

    if user["role"] != "seller":
        return None, None, _err(
            request_id,
            ERR_UNAUTHORIZED_ROLE,
            f"User {seller_id} is not a seller (role='{user['role']}').",
        )

    if not user["is_active"]:
        return None, None, _err(request_id, ERR_FORBIDDEN, f"Seller account {seller_id} is deactivated.")

    restaurant = conn.execute(
        """
        SELECT restaurant_id, name, description, approval_status, is_open, owner_user_id
        FROM restaurants
        WHERE owner_user_id = ?;
        """,
        (seller_id,),
    ).fetchone()

    if restaurant is None:
        return None, None, _err(
            request_id,
            ERR_FORBIDDEN,
            f"Seller {seller_id} does not own any restaurant.",
        )

    return dict(restaurant), dict(user), None


# -------------------------------------------------------------------
# Action: get_seller_profile / get_seller_restaurant (V3)
# -------------------------------------------------------------------
def _handle_get_seller_profile(request_id: str, payload: dict) -> dict:
    """
    Return seller identity, associated restaurant, and dashboard statistics.
    """
    seller_id = payload.get("seller_id")
    try:
        with get_connection() as conn:
            restaurant, user, err = _authenticate_seller(conn, request_id, seller_id)
            if err:
                return err

            rid = restaurant["restaurant_id"]

            total_orders = conn.execute(
                "SELECT COUNT(*) AS c FROM orders WHERE restaurant_id = ?;",
                (rid,),
            ).fetchone()["c"]

            pending_orders = conn.execute(
                "SELECT COUNT(*) AS c FROM orders WHERE restaurant_id = ? AND order_status IN ('placed', 'pending');",
                (rid,),
            ).fetchone()["c"]

            accepted_orders = conn.execute(
                "SELECT COUNT(*) AS c FROM orders WHERE restaurant_id = ? AND order_status = 'accepted';",
                (rid,),
            ).fetchone()["c"]

            preparing_orders = conn.execute(
                "SELECT COUNT(*) AS c FROM orders WHERE restaurant_id = ? AND order_status = 'preparing';",
                (rid,),
            ).fetchone()["c"]

            ready_orders = conn.execute(
                "SELECT COUNT(*) AS c FROM orders WHERE restaurant_id = ? AND order_status IN ('ready', 'ready_for_pickup');",
                (rid,),
            ).fetchone()["c"]

            rejected_orders = conn.execute(
                "SELECT COUNT(*) AS c FROM orders WHERE restaurant_id = ? AND order_status IN ('rejected', 'cancelled');",
                (rid,),
            ).fetchone()["c"]

            menu_items_count = conn.execute(
                "SELECT COUNT(*) AS c FROM menu_items WHERE restaurant_id = ?;",
                (rid,),
            ).fetchone()["c"]

        return _ok(request_id, {
            "seller": {
                "user_id": user["user_id"],
                "name": user["name"],
                "email": user["email"],
                "role": user["role"],
            },
            "restaurant": {
                "restaurant_id": restaurant["restaurant_id"],
                "name": restaurant["name"],
                "description": restaurant["description"],
                "approval_status": restaurant["approval_status"],
                "is_open": bool(restaurant["is_open"]),
            },
            "stats": {
                "total_orders": total_orders,
                "pending_orders": pending_orders,
                "accepted_orders": accepted_orders,
                "preparing_orders": preparing_orders,
                "ready_orders": ready_orders,
                "rejected_orders": rejected_orders,
                "menu_items_count": menu_items_count,
            },
        })

    except sqlite3.Error as exc:
        return _err(request_id, ERR_DB_ERROR, f"Database error fetching seller profile: {exc}")


# -------------------------------------------------------------------
# Action: get_seller_orders (V3)
# -------------------------------------------------------------------
def _handle_get_seller_orders(request_id: str, payload: dict) -> dict:
    """
    Return orders belonging exclusively to the seller's restaurant, with optional filter.
    """
    seller_id = payload.get("seller_id")
    status_filter = payload.get("status")

    try:
        with get_connection() as conn:
            restaurant, _, err = _authenticate_seller(conn, request_id, seller_id)
            if err:
                return err

            rid = restaurant["restaurant_id"]

            query = """
                SELECT o.order_id, o.customer_id, u.name AS customer_name,
                       o.restaurant_id, r.name AS restaurant_name,
                       o.total_amount, o.order_status, o.payment_status, o.created_at
                FROM orders o
                JOIN users u ON o.customer_id = u.user_id
                JOIN restaurants r ON o.restaurant_id = r.restaurant_id
                WHERE o.restaurant_id = ?
            """
            params: list[object] = [rid]

            if status_filter and isinstance(status_filter, str):
                s = status_filter.strip().lower()
                if s == "pending":
                    query += " AND o.order_status IN ('placed', 'pending')"
                elif s == "accepted":
                    query += " AND o.order_status = 'accepted'"
                elif s == "preparing":
                    query += " AND o.order_status = 'preparing'"
                elif s in ("ready", "ready_for_pickup"):
                    query += " AND o.order_status IN ('ready', 'ready_for_pickup')"
                elif s in ("rejected", "cancelled"):
                    query += " AND o.order_status IN ('rejected', 'cancelled')"
                elif s != "all":
                    query += " AND o.order_status = ?"
                    params.append(s)

            query += " ORDER BY o.order_id DESC;"

            order_rows = conn.execute(query, tuple(params)).fetchall()

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

                payment_row = conn.execute(
                    """
                    SELECT payment_id, payment_status, transaction_ref, amount, created_at
                    FROM payments
                    WHERE order_id = ?;
                    """,
                    (oid,),
                ).fetchone()

                payment_info = None
                if payment_row:
                    payment_info = {
                        "payment_id": payment_row["payment_id"],
                        "payment_status": payment_row["payment_status"],
                        "transaction_ref": payment_row["transaction_ref"],
                        "amount": float(payment_row["amount"]),
                        "created_at": payment_row["created_at"],
                    }

                refund_row = conn.execute(
                    """
                    SELECT refund_id, order_id, amount, refund_status, reason,
                           transaction_ref, attempt_count, created_at, updated_at
                    FROM refunds
                    WHERE order_id = ?;
                    """,
                    (oid,),
                ).fetchone()

                refund_info = None
                if refund_row:
                    refund_info = {
                        "refund_id": refund_row["refund_id"],
                        "order_id": refund_row["order_id"],
                        "amount": float(refund_row["amount"]),
                        "refund_status": refund_row["refund_status"],
                        "reason": refund_row["reason"],
                        "transaction_ref": refund_row["transaction_ref"],
                        "attempt_count": refund_row["attempt_count"],
                        "created_at": refund_row["created_at"],
                        "updated_at": refund_row["updated_at"],
                    }

                orders.append({
                    "order_id": ord_row["order_id"],
                    "customer_id": ord_row["customer_id"],
                    "customer_name": ord_row["customer_name"],
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
                    "payment": payment_info,
                    "refund": refund_info,
                })

        return _ok(request_id, orders)

    except sqlite3.Error as exc:
        return _err(request_id, ERR_DB_ERROR, f"Database error fetching seller orders: {exc}")


# -------------------------------------------------------------------
# Action: get_seller_order (V3)
# -------------------------------------------------------------------
def _handle_get_seller_order(request_id: str, payload: dict) -> dict:
    """
    Retrieve single order detail with seller ownership authorization.
    """
    seller_id = payload.get("seller_id")
    order_id = payload.get("order_id")

    if order_id is None:
        return _err(request_id, ERR_INVALID_PAYLOAD, "payload must contain 'order_id'.")
    if not isinstance(order_id, int) or isinstance(order_id, bool):
        return _err(request_id, ERR_INVALID_PAYLOAD, "'order_id' must be an integer.")

    try:
        with get_connection() as conn:
            restaurant, _, err = _authenticate_seller(conn, request_id, seller_id)
            if err:
                return err

            ord_row = conn.execute(
                """
                SELECT o.order_id, o.customer_id, u.name AS customer_name,
                       o.restaurant_id, r.name AS restaurant_name,
                       o.total_amount, o.order_status, o.payment_status, o.created_at
                FROM orders o
                JOIN users u ON o.customer_id = u.user_id
                JOIN restaurants r ON o.restaurant_id = r.restaurant_id
                WHERE o.order_id = ?;
                """,
                (order_id,),
            ).fetchone()

            if ord_row is None:
                return _err(request_id, ERR_NOT_FOUND, f"No order found with id {order_id}.")

            if ord_row["restaurant_id"] != restaurant["restaurant_id"]:
                return _err(
                    request_id,
                    ERR_FORBIDDEN,
                    f"Access denied: order {order_id} does not belong to your restaurant.",
                )

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

            payment_row = conn.execute(
                """
                SELECT payment_id, payment_status, transaction_ref, amount, created_at
                FROM payments
                WHERE order_id = ?;
                """,
                (order_id,),
            ).fetchone()

            payment_info = None
            if payment_row:
                payment_info = {
                    "payment_id": payment_row["payment_id"],
                    "payment_status": payment_row["payment_status"],
                    "transaction_ref": payment_row["transaction_ref"],
                    "amount": float(payment_row["amount"]),
                    "created_at": payment_row["created_at"],
                }

            refund_row = conn.execute(
                """
                SELECT refund_id, order_id, amount, refund_status, reason,
                       transaction_ref, attempt_count, created_at, updated_at
                FROM refunds
                WHERE order_id = ?;
                """,
                (order_id,),
            ).fetchone()

            refund_info = None
            if refund_row:
                refund_info = {
                    "refund_id": refund_row["refund_id"],
                    "order_id": refund_row["order_id"],
                    "amount": float(refund_row["amount"]),
                    "refund_status": refund_row["refund_status"],
                    "reason": refund_row["reason"],
                    "transaction_ref": refund_row["transaction_ref"],
                    "attempt_count": refund_row["attempt_count"],
                    "created_at": refund_row["created_at"],
                    "updated_at": refund_row["updated_at"],
                }

        return _ok(request_id, {
            "order_id": ord_row["order_id"],
            "customer_id": ord_row["customer_id"],
            "customer_name": ord_row["customer_name"],
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
            "payment": payment_info,
            "refund": refund_info,
        })

    except sqlite3.Error as exc:
        return _err(request_id, ERR_DB_ERROR, f"Database error fetching seller order: {exc}")


# -------------------------------------------------------------------
# Action: update_order_status (V3)
# -------------------------------------------------------------------
def _handle_update_order_status(request_id: str, payload: dict) -> dict:
    """
    Validate status transition and update order status for seller's restaurant.
    """
    seller_id = payload.get("seller_id")
    order_id = payload.get("order_id")
    raw_status = payload.get("status")

    if order_id is None:
        return _err(request_id, ERR_INVALID_PAYLOAD, "payload must contain 'order_id'.")
    if not isinstance(order_id, int) or isinstance(order_id, bool):
        return _err(request_id, ERR_INVALID_PAYLOAD, "'order_id' must be an integer.")

    if not raw_status or not isinstance(raw_status, str):
        return _err(request_id, ERR_INVALID_PAYLOAD, "'status' must be a non-empty string.")

    target_status = raw_status.strip().lower()

    # Normalise aliases
    if target_status == "ready":
        target_status = "ready_for_pickup"
    elif target_status == "cancel":
        target_status = "cancelled"

    allowed_targets = {"accepted", "preparing", "ready_for_pickup", "rejected", "cancelled"}
    if target_status not in allowed_targets:
        return _err(
            request_id,
            ERR_INVALID_PAYLOAD,
            f"Invalid target status '{raw_status}'. Allowed: {sorted(allowed_targets)}.",
        )

    try:
        with get_connection() as conn:
            restaurant, _, err = _authenticate_seller(conn, request_id, seller_id)
            if err:
                return err

            ord_row = conn.execute(
                "SELECT order_id, restaurant_id, order_status, payment_status FROM orders WHERE order_id = ?;",
                (order_id,),
            ).fetchone()

            if ord_row is None:
                return _err(request_id, ERR_NOT_FOUND, f"No order found with id {order_id}.")

            if ord_row["restaurant_id"] != restaurant["restaurant_id"]:
                return _err(
                    request_id,
                    ERR_FORBIDDEN,
                    f"Access denied: order {order_id} does not belong to your restaurant.",
                )

            cur_status = ord_row["order_status"]

            # --- State Machine Transition Guards ---
            if cur_status in ("rejected", "cancelled"):
                if cur_status == target_status:
                    pass  # Idempotent re-rejection
                else:
                    return _err(
                        request_id,
                        ERR_INVALID_TRANSITION,
                        f"Cannot update order {order_id}: order is already {cur_status}.",
                    )

            if cur_status in ("ready_for_pickup", "ready", "out_for_delivery", "delivered"):
                return _err(
                    request_id,
                    ERR_INVALID_TRANSITION,
                    f"Cannot update order {order_id}: order is already completed ({cur_status}).",
                )

            if target_status == "accepted":
                if cur_status in ("placed", "pending"):
                    pass  # Allowed
                elif cur_status == "accepted":
                    pass  # Idempotent
                else:
                    return _err(
                        request_id,
                        ERR_INVALID_TRANSITION,
                        f"Cannot accept order {order_id} from current status '{cur_status}'.",
                    )

            elif target_status in ("rejected", "cancelled"):
                if cur_status in ("placed", "pending", "accepted"):
                    pass  # Allowed
                elif cur_status == target_status:
                    pass  # Idempotent
                elif cur_status == "preparing":
                    return _err(
                        request_id,
                        ERR_INVALID_TRANSITION,
                        f"Cannot reject order {order_id} while it is currently being prepared.",
                    )
                else:
                    return _err(
                        request_id,
                        ERR_INVALID_TRANSITION,
                        f"Cannot reject order {order_id} from status '{cur_status}'.",
                    )

            elif target_status == "preparing":
                if cur_status == "accepted":
                    pass  # Allowed
                elif cur_status in ("placed", "pending"):
                    return _err(
                        request_id,
                        ERR_INVALID_TRANSITION,
                        f"Order {order_id} must be accepted before preparation can start.",
                    )
                elif cur_status == "preparing":
                    pass  # Idempotent
                else:
                    return _err(
                        request_id,
                        ERR_INVALID_TRANSITION,
                        f"Cannot start preparation for order {order_id} from status '{cur_status}'.",
                    )

            elif target_status == "ready_for_pickup":
                if cur_status == "preparing":
                    pass  # Allowed
                elif cur_status in ("ready_for_pickup", "ready"):
                    pass  # Idempotent
                else:
                    return _err(
                        request_id,
                        ERR_INVALID_TRANSITION,
                        f"Order {order_id} must be in 'preparing' status before marking ready for pickup.",
                    )

            # Persist status change
            conn.execute(
                "UPDATE orders SET order_status = ? WHERE order_id = ?;",
                (target_status, order_id),
            )

            # V3.2: Automated refund initiation when a paid order is rejected or cancelled
            refund_data = None
            if target_status in ("rejected", "cancelled"):
                pay_row = conn.execute(
                    "SELECT payment_id, amount, payment_status, transaction_ref FROM payments WHERE order_id = ?;",
                    (order_id,),
                ).fetchone()

                if pay_row and pay_row["payment_status"] == "paid":
                    # Check if refund record already exists (prevent duplicate creation)
                    existing_ref = conn.execute(
                        """
                        SELECT refund_id, order_id, amount, refund_status, reason,
                               transaction_ref, attempt_count, created_at, updated_at
                        FROM refunds
                        WHERE order_id = ?;
                        """,
                        (order_id,),
                    ).fetchone()

                    if existing_ref:
                        refund_data = {
                            "refund_id": existing_ref["refund_id"],
                            "order_id": existing_ref["order_id"],
                            "amount": float(existing_ref["amount"]),
                            "refund_status": existing_ref["refund_status"],
                            "reason": existing_ref["reason"],
                            "transaction_ref": existing_ref["transaction_ref"],
                            "attempt_count": existing_ref["attempt_count"],
                            "created_at": existing_ref["created_at"],
                            "updated_at": existing_ref["updated_at"],
                        }
                    else:
                        paid_amount = float(pay_row["amount"])
                        reason = str(payload.get("reason", "")).strip() or f"Order #{order_id} {target_status} by seller."
                        sim_failure = bool(payload.get("simulate_refund_failure", False))

                        ref_status = "failed" if sim_failure else "refunded"
                        ref_txn = f"REF-SIM-{order_id}-{uuid.uuid4().hex[:8].upper()}"

                        conn.execute(
                            """
                            INSERT INTO refunds (order_id, amount, refund_status, reason, transaction_ref, attempt_count)
                            VALUES (?, ?, ?, ?, ?, 1);
                            """,
                            (order_id, paid_amount, ref_status, reason, ref_txn),
                        )
                        ref_id = conn.execute("SELECT last_insert_rowid() AS id;").fetchone()["id"]
                        refund_data = {
                            "refund_id": ref_id,
                            "order_id": order_id,
                            "amount": paid_amount,
                            "refund_status": ref_status,
                            "reason": reason,
                            "transaction_ref": ref_txn,
                            "attempt_count": 1,
                        }

        resp_data = {
            "order_id": order_id,
            "previous_status": cur_status,
            "order_status": target_status,
            "payment_status": ord_row["payment_status"],
            "refund": refund_data,
            "message": f"Order #{order_id} status successfully updated to {target_status}.",
        }
        return _ok(request_id, resp_data)

    except sqlite3.Error as exc:
        return _err(request_id, ERR_DB_ERROR, f"Database error updating order status: {exc}")


# -------------------------------------------------------------------
# Action: get_seller_menu (V3)
# -------------------------------------------------------------------
def _handle_get_seller_menu(request_id: str, payload: dict) -> dict:
    """
    Return menu items belonging exclusively to the seller's restaurant.
    """
    seller_id = payload.get("seller_id")

    try:
        with get_connection() as conn:
            restaurant, _, err = _authenticate_seller(conn, request_id, seller_id)
            if err:
                return err

            rid = restaurant["restaurant_id"]

            items = conn.execute(
                """
                SELECT item_id, restaurant_id, name, description, price, is_available
                FROM menu_items
                WHERE restaurant_id = ?
                ORDER BY item_id;
                """,
                (rid,),
            ).fetchall()

        return _ok(request_id, [
            {
                "item_id": it["item_id"],
                "restaurant_id": it["restaurant_id"],
                "name": it["name"],
                "description": it["description"] or "",
                "price": float(it["price"]),
                "is_available": int(it["is_available"]),
            }
            for it in items
        ])

    except sqlite3.Error as exc:
        return _err(request_id, ERR_DB_ERROR, f"Database error fetching seller menu: {exc}")


# -------------------------------------------------------------------
# Action: add_menu_item (V3)
# -------------------------------------------------------------------
def _handle_add_menu_item(request_id: str, payload: dict) -> dict:
    """
    Add a new menu item to the seller's restaurant.
    """
    seller_id = payload.get("seller_id")
    name = payload.get("name")
    description = payload.get("description", "")
    price = payload.get("price")
    is_available = payload.get("is_available", 1)

    if not name or not isinstance(name, str) or not name.strip():
        return _err(request_id, ERR_INVALID_PAYLOAD, "'name' is required and cannot be empty.")

    if price is None or not isinstance(price, (int, float)) or isinstance(price, bool) or price <= 0:
        return _err(request_id, ERR_INVALID_PAYLOAD, "'price' must be a positive number greater than 0.")

    if not isinstance(description, str):
        return _err(request_id, ERR_INVALID_PAYLOAD, "'description' must be a string.")

    if is_available in (True, 1, "1"):
        avail_int = 1
    elif is_available in (False, 0, "0"):
        avail_int = 0
    else:
        return _err(request_id, ERR_INVALID_PAYLOAD, "'is_available' must be 1 or 0.")

    try:
        with get_connection() as conn:
            restaurant, _, err = _authenticate_seller(conn, request_id, seller_id)
            if err:
                return err

            rid = restaurant["restaurant_id"]

            cursor = conn.execute(
                """
                INSERT INTO menu_items (restaurant_id, name, description, price, is_available)
                VALUES (?, ?, ?, ?, ?);
                """,
                (rid, name.strip(), description.strip(), float(price), avail_int),
            )
            item_id = cursor.lastrowid

        return _ok(request_id, {
            "item_id": item_id,
            "restaurant_id": rid,
            "name": name.strip(),
            "description": description.strip(),
            "price": float(price),
            "is_available": avail_int,
        })

    except sqlite3.Error as exc:
        return _err(request_id, ERR_DB_ERROR, f"Database error adding menu item: {exc}")


# -------------------------------------------------------------------
# Action: update_menu_item (V3)
# -------------------------------------------------------------------
def _handle_update_menu_item(request_id: str, payload: dict) -> dict:
    """
    Update an existing menu item (name, price, description, is_available).
    Preserves historical price snapshots on existing orders.
    """
    seller_id = payload.get("seller_id")
    item_id = payload.get("item_id")

    if item_id is None:
        return _err(request_id, ERR_INVALID_PAYLOAD, "payload must contain 'item_id'.")
    if not isinstance(item_id, int) or isinstance(item_id, bool):
        return _err(request_id, ERR_INVALID_PAYLOAD, "'item_id' must be an integer.")

    try:
        with get_connection() as conn:
            restaurant, _, err = _authenticate_seller(conn, request_id, seller_id)
            if err:
                return err

            item = conn.execute(
                "SELECT item_id, restaurant_id, name, description, price, is_available FROM menu_items WHERE item_id = ?;",
                (item_id,),
            ).fetchone()

            if item is None:
                return _err(request_id, ERR_NOT_FOUND, f"No menu item found with id {item_id}.")

            if item["restaurant_id"] != restaurant["restaurant_id"]:
                return _err(
                    request_id,
                    ERR_FORBIDDEN,
                    f"Access denied: menu item {item_id} does not belong to your restaurant.",
                )

            new_name = item["name"]
            new_desc = item["description"] or ""
            new_price = item["price"]
            new_avail = item["is_available"]

            if "name" in payload:
                n = payload["name"]
                if not n or not isinstance(n, str) or not n.strip():
                    return _err(request_id, ERR_INVALID_PAYLOAD, "'name' cannot be empty.")
                new_name = n.strip()

            if "description" in payload:
                d = payload["description"]
                if not isinstance(d, str):
                    return _err(request_id, ERR_INVALID_PAYLOAD, "'description' must be a string.")
                new_desc = d.strip()

            if "price" in payload:
                p = payload["price"]
                if not isinstance(p, (int, float)) or isinstance(p, bool) or p <= 0:
                    return _err(request_id, ERR_INVALID_PAYLOAD, "'price' must be a positive number greater than 0.")
                new_price = float(p)

            if "is_available" in payload:
                a = payload["is_available"]
                if a in (True, 1, "1"):
                    new_avail = 1
                elif a in (False, 0, "0"):
                    new_avail = 0
                else:
                    return _err(request_id, ERR_INVALID_PAYLOAD, "'is_available' must be 1 or 0.")

            conn.execute(
                """
                UPDATE menu_items
                SET name = ?, description = ?, price = ?, is_available = ?
                WHERE item_id = ?;
                """,
                (new_name, new_desc, new_price, new_avail, item_id),
            )

        return _ok(request_id, {
            "item_id": item_id,
            "restaurant_id": restaurant["restaurant_id"],
            "name": new_name,
            "description": new_desc,
            "price": new_price,
            "is_available": new_avail,
        })

    except sqlite3.Error as exc:
        return _err(request_id, ERR_DB_ERROR, f"Database error updating menu item: {exc}")


# -------------------------------------------------------------------
# Action: update_menu_item_availability (V3)
# -------------------------------------------------------------------
def _handle_update_menu_item_availability(request_id: str, payload: dict) -> dict:
    """
    Toggle availability of a menu item belonging to the seller's restaurant.
    """
    seller_id = payload.get("seller_id")
    item_id = payload.get("item_id")
    is_available = payload.get("is_available")

    if item_id is None:
        return _err(request_id, ERR_INVALID_PAYLOAD, "payload must contain 'item_id'.")
    if not isinstance(item_id, int) or isinstance(item_id, bool):
        return _err(request_id, ERR_INVALID_PAYLOAD, "'item_id' must be an integer.")

    if is_available in (True, 1, "1"):
        avail_int = 1
    elif is_available in (False, 0, "0"):
        avail_int = 0
    else:
        return _err(request_id, ERR_INVALID_PAYLOAD, "'is_available' must be 1 or 0.")

    try:
        with get_connection() as conn:
            restaurant, _, err = _authenticate_seller(conn, request_id, seller_id)
            if err:
                return err

            item = conn.execute(
                "SELECT item_id, restaurant_id, name, description, price, is_available FROM menu_items WHERE item_id = ?;",
                (item_id,),
            ).fetchone()

            if item is None:
                return _err(request_id, ERR_NOT_FOUND, f"No menu item found with id {item_id}.")

            if item["restaurant_id"] != restaurant["restaurant_id"]:
                return _err(
                    request_id,
                    ERR_FORBIDDEN,
                    f"Access denied: menu item {item_id} does not belong to your restaurant.",
                )

            conn.execute(
                "UPDATE menu_items SET is_available = ? WHERE item_id = ?;",
                (avail_int, item_id),
            )

        return _ok(request_id, {
            "item_id": item_id,
            "restaurant_id": restaurant["restaurant_id"],
            "name": item["name"],
            "is_available": avail_int,
        })

    except sqlite3.Error as exc:
        return _err(request_id, ERR_DB_ERROR, f"Database error updating item availability: {exc}")


# -------------------------------------------------------------------
# Action: get_refund (V3.2)
# -------------------------------------------------------------------
def _handle_get_refund(request_id: str, payload: dict) -> dict:
    """
    Retrieve refund details for an order.
    Enforces customer and seller authorization.
    """
    if "order_id" not in payload:
        return _err(request_id, ERR_INVALID_PAYLOAD, "payload must contain 'order_id'.")
    order_id = payload["order_id"]
    if not isinstance(order_id, int) or isinstance(order_id, bool):
        return _err(request_id, ERR_INVALID_PAYLOAD, "'order_id' must be an integer.")

    customer_id = payload.get("customer_id")
    seller_id = payload.get("seller_id")

    try:
        with get_connection() as conn:
            ord_row = conn.execute(
                "SELECT order_id, customer_id, restaurant_id FROM orders WHERE order_id = ?;",
                (order_id,),
            ).fetchone()

            if ord_row is None:
                return _err(request_id, ERR_NOT_FOUND, f"No order found with id {order_id}.")

            if customer_id is not None:
                if not isinstance(customer_id, int) or isinstance(customer_id, bool):
                    return _err(request_id, ERR_INVALID_PAYLOAD, "'customer_id' must be an integer.")
                if ord_row["customer_id"] != customer_id:
                    return _err(request_id, ERR_FORBIDDEN, f"Access denied: order {order_id} does not belong to customer {customer_id}.")

            if seller_id is not None:
                if not isinstance(seller_id, int) or isinstance(seller_id, bool):
                    return _err(request_id, ERR_INVALID_PAYLOAD, "'seller_id' must be an integer.")
                restaurant, _, err = _authenticate_seller(conn, request_id, seller_id)
                if err:
                    return err
                if ord_row["restaurant_id"] != restaurant["restaurant_id"]:
                    return _err(request_id, ERR_FORBIDDEN, f"Access denied: order {order_id} does not belong to your restaurant.")

            ref_row = conn.execute(
                """
                SELECT refund_id, order_id, amount, refund_status, reason,
                       transaction_ref, attempt_count, created_at, updated_at
                FROM refunds
                WHERE order_id = ?;
                """,
                (order_id,),
            ).fetchone()

            if ref_row is None:
                return _err(request_id, ERR_REFUND_NOT_FOUND, f"No refund record found for order {order_id}.")

            return _ok(request_id, {
                "refund_id": ref_row["refund_id"],
                "order_id": ref_row["order_id"],
                "amount": float(ref_row["amount"]),
                "refund_status": ref_row["refund_status"],
                "reason": ref_row["reason"],
                "transaction_ref": ref_row["transaction_ref"],
                "attempt_count": ref_row["attempt_count"],
                "created_at": ref_row["created_at"],
                "updated_at": ref_row["updated_at"],
            })

    except sqlite3.Error as exc:
        return _err(request_id, ERR_DB_ERROR, f"Database error retrieving refund: {exc}")


# -------------------------------------------------------------------
# Action: get_refund_history (V3.2)
# -------------------------------------------------------------------
def _handle_get_refund_history(request_id: str, payload: dict) -> dict:
    """
    Retrieve refund history for a customer or a seller's restaurant.
    """
    customer_id = payload.get("customer_id")
    seller_id = payload.get("seller_id")

    if customer_id is None and seller_id is None:
        return _err(request_id, ERR_INVALID_PAYLOAD, "payload must contain either 'customer_id' or 'seller_id'.")

    try:
        with get_connection() as conn:
            if customer_id is not None:
                if not isinstance(customer_id, int) or isinstance(customer_id, bool):
                    return _err(request_id, ERR_INVALID_PAYLOAD, "'customer_id' must be an integer.")
                user = conn.execute("SELECT user_id FROM users WHERE user_id = ?;", (customer_id,)).fetchone()
                if user is None:
                    return _err(request_id, ERR_NOT_FOUND, f"No customer found with id {customer_id}.")

                rows = conn.execute(
                    """
                    SELECT ref.refund_id, ref.order_id, ref.amount, ref.refund_status, ref.reason,
                           ref.transaction_ref, ref.attempt_count, ref.created_at, ref.updated_at,
                           o.restaurant_id, r.name AS restaurant_name
                    FROM refunds ref
                    JOIN orders o ON ref.order_id = o.order_id
                    JOIN restaurants r ON o.restaurant_id = r.restaurant_id
                    WHERE o.customer_id = ?
                    ORDER BY ref.refund_id DESC;
                    """,
                    (customer_id,),
                ).fetchall()
            else:
                if not isinstance(seller_id, int) or isinstance(seller_id, bool):
                    return _err(request_id, ERR_INVALID_PAYLOAD, "'seller_id' must be an integer.")
                restaurant, _, err = _authenticate_seller(conn, request_id, seller_id)
                if err:
                    return err

                rows = conn.execute(
                    """
                    SELECT ref.refund_id, ref.order_id, ref.amount, ref.refund_status, ref.reason,
                           ref.transaction_ref, ref.attempt_count, ref.created_at, ref.updated_at,
                           o.customer_id, u.name AS customer_name
                    FROM refunds ref
                    JOIN orders o ON ref.order_id = o.order_id
                    JOIN users u ON o.customer_id = u.user_id
                    WHERE o.restaurant_id = ?
                    ORDER BY ref.refund_id DESC;
                    """,
                    (restaurant["restaurant_id"],),
                ).fetchall()

            refunds = [
                {
                    "refund_id": r["refund_id"],
                    "order_id": r["order_id"],
                    "amount": float(r["amount"]),
                    "refund_status": r["refund_status"],
                    "reason": r["reason"],
                    "transaction_ref": r["transaction_ref"],
                    "attempt_count": r["attempt_count"],
                    "created_at": r["created_at"],
                    "updated_at": r["updated_at"],
                }
                for r in rows
            ]
            return _ok(request_id, refunds)

    except sqlite3.Error as exc:
        return _err(request_id, ERR_DB_ERROR, f"Database error retrieving refund history: {exc}")


# -------------------------------------------------------------------
# Action: retry_refund (V3.2)
# -------------------------------------------------------------------
def _handle_retry_refund(request_id: str, payload: dict) -> dict:
    """
    Retry a failed refund for an order.
    Validates ownership, ensures refund is in 'failed' status, generates a new transaction_ref,
    increments attempt_count, and atomically updates the record.
    """
    if "order_id" not in payload:
        return _err(request_id, ERR_INVALID_PAYLOAD, "payload must contain 'order_id'.")
    order_id = payload["order_id"]
    if not isinstance(order_id, int) or isinstance(order_id, bool):
        return _err(request_id, ERR_INVALID_PAYLOAD, "'order_id' must be an integer.")

    customer_id = payload.get("customer_id")
    seller_id = payload.get("seller_id")
    simulate_failure = bool(payload.get("simulate_failure", False))

    try:
        with get_connection() as conn:
            ord_row = conn.execute(
                "SELECT order_id, customer_id, restaurant_id, order_status FROM orders WHERE order_id = ?;",
                (order_id,),
            ).fetchone()

            if ord_row is None:
                return _err(request_id, ERR_NOT_FOUND, f"No order found with id {order_id}.")

            if customer_id is not None:
                if not isinstance(customer_id, int) or isinstance(customer_id, bool):
                    return _err(request_id, ERR_INVALID_PAYLOAD, "'customer_id' must be an integer.")
                if ord_row["customer_id"] != customer_id:
                    return _err(request_id, ERR_FORBIDDEN, f"Access denied: order {order_id} does not belong to customer {customer_id}.")

            if seller_id is not None:
                if not isinstance(seller_id, int) or isinstance(seller_id, bool):
                    return _err(request_id, ERR_INVALID_PAYLOAD, "'seller_id' must be an integer.")
                restaurant, _, err = _authenticate_seller(conn, request_id, seller_id)
                if err:
                    return err
                if ord_row["restaurant_id"] != restaurant["restaurant_id"]:
                    return _err(request_id, ERR_FORBIDDEN, f"Access denied: order {order_id} does not belong to your restaurant.")

            ref_row = conn.execute(
                """
                SELECT refund_id, order_id, amount, refund_status, reason, transaction_ref, attempt_count
                FROM refunds
                WHERE order_id = ?;
                """,
                (order_id,),
            ).fetchone()

            if ref_row is None:
                return _err(request_id, ERR_REFUND_NOT_FOUND, f"No refund record found for order {order_id}.")

            if ref_row["refund_status"] == "refunded":
                return _err(request_id, ERR_REFUND_ALREADY_PROCESSED, f"Refund for order {order_id} has already succeeded.")

            if ref_row["refund_status"] != "failed":
                return _err(request_id, ERR_REFUND_NOT_RETRYABLE, f"Refund for order {order_id} is in '{ref_row['refund_status']}' status and cannot be retried.")

            new_status = "failed" if simulate_failure else "refunded"
            new_txn_ref = f"REF-SIM-{order_id}-{uuid.uuid4().hex[:8].upper()}"

            conn.execute(
                """
                UPDATE refunds
                SET refund_status = ?,
                    transaction_ref = ?,
                    attempt_count = attempt_count + 1,
                    updated_at = datetime('now')
                WHERE order_id = ?;
                """,
                (new_status, new_txn_ref, order_id),
            )

            updated_row = conn.execute(
                """
                SELECT refund_id, order_id, amount, refund_status, reason,
                       transaction_ref, attempt_count, created_at, updated_at
                FROM refunds
                WHERE order_id = ?;
                """,
                (order_id,),
            ).fetchone()

            return _ok(request_id, {
                "refund_id": updated_row["refund_id"],
                "order_id": updated_row["order_id"],
                "amount": float(updated_row["amount"]),
                "refund_status": updated_row["refund_status"],
                "reason": updated_row["reason"],
                "transaction_ref": updated_row["transaction_ref"],
                "attempt_count": updated_row["attempt_count"],
                "created_at": updated_row["created_at"],
                "updated_at": updated_row["updated_at"],
            })

    except sqlite3.Error as exc:
        return _err(request_id, ERR_DB_ERROR, f"Database error retrying refund: {exc}")


# -------------------------------------------------------------------
# Action: register (V4.1)
# -------------------------------------------------------------------
def _handle_register(request_id: str, payload: dict) -> dict:
    """
    Register a new user account with hashed password and email verification.

    Payload:
      name: str (required)
      email: str (required)
      password: str (required, min 8 chars, letter + digit)
      role: str (optional, default 'customer', allowed 'customer' or 'seller')
    """
    name = payload.get("name")
    if not isinstance(name, str) or not name.strip():
        return _err(request_id, ERR_INVALID_PAYLOAD, "'name' is required.")
    name = name.strip()

    email = payload.get("email")
    if not isinstance(email, str) or not validate_email(email):
        return _err(request_id, ERR_INVALID_PAYLOAD, "A valid 'email' address is required.")
    email = email.strip().lower()

    password = payload.get("password")
    is_valid_pwd, pwd_err = validate_password(password)
    if not is_valid_pwd:
        return _err(request_id, ERR_INVALID_PAYLOAD, pwd_err)

    role = payload.get("role", "customer")
    if not isinstance(role, str) or role not in ("customer", "seller"):
        return _err(request_id, ERR_UNAUTHORIZED_ROLE, "Public registration is only permitted for 'customer' and 'seller' roles.")

    pwd_hash = hash_password(password)
    raw_token, token_hash = generate_token()

    try:
        with get_connection() as conn:
            # Check unique email
            existing = conn.execute("SELECT user_id FROM users WHERE email = ?;", (email,)).fetchone()
            if existing:
                return _err(request_id, ERR_EMAIL_ALREADY_EXISTS, f"Email '{email}' is already registered.")

            cursor = conn.execute(
                """
                INSERT INTO users (name, email, password_hash, role, is_active, email_verified)
                VALUES (?, ?, ?, ?, 1, 0);
                """,
                (name, email, pwd_hash, role),
            )
            user_id = cursor.lastrowid

            conn.execute(
                """
                INSERT INTO email_verification_tokens (user_id, token_hash, token_type, expires_at)
                VALUES (?, ?, 'registration', datetime('now', '+24 hours'));
                """,
                (user_id, token_hash),
            )

        # Dispatch verification email via adapter
        verify_url = f"http://127.0.0.1:8000/api/auth/verify-email?token={raw_token}"
        default_email_adapter.send_verification_email(email, verify_url)

        return _ok(request_id, {
            "user_id": user_id,
            "name": name,
            "email": email,
            "role": role,
            "email_verified": 0,
            "verification_token": raw_token,
            "message": "Registration successful. Please verify your email.",
        })

    except sqlite3.Error as exc:
        return _err(request_id, ERR_DB_ERROR, f"Database error during registration: {exc}")


# -------------------------------------------------------------------
# Action: verify_email (V4.1)
# -------------------------------------------------------------------
def _handle_verify_email(request_id: str, payload: dict) -> dict:
    """
    Verify a user's email using a single-use token.

    Payload:
      token: str (required raw token)
    """
    raw_token = payload.get("token")
    if not isinstance(raw_token, str) or not raw_token.strip():
        return _err(request_id, ERR_INVALID_PAYLOAD, "'token' is required.")
    raw_token = raw_token.strip()

    token_hash = hash_token(raw_token)

    try:
        with get_connection() as conn:
            row = conn.execute(
                """
                SELECT token_id, user_id, expires_at, used_at,
                       (datetime('now') > expires_at) AS is_expired
                FROM email_verification_tokens
                WHERE token_hash = ? AND token_type = 'registration';
                """,
                (token_hash,),
            ).fetchone()

            if not row:
                return _err(request_id, ERR_INVALID_TOKEN, "Verification token is invalid or does not exist.")

            if row["used_at"] is not None:
                return _err(request_id, ERR_TOKEN_ALREADY_USED, "This verification token has already been used.")

            if row["is_expired"]:
                return _err(request_id, ERR_TOKEN_EXPIRED, "Verification token has expired. Please request a new one.")

            # Mark token as used and verify user
            conn.execute(
                "UPDATE email_verification_tokens SET used_at = datetime('now') WHERE token_id = ?;",
                (row["token_id"],),
            )
            conn.execute(
                "UPDATE users SET email_verified = 1, updated_at = datetime('now') WHERE user_id = ?;",
                (row["user_id"],),
            )

        return _ok(request_id, {
            "user_id": row["user_id"],
            "email_verified": 1,
            "message": "Email verified successfully. You may now log in.",
        })

    except sqlite3.Error as exc:
        return _err(request_id, ERR_DB_ERROR, f"Database error verifying email: {exc}")


# -------------------------------------------------------------------
# Action: login (V4.1)
# -------------------------------------------------------------------
def _handle_login(request_id: str, payload: dict) -> dict:
    """
    Authenticate a user with email and password and issue a session token.

    Payload:
      email: str (required)
      password: str (required)
      ip_address: str (optional)
      user_agent: str (optional)
    """
    email = payload.get("email")
    password = payload.get("password")
    if not isinstance(email, str) or not email.strip() or not isinstance(password, str):
        return _err(request_id, ERR_INVALID_PAYLOAD, "'email' and 'password' are required.")
    email = email.strip().lower()

    try:
        with get_connection() as conn:
            user = conn.execute(
                """
                SELECT user_id, name, email, password_hash, role, is_active, email_verified
                FROM users
                WHERE email = ?;
                """,
                (email,),
            ).fetchone()

            if not user or not verify_password(password, user["password_hash"]):
                return _err(request_id, ERR_INVALID_CREDENTIALS, "Invalid email or password.")

            if user["is_active"] == 0:
                return _err(request_id, ERR_ACCOUNT_DEACTIVATED, "Account is deactivated.")

            if user["email_verified"] == 0:
                return _err(request_id, ERR_EMAIL_NOT_VERIFIED, "Email address is not verified. Please verify your email before logging in.")

            raw_token, token_hash = generate_token()
            ip_address = payload.get("ip_address")
            user_agent = payload.get("user_agent")

            conn.execute(
                """
                INSERT INTO sessions (session_id, user_id, expires_at, ip_address, user_agent)
                VALUES (?, ?, datetime('now', '+24 hours'), ?, ?);
                """,
                (token_hash, user["user_id"], ip_address, user_agent),
            )

        return _ok(request_id, {
            "session_token": raw_token,
            "user": {
                "user_id": user["user_id"],
                "name": user["name"],
                "email": user["email"],
                "role": user["role"],
                "email_verified": user["email_verified"],
            },
            "message": "Login successful.",
        })

    except sqlite3.Error as exc:
        return _err(request_id, ERR_DB_ERROR, f"Database error during login: {exc}")


# -------------------------------------------------------------------
# Action: logout (V4.1)
# -------------------------------------------------------------------
def _handle_logout(request_id: str, payload: dict) -> dict:
    """
    Revoke an active session.

    Payload:
      session_token: str (required)
    """
    session_token = payload.get("session_token")
    if not isinstance(session_token, str) or not session_token.strip():
        return _err(request_id, ERR_INVALID_PAYLOAD, "'session_token' is required.")
    session_token = session_token.strip()

    token_hash = hash_token(session_token)

    try:
        with get_connection() as conn:
            conn.execute(
                "UPDATE sessions SET revoked_at = datetime('now') WHERE session_id = ? AND revoked_at IS NULL;",
                (token_hash,),
            )
        return _ok(request_id, {"message": "Logged out successfully."})

    except sqlite3.Error as exc:
        return _err(request_id, ERR_DB_ERROR, f"Database error during logout: {exc}")


# -------------------------------------------------------------------
# Action: get_session (V4.1)
# -------------------------------------------------------------------
def _handle_get_session(request_id: str, payload: dict) -> dict:
    """
    Validate a session token and return the associated authenticated user profile.

    Payload:
      session_token: str (required)
    """
    session_token = payload.get("session_token")
    if not isinstance(session_token, str) or not session_token.strip():
        return _err(request_id, ERR_UNAUTHORIZED, "Authentication session token is required.")
    session_token = session_token.strip()

    token_hash = hash_token(session_token)

    try:
        with get_connection() as conn:
            row = conn.execute(
                """
                SELECT s.session_id, s.user_id, s.expires_at, s.revoked_at,
                       u.name, u.email, u.role, u.is_active, u.email_verified,
                       (datetime('now') > s.expires_at) AS is_expired
                FROM sessions s
                JOIN users u ON s.user_id = u.user_id
                WHERE s.session_id = ?;
                """,
                (token_hash,),
            ).fetchone()

            if not row or row["revoked_at"] is not None or row["is_expired"]:
                return _err(request_id, ERR_UNAUTHORIZED, "Session is invalid or has expired.")

            if row["is_active"] == 0:
                return _err(request_id, ERR_ACCOUNT_DEACTIVATED, "Account is deactivated.")

        return _ok(request_id, {
            "user_id": row["user_id"],
            "name": row["name"],
            "email": row["email"],
            "role": row["role"],
            "email_verified": row["email_verified"],
        })

    except sqlite3.Error as exc:
        return _err(request_id, ERR_DB_ERROR, f"Database error retrieving session: {exc}")


# -------------------------------------------------------------------
# Action: resend_verification_email (V4.1)
# -------------------------------------------------------------------
def _handle_resend_verification_email(request_id: str, payload: dict) -> dict:
    """
    Generate and dispatch a new verification link for an unverified account.

    Payload:
      email: str (required)
    """
    email = payload.get("email")
    if not isinstance(email, str) or not validate_email(email):
        return _err(request_id, ERR_INVALID_PAYLOAD, "A valid 'email' address is required.")
    email = email.strip().lower()

    try:
        with get_connection() as conn:
            user = conn.execute(
                "SELECT user_id, email_verified, is_active FROM users WHERE email = ?;",
                (email,),
            ).fetchone()

            if not user:
                return _ok(request_id, {"message": "If this email is registered and unverified, a new link has been dispatched."})

            if user["is_active"] == 0:
                return _err(request_id, ERR_ACCOUNT_DEACTIVATED, "Account is deactivated.")

            if user["email_verified"] == 1:
                return _ok(request_id, {"message": "Email is already verified. You may log in."})

            raw_token, token_hash = generate_token()
            conn.execute(
                """
                INSERT INTO email_verification_tokens (user_id, token_hash, token_type, expires_at)
                VALUES (?, ?, 'registration', datetime('now', '+24 hours'));
                """,
                (user["user_id"], token_hash),
            )

        verify_url = f"http://127.0.0.1:8000/api/auth/verify-email?token={raw_token}"
        default_email_adapter.send_verification_email(email, verify_url)

        return _ok(request_id, {
            "message": "Verification link has been sent.",
            "verification_token": raw_token,
        })

    except sqlite3.Error as exc:
        return _err(request_id, ERR_DB_ERROR, f"Database error resending verification email: {exc}")


# -------------------------------------------------------------------
# Dispatch table — maps action name → handler function
# -------------------------------------------------------------------
_HANDLERS = {
    "ping":                          _handle_ping,
    "list_restaurants":              _handle_list_restaurants,
    "list_menu":                     _handle_list_menu,
    "create_order":                  _handle_create_order,
    "simulate_payment":              _handle_simulate_payment,
    "get_order":                     _handle_get_order,
    "get_order_history":             _handle_get_order_history,
    "get_seller_profile":            _handle_get_seller_profile,
    "get_seller_restaurant":         _handle_get_seller_profile,
    "get_seller_orders":             _handle_get_seller_orders,
    "get_seller_order":              _handle_get_seller_order,
    "update_order_status":           _handle_update_order_status,
    "get_seller_menu":               _handle_get_seller_menu,
    "add_menu_item":                 _handle_add_menu_item,
    "update_menu_item":              _handle_update_menu_item,
    "update_menu_item_availability": _handle_update_menu_item_availability,
    "get_refund":                    _handle_get_refund,
    "get_refund_history":            _handle_get_refund_history,
    "retry_refund":                  _handle_retry_refund,
    # V4.1 Auth actions
    "register":                      _handle_register,
    "verify_email":                  _handle_verify_email,
    "login":                         _handle_login,
    "logout":                        _handle_logout,
    "get_session":                   _handle_get_session,
    "resend_verification_email":     _handle_resend_verification_email,
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

