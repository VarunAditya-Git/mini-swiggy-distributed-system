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
| **V3** | **Complete** | **Seller Dashboard & Restaurant Management System** · Multi-tenant kitchen management · Order lifecycle state machine · Real-time menu management & availability · 52 new tests (**164 total**) |
| **V4** (planned) | Future | Independent networked microservices (Restaurant, Ordering, Payment, Delivery) |
| **V5+** (planned) | Future | Asynchronous messaging, caching, fault tolerance, and consensus |

---

## Technology Stack

| Component | Technology | Purpose |
|---|---|---|
| Language | Python 3.11+ | Clean, readable standard library implementations |
| Networking | `socket` (stdlib) | Raw TCP transport and low-level stream framing |
| Serialization | `json` (stdlib) | Newline-delimited JSON (NDJSON) protocol |
| Web Gateway | `http.server` (stdlib) | Non-blocking ThreadingHTTPServer bridging HTTP to TCP |
| Frontend | Vanilla HTML5, CSS3, JS | Modern, responsive Indian food-delivery & kitchen partner interfaces |
| Storage | SQLite (`sqlite3` stdlib) | Relational persistence with foreign keys and ACID transactions |
| Testing | `pytest` | Comprehensive unit and real TCP integration testing (**164 automated tests**) |

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

### 4. Start the Web Interface (Browser Experience)

To experience the modern customer-facing web UI:

1. **Terminal 1** (TCP Server):
   ```powershell
   python -m server.tcp_server
   ```
2. **Terminal 2** (HTTP Gateway):
   ```powershell
   python -m gateway.http_gateway
   ```
3. **Open in Browser**:
   ```
   http://127.0.0.1:8000
   ```
   *The website communicates with the HTTP gateway, which forwards NDJSON requests to the backend TCP socket server.*

See [`docs/WEB_INTERFACE.md`](docs/WEB_INTERFACE.md) for full architectural documentation.

---

## How a TCP Request Becomes a Saved Food Order

For beginners learning distributed systems, here is step-by-step what happens under the hood:

```
[Customer Client]                                                    [TCP Server & SQLite DB]
       │                                                                        │
       ├─ 1. Build JSON payload: customer_id, restaurant_id, items              │
       ├─ 2. Append '\n' delimiter to mark end of message                       │
       ├─ 3. Send raw UTF-8 byte stream across TCP socket ────────────────────> │
       │                                                                        ├─ 4. Accumulate chunks in buffer until '\n'
       │                                                                        ├─ 5. Parse JSON into Python dict
       │                                                                        ├─ 6. Validate customer, restaurant, availability
       │                                                                        ├─ 7. Fetch official item prices from SQLite
       │                                                                        │     (never trust prices sent by client!)
       │                                                                        ├─ 8. Calculate total amount on server
       │                                                                        ├─ 9. BEGIN TRANSACTION:
       │                                                                        │     - INSERT into orders table
       │                                                                        │     - INSERT into order_items (unit price snapshot)
       │                                                                        │     - COMMIT TRANSACTION (atomic rollback on error)
       │                                                                        ├─ 10. Format success response JSON + '\n'
       │ <───────────────── Receive raw UTF-8 byte stream ──────────────────────┤
       ├─ 11. Read buffer until '\n', parse JSON response, and print order ID   │
```

### Core Design Decisions in V2:
1. **Never Trust Client Prices**: The client only sends `item_id` and `quantity`. The server retrieves `price` directly from the database to prevent client tampering.
2. **Order-Time Price Snapshotting**: Restaurants may update their food prices later. To preserve historical and accounting accuracy, the unit price at the moment of order creation is stored in `order_items.unit_price`.
3. **Database Transaction Atomicity**: Orders and their line items are inserted within a single SQLite transaction block (`with conn:`). If any item fails, the entire transaction rolls back—leaving no orphan or partial orders in the database.
4. **Idempotent Payment Protection**: Once an order has `payment_status = 'paid'`, subsequent payment attempts are rejected with `PAYMENT_ALREADY_COMPLETED`, protecting users from double-billing.

---

## Running the Automated Test Suite

Every feature in V0, V1, and V2 is backed by automated tests:

```powershell
# Run the entire test suite (94 tests)
python -m pytest tests/ -v

# Run V2 unit tests only (31 tests)
python -m pytest tests/test_v2_order_handler.py -v

# Run V2 TCP socket tests only (10 tests)
python -m pytest tests/test_v2_tcp.py -v
```

### Exact Test Suite Results:
- **Total collected**: `94`
- **Passed**: `94`
- **Failed**: `0`
- **Skipped**: `0`

---

## Version 2 Limitations & Roadmap

- **Single-Threaded Server**: The server currently handles one client connection at a time. Concurrency with Python threads or worker pools will be introduced in V3.
- **Simulated Payments**: Payments are validated and persisted locally without external third-party payment gateways (Razorpay, Stripe).
- **Single-Node SQLite**: Data is persisted in a local SQLite file rather than a distributed multi-node database cluster.
- **No Background Delivery Worker**: Delivery assignment and tracking will be automated in subsequent versions.

---

## License

MIT — Free for educational use and experimentation.

