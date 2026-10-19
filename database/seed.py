"""
database/seed.py
================
Populates swiggy.db with a fixed set of sample data.

Rules applied to keep seeding IDEMPOTENT (safe to run many times):
  - We check for existence before inserting restaurants or menu items.
  - INSERT OR IGNORE is used for users so duplicate e-mails are skipped.
  - All related inserts happen inside a single transaction so we never
    end up with half-seeded data if something fails midway.

Idempotent means: running this script twice produces the same database
state as running it once – no duplicates.
"""

from database.db import get_connection, init_db


# -------------------------------------------------------------------
# Sample data definitions
# -------------------------------------------------------------------

# Each user dict matches the columns in the users table.
# password_hash is None – authentication is out of scope for Version 0.
# In a real system you would NEVER store a plaintext password.
SAMPLE_USERS = [
    {
        "name": "Alice Customer",
        "email": "alice@example.com",
        "role": "customer",
    },
    {
        "name": "Bob Seller",
        "email": "bob@example.com",
        "role": "seller",
    },
    {
        "name": "Carol Delivery",
        "email": "carol@example.com",
        "role": "delivery_partner",
    },
    {
        "name": "Dave Admin",
        "email": "dave@example.com",
        "role": "admin",
    },
]

# Each restaurant dict has a 'menu' key that holds its menu items.
# Prices are in Indian Rupees (₹).
SAMPLE_RESTAURANTS = [
    {
        "name": "Hyderabad Spice House",
        "description": "Authentic Hyderabadi cuisine – famous for Dum Biryani.",
        "approval_status": "approved",
        "menu": [
            {
                "name": "Chicken Biryani",
                "description": "Slow-cooked Dum Biryani with tender chicken pieces.",
                "price": 180.0,
            },
            {
                "name": "Veg Biryani",
                "description": "Aromatic basmati rice with fresh vegetables.",
                "price": 120.0,
            },
        ],
    },
    {
        "name": "Dosa Corner",
        "description": "South Indian breakfast and snack specialities.",
        "approval_status": "approved",
        "menu": [
            {
                "name": "Masala Dosa",
                "description": "Crispy dosa stuffed with spiced potato filling.",
                "price": 90.0,
            },
            {
                "name": "Idli Sambar",
                "description": "Soft steamed idlis served with hot sambar and chutney.",
                "price": 60.0,
            },
        ],
    },
    {
        "name": "Pizza Junction",
        "description": "Italian-inspired pizzas with an Indian twist.",
        "approval_status": "approved",
        "menu": [
            {
                "name": "Paneer Pizza",
                "description": "Loaded with cottage cheese, capsicum, and onions.",
                "price": 220.0,
            },
            {
                "name": "Margherita Pizza",
                "description": "Classic tomato base, mozzarella, and fresh basil.",
                "price": 180.0,
            },
        ],
    },
]


# -------------------------------------------------------------------
# Helper: insert a restaurant and its menu items (idempotent)
# -------------------------------------------------------------------
def _seed_restaurant(conn: object, restaurant: dict) -> None:
    """
    Insert one restaurant and its menu items, skipping if they already
    exist (checked by name).

    Args:
        conn: An open sqlite3 connection.
        restaurant: A dict matching SAMPLE_RESTAURANTS format.
    """
    # Check if this restaurant already exists (match by name).
    existing = conn.execute(
        "SELECT restaurant_id FROM restaurants WHERE name = ?;",
        (restaurant["name"],),
    ).fetchone()

    if existing:
        restaurant_id = existing["restaurant_id"]
        print(f"  [seed] Restaurant already exists: '{restaurant['name']}' (id={restaurant_id})")
    else:
        cursor = conn.execute(
            """
            INSERT INTO restaurants (name, description, approval_status)
            VALUES (?, ?, ?);
            """,
            (
                restaurant["name"],
                restaurant["description"],
                restaurant["approval_status"],
            ),
        )
        restaurant_id = cursor.lastrowid
        print(f"  [seed] Inserted restaurant: '{restaurant['name']}' (id={restaurant_id})")

    # Seed menu items for this restaurant.
    for item in restaurant["menu"]:
        _seed_menu_item(conn, restaurant_id, item)


def _seed_menu_item(conn: object, restaurant_id: int, item: dict) -> None:
    """
    Insert one menu item for a restaurant, skipping if it already exists
    (matched by restaurant_id + item name).

    Args:
        conn: An open sqlite3 connection.
        restaurant_id: The parent restaurant's ID.
        item: A dict with keys name, description, price.
    """
    existing = conn.execute(
        "SELECT item_id FROM menu_items WHERE restaurant_id = ? AND name = ?;",
        (restaurant_id, item["name"]),
    ).fetchone()

    if existing:
        print(f"    [seed] Menu item already exists: '{item['name']}' (id={existing['item_id']})")
    else:
        cursor = conn.execute(
            """
            INSERT INTO menu_items (restaurant_id, name, description, price)
            VALUES (?, ?, ?, ?);
            """,
            (restaurant_id, item["name"], item["description"], item["price"]),
        )
        print(f"    [seed] Inserted menu item: '{item['name']}' Rs {item['price']} (id={cursor.lastrowid})")


# -------------------------------------------------------------------
# Main seed function
# -------------------------------------------------------------------
def seed_data() -> None:
    """
    Insert all sample users, restaurants, and menu items.

    Uses a single transaction so the database is never left in a
    partially-seeded state if something goes wrong.
    """
    print("[seed] Starting seed …")

    with get_connection() as conn:
        # ----------------------------------------------------------
        # Seed sample users
        # INSERT OR IGNORE skips rows that violate the UNIQUE(email)
        # constraint instead of raising an error.
        # ----------------------------------------------------------
        for user in SAMPLE_USERS:
            conn.execute(
                """
                INSERT OR IGNORE INTO users (name, email, password_hash, role)
                VALUES (?, ?, NULL, ?);
                """,
                (user["name"], user["email"], user["role"]),
            )

        # ----------------------------------------------------------
        # Seed restaurants and their menu items
        # ----------------------------------------------------------
        for restaurant in SAMPLE_RESTAURANTS:
            _seed_restaurant(conn, restaurant)

    print("[seed] Done.")


# -------------------------------------------------------------------
# Entry point – run directly or via main.py
# -------------------------------------------------------------------
if __name__ == "__main__":
    # Make sure the schema exists before seeding.
    init_db()
    seed_data()
