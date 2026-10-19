"""
tests/test_schema.py
====================
Automated tests for Mini Swiggy – Version 0.

What is tested:
  1. All eight expected tables are created by init_db().
  2. The UNIQUE constraint on users.email is enforced.
  3. The CHECK constraint on users.role rejects invalid values.
  4. Foreign-key enforcement: inserting a menu item for a
     non-existent restaurant raises IntegrityError.
  5. The price CHECK on menu_items rejects zero / negative prices.
  6. The quantity CHECK on order_items rejects zero / negative.
  7. Seeding is idempotent: running seed_data() twice produces
     exactly 3 restaurants and 6 menu items.
  8. All expected seed restaurant names are present.
  9. Each restaurant has exactly 2 menu items.

Run from the project root with:
    python -m pytest tests/ -v
"""

import sqlite3
import pathlib
import sys
import pytest

# Make sure we can import from the project root.
sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))

from database.db import get_connection, init_db
from database.seed import seed_data


# -------------------------------------------------------------------
# Fixture: fresh in-memory database for each test
# -------------------------------------------------------------------
@pytest.fixture
def db(tmp_path, monkeypatch):
    """
    Provide a temporary, isolated SQLite database for each test.

    We monkeypatch database.db.DB_PATH so that tests never touch the
    real swiggy.db file on disk.
    """
    import database.db as db_module

    # Point DB_PATH at a temp file for the duration of this test.
    temp_db = tmp_path / "test_swiggy.db"
    monkeypatch.setattr(db_module, "DB_PATH", temp_db)

    # Create the schema in the temp database.
    init_db()

    # Yield the path so tests can inspect it directly if needed.
    yield temp_db


# -------------------------------------------------------------------
# 1. Schema – all tables exist
# -------------------------------------------------------------------
EXPECTED_TABLES = {
    "users",
    "restaurants",
    "menu_items",
    "orders",
    "order_items",
    "payments",
    "delivery_partners",
    "deliveries",
}


def test_all_tables_created(db):
    """init_db() must create all eight expected tables."""
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table';"
        ).fetchall()
        # sqlite_sequence is an internal SQLite bookkeeping table created
        # automatically whenever a table uses AUTOINCREMENT.  We exclude it
        # so the assertion only checks *our* application tables.
        actual = {row["name"] for row in rows} - {"sqlite_sequence"}

    assert EXPECTED_TABLES == actual, (
        f"Missing tables: {EXPECTED_TABLES - actual}"
    )


# -------------------------------------------------------------------
# 2. Unique email constraint
# -------------------------------------------------------------------
def test_duplicate_email_rejected(db):
    """Inserting two users with the same email must raise IntegrityError."""
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO users (name, email, role) VALUES (?, ?, ?);",
            ("User A", "same@example.com", "customer"),
        )
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO users (name, email, role) VALUES (?, ?, ?);",
                ("User B", "same@example.com", "customer"),
            )


# -------------------------------------------------------------------
# 3. Role CHECK constraint
# -------------------------------------------------------------------
def test_invalid_role_rejected(db):
    """Inserting a user with an invalid role must raise IntegrityError."""
    with get_connection() as conn:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO users (name, email, role) VALUES (?, ?, ?);",
                ("Ghost", "ghost@example.com", "superuser"),  # not in allowed list
            )


# -------------------------------------------------------------------
# 4. Foreign-key enforcement (menu_item → restaurant)
# -------------------------------------------------------------------
def test_menu_item_fk_enforced(db):
    """
    Inserting a menu item that references a non-existent restaurant
    must raise IntegrityError (requires PRAGMA foreign_keys = ON).
    """
    with get_connection() as conn:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                """
                INSERT INTO menu_items (restaurant_id, name, price)
                VALUES (?, ?, ?);
                """,
                (9999, "Ghost Burger", 99.0),  # restaurant_id 9999 doesn't exist
            )


# -------------------------------------------------------------------
# 5. Price CHECK on menu_items
# -------------------------------------------------------------------
@pytest.mark.parametrize("bad_price", [0, -10, -0.01])
def test_menu_item_price_must_be_positive(db, bad_price):
    """menu_items.price must be > 0; zero and negatives must be rejected."""
    with get_connection() as conn:
        # First create a valid restaurant to satisfy the FK.
        conn.execute(
            "INSERT INTO restaurants (name) VALUES (?);",
            ("Test Restaurant",),
        )
        restaurant_id = conn.execute(
            "SELECT restaurant_id FROM restaurants WHERE name = ?;",
            ("Test Restaurant",),
        ).fetchone()["restaurant_id"]

        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO menu_items (restaurant_id, name, price) VALUES (?, ?, ?);",
                (restaurant_id, "Bad Item", bad_price),
            )


# -------------------------------------------------------------------
# 6. Quantity CHECK on order_items
# -------------------------------------------------------------------
def test_order_item_quantity_must_be_positive(db):
    """order_items.quantity must be > 0."""
    with get_connection() as conn:
        # Minimal data chain: user → restaurant → menu_item → order → order_item
        conn.execute(
            "INSERT INTO users (name, email, role) VALUES (?, ?, ?);",
            ("Cust", "cust@x.com", "customer"),
        )
        customer_id = conn.execute(
            "SELECT user_id FROM users WHERE email = ?;", ("cust@x.com",)
        ).fetchone()["user_id"]

        conn.execute("INSERT INTO restaurants (name) VALUES (?);", ("R",))
        restaurant_id = conn.execute(
            "SELECT restaurant_id FROM restaurants WHERE name = ?;", ("R",)
        ).fetchone()["restaurant_id"]

        conn.execute(
            "INSERT INTO menu_items (restaurant_id, name, price) VALUES (?, ?, ?);",
            (restaurant_id, "Item", 50.0),
        )
        item_id = conn.execute(
            "SELECT item_id FROM menu_items WHERE restaurant_id = ?;",
            (restaurant_id,)
        ).fetchone()["item_id"]

        conn.execute(
            """
            INSERT INTO orders (customer_id, restaurant_id, total_amount)
            VALUES (?, ?, ?);
            """,
            (customer_id, restaurant_id, 50.0),
        )
        order_id = conn.execute(
            "SELECT order_id FROM orders WHERE customer_id = ?;",
            (customer_id,)
        ).fetchone()["order_id"]

        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                """
                INSERT INTO order_items (order_id, item_id, quantity, unit_price)
                VALUES (?, ?, ?, ?);
                """,
                (order_id, item_id, 0, 50.0),  # quantity = 0 → must fail
            )


# -------------------------------------------------------------------
# 7. Idempotent seeding – no duplicates after two runs
# -------------------------------------------------------------------
def test_seed_is_idempotent(db):
    """
    Running seed_data() twice must produce exactly 3 restaurants
    and 6 menu items (no duplicates).
    """
    seed_data()
    seed_data()  # second run – should be a no-op

    with get_connection() as conn:
        restaurant_count = conn.execute(
            "SELECT COUNT(*) AS n FROM restaurants;"
        ).fetchone()["n"]
        menu_item_count = conn.execute(
            "SELECT COUNT(*) AS n FROM menu_items;"
        ).fetchone()["n"]

    assert restaurant_count == 3, f"Expected 3 restaurants, got {restaurant_count}"
    assert menu_item_count == 6, f"Expected 6 menu items, got {menu_item_count}"


# -------------------------------------------------------------------
# 8. Correct restaurant names present after seeding
# -------------------------------------------------------------------
def test_seed_restaurant_names(db):
    """All three expected restaurant names must be in the database."""
    seed_data()

    with get_connection() as conn:
        rows = conn.execute("SELECT name FROM restaurants ORDER BY name;").fetchall()
        names = {row["name"] for row in rows}

    expected = {"Hyderabad Spice House", "Dosa Corner", "Pizza Junction"}
    assert names == expected, f"Unexpected names: {names}"


# -------------------------------------------------------------------
# 9. Each restaurant has exactly 2 menu items
# -------------------------------------------------------------------
def test_each_restaurant_has_two_items(db):
    """After seeding, every restaurant must have exactly 2 menu items."""
    seed_data()

    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT r.name, COUNT(m.item_id) AS item_count
            FROM restaurants r
            LEFT JOIN menu_items m ON m.restaurant_id = r.restaurant_id
            GROUP BY r.restaurant_id;
            """
        ).fetchall()

    for row in rows:
        assert row["item_count"] == 2, (
            f"'{row['name']}' has {row['item_count']} items, expected 2"
        )
