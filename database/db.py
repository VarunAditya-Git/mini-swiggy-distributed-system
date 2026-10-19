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
def init_db() -> None:
    """
    Read schema.sql and execute it against swiggy.db.

    All CREATE TABLE statements use  IF NOT EXISTS,  so running
    init_db() more than once is safe – it will not drop existing data.
    """
    schema_sql = SCHEMA_PATH.read_text(encoding="utf-8")

    with get_connection() as conn:
        # executescript runs multiple SQL statements separated by semicolons.
        # It also implicitly commits any open transaction first.
        conn.executescript(schema_sql)

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
