# Mini Swiggy — Distributed Food Delivery System

> **Current branch: `feature/v3-seller-dashboard`**  
> V0 (database), V1 (TCP client-server), V2 (core food ordering), and Customer Web Interface are merged into `main`.  
> This branch implements **V3 — Seller Dashboard and Restaurant Management System**.

---

## What this project is

Mini Swiggy is an **educational, incremental** food-delivery platform designed to teach Python, socket programming, transactional database persistence, and distributed-systems fundamentals step by step.

| Version | Status | Scope |
|---|---|---|
| **V0** | Merged to `main` | Database foundation · SQLite schema (8 tables) · Seed data · 11 tests |
| **V1** | Merged to `main` | Raw TCP sockets · Newline-delimited JSON framing · Read-only actions · 42 tests |
| **V2** | Merged to `main` | Core Food Ordering · Atomic transactions · Price snapshots · Simulated payments · Order history · 41 tests |
| **Web Extension** | Merged to `main` | Customer Web Interface & HTTP Gateway · Modern web UI · REST API bridge · Single-restaurant cart · Receipt viewer · 18 tests |
| **V3** | **Complete** | **Seller Dashboard & Restaurant Management System** · Multi-tenant kitchen management · Order lifecycle state machine · Real-time menu management & availability · 67 new tests (**179 total**) |
| **V4** (planned) | Future | Independent networked microservices (Restaurant, Ordering, Payment, Delivery) |
| **V5+** (planned) | Future | Asynchronous messaging, caching, fault tolerance, and consensus |

---

## Technology Stack

| Component | Technology | Purpose |
|---|---|---|
| Language | Python 3.11+ | Clean, readable standard library implementations |
| Networking | `socket` (stdlib) | Raw TCP transport and low-level stream framing |
| Serialization | `json` (stdlib) | Newline-delimited JSON (NDJSON) protocol |
| Web Gateway | `http.server` (stdlib) | Concurrent ThreadingHTTPServer bridging HTTP to TCP |
| Frontend | Vanilla HTML5, CSS3, JS | Modern, responsive Indian food-delivery & kitchen partner interfaces |
| Storage | SQLite (`sqlite3` stdlib) | Relational persistence with foreign keys and ACID transactions |
| Testing | `pytest` | Comprehensive unit and real TCP integration testing (**179 automated tests**) |

---

## Project Structure

```
MiniSwiggy/
├── .gitignore                    ← Excludes swiggy.db, __pycache__, test artifacts
├── README.md                     ← Project overview, setup, and usage guide
├── main.py                       ← V0 CLI for database management (init/seed/show/all)
│
├── database/
│   ├── __init__.py
│   ├── db.py                     ← Connection factory with PRAGMA foreign_keys = ON & migrate_db()
│   ├── schema.sql                ← 8 relational tables with expanded V3 order_status lifecycle
│   └── seed.py                   ← Idempotent seed script (Hyderabad Spice House assigned to Bob Seller ID: 2)
│
├── server/
│   ├── __init__.py
│   ├── tcp_server.py             ← Raw TCP server handling NDJSON framing and timeouts
│   └── request_handler.py        ← Application logic: validation, zero-trust pricing, seller RBAC, lifecycle state machine
│
├── clients/
│   ├── __init__.py
│   └── tcp_client.py            ← Reusable SwiggyClient with V1, V2, and V3 seller methods
│
├── gateway/
│   ├── __init__.py
│   └── http_gateway.py          ← HTTP gateway translating REST GET/POST/PATCH to TCP NDJSON
│
├── web/
│   ├── index.html               ← Customer food ordering storefront
│   ├── styles.css               ← Swiggy-inspired visual design system
│   ├── app.js                   ← Customer client application logic & status rendering
│   ├── seller.html              ← Dedicated Restaurant Seller Dashboard (KDS)
│   ├── seller.css               ← Dark-themed kitchen partner stylesheet
│   └── seller.js                ← Seller client application logic & state transitions
│
├── docs/
│   ├── ARCHITECTURE.md           ← Architecture & schema relationship reference
│   ├── V1_TCP_PROTOCOL.md       ← V1 TCP protocol specification
│   ├── V2_PROTOCOL.md           ← Complete V2 protocol reference
│   ├── WEB_INTERFACE.md         ← Customer web interface and gateway documentation
│   └── V3_SELLER_DASHBOARD.md   ← Complete V3 Seller Dashboard and Kitchen Management specification
│
└── tests/
    ├── test_schema.py            ← V0: 11 database schema constraint & seed tests
    ├── test_request_handler.py    ← V1: 23 unit tests for read-only actions & framing errors
    ├── test_tcp_server.py        ← V1: 19 real TCP socket integration tests
    ├── test_v2_order_handler.py  ← V2: 31 unit tests for ordering, payment, rollback, and snapshots
    ├── test_v2_tcp.py            ← V2: 10 real TCP socket integration tests for V2 ordering flows
    ├── test_web_gateway.py       ← Web: 18 unit & integration tests for gateway and security guards
    ├── test_v3_seller_handler.py ← V3: 34 unit tests for seller auth, order transitions, menu CRUD
    ├── test_v3_tcp.py            ← V3: 5 integration tests for seller operations over raw TCP
    └── test_v3_gateway.py        ← V3: 13 integration tests for seller REST API endpoints & static routing
```

---

## Quickstart & PowerShell Command Reference

### 1. Database Setup
```powershell
# Create swiggy.db and seed sample restaurants, menus, and users:
python main.py all

# Inspect menu items in the terminal:
python main.py show
```

### 2. Start the TCP Server
Open **Terminal 1**:
```powershell
python -m server.tcp_server
```
Expected output:
```
[server] Mini Swiggy V1 TCP server listening on 127.0.0.1:5000
[server] Press Ctrl+C to stop.
```

### 3. Use the TCP Client (V1 & V2 Commands)
Open **Terminal 2** (while server is running):

#### Discovery Actions (V1)
```powershell
# Health check
python -m clients.tcp_client ping

# List all approved restaurants
python -m clients.tcp_client list_restaurants

# List menu items for restaurant ID 1
python -m clients.tcp_client list_menu 1
```

#### Ordering & Payment Actions (V2)
```powershell
# 1. Place an order (Customer 1 orders 2 Chicken Biryani [item 1] and 1 Veg Biryani [item 2] from Restaurant 1)
python -m clients.tcp_client create_order --customer-id 1 --restaurant-id 1 --items "1:2,2:1"

# 2. Simulate successful payment for order ID 1
python -m clients.tcp_client simulate_payment 1

# 3. Try to pay again for order ID 1 (verify idempotent duplicate charge protection)
python -m clients.tcp_client simulate_payment 1

# 4. Simulate a failed payment for a new order
python -m clients.tcp_client create_order --customer-id 1 --restaurant-id 1 --items "1:1"
python -m clients.tcp_client simulate_payment 2 --fail

# 5. Retrieve full order details (includes snapshot items and payment status)
python -m clients.tcp_client get_order 1

# 6. Retrieve order history for Customer 1 (newest orders first)
python -m clients.tcp_client get_order_history 1
```

#### Seller Kitchen Actions (V3)
```powershell
# 1. View seller profile and dashboard statistics (Bob Seller ID 2)
python -m clients.tcp_client get_seller_profile 2

# 2. View incoming restaurant orders (with optional status filtering)
python -m clients.tcp_client get_seller_orders 2
python -m clients.tcp_client get_seller_orders 2 --status pending

# 3. View individual order details with snapshotted items
python -m clients.tcp_client get_seller_order 2 1

# 4. Advance order lifecycle state (accepted -> preparing -> ready_for_pickup, or rejected)
python -m clients.tcp_client update_order_status 2 1 accepted
python -m clients.tcp_client update_order_status 2 1 preparing
python -m clients.tcp_client update_order_status 2 1 ready_for_pickup

# 5. Retrieve restaurant menu items
python -m clients.tcp_client get_seller_menu 2

# 6. Add a new menu item to seller's restaurant
python -m clients.tcp_client add_menu_item 2 --name "Paneer Tikka" --price 180.0 --description "Spiced skewers" --available 1

# 7. Edit an existing menu item (preserves historical order snapshots)
python -m clients.tcp_client update_menu_item 2 1 --price 195.0

# 8. Toggle item availability (immediately affects customer ordering)
python -m clients.tcp_client update_menu_item_availability 2 1 0
```

### 4. Start the Web Interfaces (Browser Experience)

To experience both the customer ordering storefront and the seller kitchen dashboard:

1. **Terminal 1 — Core TCP Server (Port 5000)**:
   ```powershell
   python -m server.tcp_server
   ```
2. **Terminal 2 — HTTP Web Gateway (Port 8000)**:
   ```powershell
   python -m gateway.http_gateway
   ```
3. **Open in Browser**:
   - **Customer Storefront**: [http://127.0.0.1:8000/](http://127.0.0.1:8000/) — Browse dishes, add to cart, checkout, and track real-time kitchen status.
   - **Seller Kitchen Dashboard**: [http://127.0.0.1:8000/seller](http://127.0.0.1:8000/seller) — Manage live incoming order queues, advance kitchen preparation, and update menus.

See [`docs/WEB_INTERFACE.md`](docs/WEB_INTERFACE.md) and [`docs/V3_SELLER_DASHBOARD.md`](docs/V3_SELLER_DASHBOARD.md) for full architectural documentation.

---

## How a TCP Request Becomes a Saved Food Order

For beginners learning distributed systems, here is step-by-step what happens under the hood:

```
[Customer / Seller Client]                                           [TCP Server & SQLite DB]
       │                                                                        │
       ├─ 1. Build JSON payload: action, seller_id / customer_id, data         │
       ├─ 2. Append '\n' delimiter to mark end of message                       │
       ├─ 3. Send raw UTF-8 byte stream across TCP socket ────────────────────> │
       │                                                                        ├─ 4. Accumulate chunks in buffer until '\n'
       │                                                                        ├─ 5. Parse JSON into Python dict
       │                                                                        ├─ 6. Validate actor role, restaurant ownership, availability
       │                                                                        ├─ 7. Execute state transition or price calculation
       │                                                                        ├─ 8. BEGIN TRANSACTION:
       │                                                                        │     - Perform atomic SQL inserts/updates
       │                                                                        │     - COMMIT TRANSACTION (atomic rollback on error)
       │                                                                        ├─ 9. Format response JSON + '\n'
       │ <───────────────── Receive raw UTF-8 byte stream ──────────────────────┤
       ├─ 10. Read buffer until '\n', parse JSON response                       │
```

### Core Design Decisions in V3:
1. **Zero-Trust Role-Based Access Control (RBAC)**: Backend validates restaurant ownership in SQLite (`restaurants.owner_user_id`). Sellers can only inspect or modify their own kitchen's orders and menus.
2. **Strict Lifecycle State Machine**: Finite-state transitions (`placed` $\rightarrow$ `accepted` $\rightarrow$ `preparing` $\rightarrow$ `ready_for_pickup` or `rejected`) prevent invalid or backwards status jumps.
3. **Order-Time Price Snapshotting**: Historical price consistency is preserved. Editing menu prices never alters line item records in past customer receipts.
4. **Idempotent Payment & Lifecycle Protection**: Once an order has `payment_status = 'paid'`, subsequent payment attempts are rejected. Payments cannot resurrect rejected orders or demote in-progress kitchen preparations.

---

## Running the Automated Test Suite

Every feature across V0, V1, V2, and V3 is backed by automated tests:

```powershell
# Run the entire test suite from root (179 tests)
python -m pytest

# Run V3 seller backend tests only
python -m pytest tests/test_v3_seller_handler.py -v

# Run V3 TCP integration tests only
python -m pytest tests/test_v3_tcp.py -v

# Run V3 Gateway integration tests only
python -m pytest tests/test_v3_gateway.py -v
```

### Exact Test Suite Results:
- **Total collected**: `179`
- **Passed**: `179`
- **Failed**: `0`
- **Skipped**: `0`

---

## Architecture, Limitations & Roadmap

- **Single-Threaded TCP Backend**: The authoritative TCP server (`server/tcp_server.py`) operates as a single-threaded sequential daemon accepting one connection per request. Concurrency is handled at Tier 2 by the HTTP Gateway's `ThreadingHTTPServer`, which multiplexes requests to the backend TCP socket.
- **Development-Only Seller Identity**: In V0–V3, cryptographic passwords, JWT tokens, and session cookies are intentionally out of scope for educational simplicity. Development user identities are supplied via parameters (`seller_id=2` for Bob Seller, owner of Hyderabad Spice House; `customer_id=1` for Alice Customer). Multi-tenant isolation and role checks are strictly enforced on the server, but production cryptographic authentication is planned for future versions.
- **Simulated Payments**: Payments are validated and persisted locally without external third-party payment gateways (Razorpay, Stripe).
- **Single-Node SQLite**: Data is persisted in a local SQLite file (`swiggy.db`) with foreign keys enforced.
- **No Background Delivery Worker**: Automated delivery partner dispatch and live GPS tracking will be introduced in V4.

---

## License

MIT — Free for educational use and experimentation.


