# Mini Swiggy — Architecture Notes (Version 0)

> **Version 0 is the database foundation only.**
> There is no networking, no authentication, no payment processing,
> and no distributed components yet. Those will be introduced in
> later versions step by step.

---

## Component map

```
MiniSwiggy/
├── main.py              ← CLI entry point
├── database/
│   ├── db.py            ← Connection factory + schema loader
│   ├── schema.sql       ← All eight table definitions
│   └── seed.py          ← Sample restaurants and menu items
└── tests/
    └── test_schema.py   ← Automated constraint and seed tests
```

---

## Database — eight tables and their relationships

```
┌──────────────────────────────────────────────────────────┐
│                         users                            │
│  user_id (PK) | name | email (UNIQUE) | role | is_active │
└──────────────┬───────────────────────────────────────────┘
               │ owner_user_id (FK, nullable)
               ▼
┌──────────────────────────────────────┐
│            restaurants               │
│  restaurant_id (PK) | name |         │
│  approval_status | is_open           │
└──────────────┬───────────────────────┘
               │ restaurant_id (FK)
               ▼
┌──────────────────────────────────────┐
│            menu_items                │
│  item_id (PK) | name | price > 0 |  │
│  is_available                        │
└──────────────────────────────────────┘

users (customer_id) ──────────────────────────────────────┐
restaurants (restaurant_id) ──────────────────────────┐    │
                                                      ▼    ▼
                                          ┌────────────────────────┐
                                          │         orders          │
                                          │  order_id (PK)          │
                                          │  total_amount           │
                                          │  order_status           │
                                          │  payment_status         │
                                          └───────┬────────┬────────┘
                                                  │        │
                              order_id (FK) ──────┘        └── order_id (FK, UNIQUE)
                                   │                                   │
                    ┌──────────────▼──────────┐     ┌─────────────────▼───────┐
                    │       order_items        │     │        payments          │
                    │  order_item_id (PK)      │     │  payment_id (PK)         │
                    │  item_id (FK)            │     │  amount                  │
                    │  quantity > 0            │     │  payment_status          │
                    │  unit_price (snapshot)   │     │  transaction_ref (NULL)  │
                    └──────────────────────────┘     └──────────────────────────┘

users (delivery_partner role)
         │ user_id (FK, UNIQUE)
         ▼
┌────────────────────────────────┐
│       delivery_partners         │
│  partner_id (PK) | is_available │
└──────────────┬──────────────────┘
               │ partner_id (FK)
               ▼
┌───────────────────────────────────┐
│           deliveries              │
│  delivery_id (PK)                 │
│  order_id (FK, UNIQUE)            │
│  delivery_status                  │
│  assigned_at | completed_at (NULL)│
└───────────────────────────────────┘
```

---

## Table-by-table explanation

### 1. `users`
The single source of truth for all human actors.
- **role** is one of: `customer`, `seller`, `delivery_partner`, `admin`
- `password_hash` is NULL in V0 — authentication is not implemented yet
- `email` has a UNIQUE constraint so no two accounts share an address

### 2. `restaurants`
Restaurants listed on the platform.
- `owner_user_id` → `users.user_id` (optional in V0, no seller accounts yet)
- `approval_status` tracks whether an admin has approved the restaurant
- `is_open` can be toggled to pause ordering without deleting the restaurant

### 3. `menu_items`
Food items belonging to a restaurant.
- `price` has a `CHECK (price > 0)` — free or negative prices are rejected by the DB
- Deleting a restaurant cascades and deletes all its menu items (`ON DELETE CASCADE`)

### 4. `orders`
A customer's purchase request from one restaurant.
- `total_amount` is stored (denormalised) so dashboards can read it without summing line items
- `order_status` follows a lifecycle: `placed → accepted → preparing → ready → out_for_delivery → delivered`
- `payment_status` mirrors the payment gateway state (V0: always `pending`)

### 5. `order_items`
Individual line items (what was ordered).
- `unit_price` is a **snapshot** of the price at order time — if a restaurant later changes its menu price, historical orders remain accurate
- `quantity` has a `CHECK (quantity > 0)` — you can't order zero items

### 6. `payments`
One payment record per order.
- `UNIQUE(order_id)` ensures no accidental double-payment rows
- `transaction_ref` is NULL in V0 — payment gateway integration is a future version

### 7. `delivery_partners`
Extends a user (role = `delivery_partner`) with operational information.
- `UNIQUE(user_id)` — one operational profile per person
- `is_available` can be set to 0 when the partner goes off duty

### 8. `deliveries`
Tracks an assignment of a partner to an order.
- `UNIQUE(order_id)` — one delivery per order
- `completed_at` is NULL until the delivery is confirmed; its presence signals completion

---

## Key engineering decisions

| Decision | Reason |
|----------|--------|
| `PRAGMA foreign_keys = ON` on every connection | SQLite disables FK enforcement by default; enabling it catches bugs early |
| `IF NOT EXISTS` on every `CREATE TABLE` | Makes `init_db()` safe to call multiple times |
| Idempotent seeding (check before insert) | Running `seed.py` twice cannot create duplicate restaurants |
| `unit_price` snapshot in `order_items` | Preserves historical accuracy independent of future menu changes |
| Parameterised queries everywhere (`?`) | Prevents SQL injection even in an educational codebase |
| `password_hash` column is NULL | Reminds that auth is planned; no plaintext passwords are ever stored |
| `swiggy.db` excluded from Git | Binary database files cause large unreadable diffs; recreate with one command |

---

## Planned version roadmap

| Version | Topic |
|---------|-------|
| **0 ✅** | Project structure, SQLite schema, seed data, automated tests |
| 1 | Python TCP sockets, single-server request handling |
| 2 | Concurrent clients using threading |
| 3 | Independent networked services |
| 4+ | Messaging, fault handling, distributed systems concepts |
