"""
main.py
=======
Mini Swiggy – Version 0 entry point.

Usage (Windows PowerShell):

    # 1. Initialise the database (creates swiggy.db and all tables)
    python main.py init

    # 2. Seed sample restaurants and menu items
    python main.py seed

    # 3. Print all restaurants with their menus
    python main.py show

    # 4. Run all three steps in sequence
    python main.py all

Version 0 contains only database setup and seed data.
TCP networking, user authentication, and microservices will be
introduced in later versions.
"""

import sys
from database.db import get_connection, init_db
from database.seed import seed_data


def show_menu() -> None:
    """
    Print all restaurants and their menu items to the terminal.

    This is a simple read-only query – no business logic here.
    """
    with get_connection() as conn:
        restaurants = conn.execute(
            """
            SELECT restaurant_id, name, description, approval_status, is_open
            FROM restaurants
            ORDER BY restaurant_id;
            """
        ).fetchall()

        if not restaurants:
            print("No restaurants found. Run: python main.py seed")
            return

        print("\n" + "=" * 60)
        print("  Mini Swiggy - Restaurant & Menu Directory")
        print("=" * 60)

        for r in restaurants:
            status = "Open" if r["is_open"] else "Closed"
            approved = r["approval_status"].capitalize()
            print(f"\n[{approved} | {status}]  {r['name']}")
            if r["description"]:
                print(f"   {r['description']}")
            print(f"   {'-' * 50}")

            items = conn.execute(
                """
                SELECT name, description, price, is_available
                FROM menu_items
                WHERE restaurant_id = ?
                ORDER BY item_id;
                """,
                (r["restaurant_id"],),
            ).fetchall()

            if not items:
                print("   (No menu items)")
            else:
                for item in items:
                    avail = "[v]" if item["is_available"] else "[x]"
                    print(
                        f"   {avail}  {item['name']:<30} Rs {item['price']:.2f}"
                    )
                    if item["description"]:
                        print(f"       {item['description']}")

        print("\n" + "=" * 60 + "\n")


def print_usage() -> None:
    print(__doc__)


# -------------------------------------------------------------------
# CLI dispatcher
# -------------------------------------------------------------------
COMMANDS = {
    "init": lambda: init_db(),
    "seed": lambda: (init_db(), seed_data()),
    "show": show_menu,
    "all": lambda: (init_db(), seed_data(), show_menu()),
}

if __name__ == "__main__":
    if len(sys.argv) < 2 or sys.argv[1] not in COMMANDS:
        print_usage()
        sys.exit(1)

    COMMANDS[sys.argv[1]]()
