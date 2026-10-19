-- =============================================================
-- Mini Swiggy – Version 0: Database Schema
-- =============================================================
-- Enable foreign-key enforcement at the connection level before
-- running any DML.  (See db.py: PRAGMA foreign_keys = ON;)
-- =============================================================

-- ---------------------------------------------------------------
-- 1. users
--    Stores every human actor in the system.
--    role distinguishes Customers, Sellers, Delivery Partners,
--    and Administrators.
-- ---------------------------------------------------------------
CREATE TABLE IF NOT EXISTS users (
    user_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    name          TEXT    NOT NULL,
    email         TEXT    NOT NULL UNIQUE,          -- must be globally unique
    password_hash TEXT,                             -- NULL until auth is wired up (future version)
    role          TEXT    NOT NULL
                    CHECK (role IN ('customer', 'seller', 'delivery_partner', 'admin')),
    is_active     INTEGER NOT NULL DEFAULT 1        -- 1 = active, 0 = deactivated
                    CHECK (is_active IN (0, 1)),
    created_at    TEXT    NOT NULL DEFAULT (datetime('now'))
);

-- ---------------------------------------------------------------
-- 2. restaurants
--    Each restaurant is optionally owned by a seller (user).
--    approval_status tracks admin approval; is_open tracks
--    real-time availability.
-- ---------------------------------------------------------------
CREATE TABLE IF NOT EXISTS restaurants (
    restaurant_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    name              TEXT    NOT NULL,
    description       TEXT,
    owner_user_id     INTEGER                        -- NULL until seller accounts exist
                        REFERENCES users(user_id)
                        ON DELETE SET NULL,
    approval_status   TEXT    NOT NULL DEFAULT 'pending'
                        CHECK (approval_status IN ('pending', 'approved', 'rejected')),
    is_open           INTEGER NOT NULL DEFAULT 1     -- 1 = accepting orders
                        CHECK (is_open IN (0, 1))
);

-- ---------------------------------------------------------------
-- 3. menu_items
--    Belongs to one restaurant.  Price is stored in rupees as a
--    real number (REAL maps to IEEE-754 double in SQLite).
-- ---------------------------------------------------------------
CREATE TABLE IF NOT EXISTS menu_items (
    item_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    restaurant_id INTEGER NOT NULL
                    REFERENCES restaurants(restaurant_id)
                    ON DELETE CASCADE,
    name          TEXT    NOT NULL,
    description   TEXT,
    price         REAL    NOT NULL CHECK (price > 0),   -- must be positive
    is_available  INTEGER NOT NULL DEFAULT 1
                    CHECK (is_available IN (0, 1))
);

-- ---------------------------------------------------------------
-- 4. orders
--    A customer's request to a single restaurant.
--    total_amount is denormalised for quick retrieval; it equals
--    the sum of (quantity × unit_price) across order_items.
-- ---------------------------------------------------------------
CREATE TABLE IF NOT EXISTS orders (
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
                         'ready', 'out_for_delivery', 'delivered', 'cancelled'
                     )),
    payment_status TEXT    NOT NULL DEFAULT 'pending'
                     CHECK (payment_status IN ('pending', 'paid', 'failed', 'refunded')),
    created_at     TEXT    NOT NULL DEFAULT (datetime('now'))
);

-- ---------------------------------------------------------------
-- 5. order_items
--    Line items for an order.  unit_price is snapshotted at order
--    time so menu price changes don't alter historical records.
-- ---------------------------------------------------------------
CREATE TABLE IF NOT EXISTS order_items (
    order_item_id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id      INTEGER NOT NULL
                    REFERENCES orders(order_id)
                    ON DELETE CASCADE,
    item_id       INTEGER NOT NULL
                    REFERENCES menu_items(item_id)
                    ON DELETE RESTRICT,
    quantity      INTEGER NOT NULL CHECK (quantity > 0),   -- at least 1
    unit_price    REAL    NOT NULL CHECK (unit_price > 0)  -- snapshot of price
);

-- ---------------------------------------------------------------
-- 6. payments
--    One payment record per order.  transaction_ref will hold the
--    gateway reference in a future version; NULL for now.
-- ---------------------------------------------------------------
CREATE TABLE IF NOT EXISTS payments (
    payment_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id        INTEGER NOT NULL UNIQUE           -- one payment per order
                      REFERENCES orders(order_id)
                      ON DELETE CASCADE,
    amount          REAL    NOT NULL CHECK (amount >= 0),
    payment_status  TEXT    NOT NULL DEFAULT 'pending'
                      CHECK (payment_status IN ('pending', 'paid', 'failed', 'refunded')),
    transaction_ref TEXT,                             -- gateway reference (future)
    created_at      TEXT    NOT NULL DEFAULT (datetime('now'))
);

-- ---------------------------------------------------------------
-- 7. delivery_partners
--    Extends a user whose role = 'delivery_partner' with
--    operational availability info.
-- ---------------------------------------------------------------
CREATE TABLE IF NOT EXISTS delivery_partners (
    partner_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id       INTEGER NOT NULL UNIQUE             -- one profile per user
                    REFERENCES users(user_id)
                    ON DELETE CASCADE,
    is_available  INTEGER NOT NULL DEFAULT 1
                    CHECK (is_available IN (0, 1))
);

-- ---------------------------------------------------------------
-- 8. deliveries
--    Tracks a single delivery assignment from partner to customer.
--    assigned_at is set when the partner is assigned;
--    completed_at is set when delivery is confirmed.
-- ---------------------------------------------------------------
CREATE TABLE IF NOT EXISTS deliveries (
    delivery_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id         INTEGER NOT NULL UNIQUE          -- one delivery per order
                       REFERENCES orders(order_id)
                       ON DELETE CASCADE,
    partner_id       INTEGER NOT NULL
                       REFERENCES delivery_partners(partner_id)
                       ON DELETE RESTRICT,
    delivery_status  TEXT    NOT NULL DEFAULT 'assigned'
                       CHECK (delivery_status IN (
                           'assigned', 'picked_up', 'delivered', 'failed'
                       )),
    assigned_at      TEXT    NOT NULL DEFAULT (datetime('now')),
    completed_at     TEXT                             -- NULL until delivered
);
