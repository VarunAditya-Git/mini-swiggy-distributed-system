"""
database/db.py
==============
Central database helper for Mini Swiggy.

Responsibilities:
  - Provide a single function (get_connection) that returns a configured
    sqlite3.Connection with foreign keys enforced and row_factory set.
  - Provide init_db() to create all tables from schema.sql.

Why a separate module?
  Keeping connection logic here means every other module imports
  get_connection() from one place.  If we ever switch from SQLite to
  another database, only this file needs to change.
"""

import sqlite3
import pathlib

# -------------------------------------------------------------------
# Paths
# -------------------------------------------------------------------
# __file__ is  <project_root>/database/db.py
# .parent      -> <project_root>/database/
# .parent.parent -> <project_root>/
_DB_DIR = pathlib.Path(__file__).parent          # database/
_PROJECT_ROOT = _DB_DIR.parent                   # MiniSwiggy/

# The actual database file lives at the project root so it is easy
# to find and inspect with any SQLite GUI tool.
DB_PATH = _PROJECT_ROOT / "swiggy.db"

# Schema file is in the same directory as this module.
SCHEMA_PATH = _DB_DIR / "schema.sql"


# -------------------------------------------------------------------
# Connection factory
# -------------------------------------------------------------------
def get_connection() -> sqlite3.Connection:
    """
    Open (or create) swiggy.db and return a configured connection.

    Configuration applied on every connection:
      - PRAGMA foreign_keys = ON   → enforces FK constraints
      - row_factory = sqlite3.Row  → rows behave like dicts (row['col'])
    """
    conn = sqlite3.connect(DB_PATH)

    # Without this PRAGMA SQLite silently ignores foreign-key violations.
    # We turn it on every time because PRAGMA settings are per-connection.
    conn.execute("PRAGMA foreign_keys = ON;")

    # sqlite3.Row lets you access columns by name: row['email']
    # instead of by index: row[2]
    conn.row_factory = sqlite3.Row

    return conn


# -------------------------------------------------------------------
# Schema initialisation
# -------------------------------------------------------------------
def migrate_db() -> None:
    """
    Ensure existing tables have V3 constraints (e.g. order_status check constraint
    including 'ready_for_pickup' and 'rejected'). Safe and idempotent.
    """
    with get_connection() as conn:
        row = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='orders';"
        ).fetchone()
        if row and row["sql"] and "ready_for_pickup" not in row["sql"]:
            conn.execute("PRAGMA foreign_keys = OFF;")
            conn.execute("""
                CREATE TABLE IF NOT EXISTS orders_v3 (
                    order_id       INTEGER PRIMARY KEY AUTOINCREMENT,
                    customer_id    INTEGER NOT NULL
                                     REFERENCES users(user_id)
                                     ON DELETE RESTRICT,
                    restaurant_id  INTEGER NOT NULL
                                     REFERENCES restaurants(restaurant_id)
                                     ON DELETE RESTRICT,
                    total_amount   REAL    NOT NULL CHECK (total_amount >= 0),
                    order_status   TEXT    NOT NULL DEFAULT 'placed'
                                     CHECK (order_status IN (
                                         'placed', 'accepted', 'preparing',
                                         'ready', 'ready_for_pickup', 'out_for_delivery', 'delivered', 'cancelled', 'rejected'
                                     )),
                    payment_status TEXT    NOT NULL DEFAULT 'pending'
                                     CHECK (payment_status IN ('pending', 'paid', 'failed', 'refunded')),
                    created_at     TEXT    NOT NULL DEFAULT (datetime('now'))
                );
            """)
            conn.execute("""
                INSERT INTO orders_v3 (order_id, customer_id, restaurant_id, total_amount, order_status, payment_status, created_at)
                SELECT order_id, customer_id, restaurant_id, total_amount, order_status, payment_status, created_at
                FROM orders;
            """)
            conn.execute("DROP TABLE orders;")
            conn.execute("ALTER TABLE orders_v3 RENAME TO orders;")
            conn.execute("PRAGMA foreign_keys = ON;")
            print("[db] Migrated orders table to V3 schema.")

        # V3.2: Ensure refunds table exists for existing databases
        conn.execute("""
            CREATE TABLE IF NOT EXISTS refunds (
                refund_id       INTEGER PRIMARY KEY AUTOINCREMENT,
                order_id        INTEGER NOT NULL UNIQUE
                                  REFERENCES orders(order_id)
                                  ON DELETE RESTRICT,
                amount          REAL    NOT NULL CHECK (amount > 0),
                refund_status   TEXT    NOT NULL DEFAULT 'pending'
                                  CHECK (refund_status IN ('pending', 'processing', 'refunded', 'failed')),
                reason          TEXT    NOT NULL,
                transaction_ref TEXT    NOT NULL,
                attempt_count   INTEGER NOT NULL DEFAULT 1 CHECK (attempt_count >= 1),
                created_at      TEXT    NOT NULL DEFAULT (datetime('now')),
                updated_at      TEXT    NOT NULL DEFAULT (datetime('now'))
            );
        """)


def init_db() -> None:
    """
    Read schema.sql and execute it against swiggy.db.

    All CREATE TABLE statements use IF NOT EXISTS, so running
    init_db() more than once is safe – it will not drop existing data.
    """
    schema_sql = SCHEMA_PATH.read_text(encoding="utf-8")

    with get_connection() as conn:
        # executescript runs multiple SQL statements separated by semicolons.
        # It also implicitly commits any open transaction first.
        conn.executescript(schema_sql)

    migrate_db()
    print(f"[db] Database initialised -> {DB_PATH}")


# -------------------------------------------------------------------
# Quick self-test: run this file directly to verify the setup
# -------------------------------------------------------------------
if __name__ == "__main__":
    init_db()
    with get_connection() as conn:
        # sqlite_master is a built-in table that lists every object in the DB.
        tables = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name;"
        ).fetchall()
        print("[db] Tables found:")
        for row in tables:
            print(f"     {row['name']}")
