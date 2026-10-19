"""
server/request_handler.py
==========================
Handles validated JSON requests and returns structured JSON responses.

This module is PURE APPLICATION LOGIC — it knows nothing about sockets
or networking. It receives a parsed Python dict and returns a Python dict.
Keeping it separate makes it easy to test without starting a real server.

Supported actions (V1):
  - ping          : health check
  - list_restaurants : return all restaurants from the database
  - list_menu     : return menu items for a given restaurant_id

Future actions (V2+):
  - register / login (authentication)
  - place_order, track_order, etc.
"""

import sqlite3
from database.db import get_connection


# -------------------------------------------------------------------
# Error codes — using string constants avoids typos
# -------------------------------------------------------------------
ERR_UNKNOWN_ACTION   = "UNKNOWN_ACTION"
ERR_INVALID_REQUEST  = "INVALID_REQUEST"
ERR_INVALID_PAYLOAD  = "INVALID_PAYLOAD"
ERR_NOT_FOUND        = "NOT_FOUND"
ERR_DB_ERROR         = "DB_ERROR"


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
# Action: ping
# -------------------------------------------------------------------
def _handle_ping(request_id: str, payload: dict) -> dict:
    """
    Simple health check.

    Request:  {"request_id": "r1", "action": "ping", "payload": {}}
    Response: {"request_id": "r1", "ok": true, "data": {"status": "ok"}}
    """
    return _ok(request_id, {"status": "ok"})


# -------------------------------------------------------------------
# Action: list_restaurants
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
# Action: list_menu
# -------------------------------------------------------------------
def _handle_list_menu(request_id: str, payload: dict) -> dict:
    """
    Return menu items for one restaurant.

    The payload must contain:
      restaurant_id (int) — the restaurant to query.

    Distinguishing cases:
      - restaurant_id is not an int        → INVALID_PAYLOAD
      - restaurant_id refers to no restaurant → NOT_FOUND
      - restaurant exists but has no items → ok, empty list
    """
    # --- Validate payload ---
    if "restaurant_id" not in payload:
        return _err(request_id, ERR_INVALID_PAYLOAD,
                    "payload must contain 'restaurant_id'.")

    rid = payload["restaurant_id"]
    if not isinstance(rid, int):
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
# Dispatch table — maps action name → handler function
# -------------------------------------------------------------------
_HANDLERS = {
    "ping":              _handle_ping,
    "list_restaurants":  _handle_list_restaurants,
    "list_menu":         _handle_list_menu,
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
