# Mini Swiggy — Distributed Food Delivery System

> **Version 0 — Database Foundation**
> This version is **not** a distributed system yet.
> Version 0 establishes the project structure and the SQLite database
> that all future versions will build on.

---

## What this project is

Mini Swiggy is an **educational, incremental** food-delivery application
built to teach Python and distributed-systems concepts one layer at a time.

| Version | Scope |
|---------|-------|
| **V0 ✅ (this)** | Project structure · SQLite schema · Seed data · Automated tests |
| V1 (planned) | Python TCP sockets · Single-server request handling |
| V2 (planned) | Concurrent clients with threading |
| V3 (planned) | Independent networked services |
| V4+ (planned) | Messaging · Fault handling · Distributed concepts |

No networking, authentication, payment processing, microservices, or
distributed components have been implemented yet. The README will be
updated as each version is completed.

---

## Technology stack

| Tool | Purpose |
|------|---------|
| Python 3.11+ | Application language |
| SQLite (built-in `sqlite3`) | Persistent local database |
| pytest | Automated testing |
| Git + GitHub | Version control |

No external Python packages are required beyond `pytest` for running tests.

---

## Project structure

```
MiniSwiggy/
├── .gitignore               ← Excludes generated files, secrets, caches
├── README.md                ← This file
├── main.py                  ← CLI entry point (init / seed / show / all)
│
├── database/
│   ├── __init__.py          ← Package marker; exports get_connection, init_db
│   ├── db.py                ← Connection factory + init_db()
│   ├── schema.sql           ← CREATE TABLE statements for all 8 tables
│   └── seed.py              ← Idempotent sample restaurants & menu items
│
├── server/
│   └── __init__.py          ← Reserved for V1 TCP server
├── clients/
│   └── __init__.py          ← Reserved for V1 client stubs
├── services/
│   └── __init__.py          ← Reserved for V3 microservices
├── frontend/                ← Reserved for future web UI
│
├── docs/
│   └── ARCHITECTURE.md      ← Table design, relationships, decisions
│
└── tests/
    └── test_schema.py       ← 11 automated tests for schema & constraints
```

> `swiggy.db` is **not committed to Git** — it is a generated binary file.
> Recreate it from scratch with the commands in the *Setup* section below.

---

## Requirements

- **Python 3.11 or newer** — check with `python --version`
- **pytest** — install with `pip install pytest`
- **SQLite3 CLI** *(optional)* — for manual database inspection

---

## Windows PowerShell setup

> Run all commands from inside the `MiniSwiggy/` project folder.

### Step 1 — Check Python version

```powershell
python --version
# Expected: Python 3.11.x or newer
```

### Step 2 — Install pytest

```powershell
pip install pytest
```

### Step 3 — Initialise the database

This creates `swiggy.db` and all eight tables.

```powershell
python main.py init
```

Expected output:
```
[db] Database initialised -> E:\Mini Swiggy\swiggy.db
```

### Step 4 — Seed sample data

Inserts 3 restaurants and 6 menu items.

```powershell
python main.py seed
```

### Step 5 — Print the restaurant and menu directory

```powershell
python main.py show
```

### Step 6 — Do all three steps at once (recommended for a fresh setup)

```powershell
python main.py all
```

### Step 7 — Verify idempotency (run seed twice; must produce no duplicates)

```powershell
python main.py seed
python main.py seed
python main.py show
# Still exactly 3 restaurants and 6 menu items
```

---

## Running the automated tests

```powershell
python -m pytest tests/ -v
```

### Expected output (11 tests, all passing)

```
tests/test_schema.py::test_all_tables_created                    PASSED
tests/test_schema.py::test_duplicate_email_rejected              PASSED
tests/test_schema.py::test_invalid_role_rejected                 PASSED
tests/test_schema.py::test_menu_item_fk_enforced                 PASSED
tests/test_schema.py::test_menu_item_price_must_be_positive[0]   PASSED
tests/test_schema.py::test_menu_item_price_must_be_positive[-10] PASSED
tests/test_schema.py::test_menu_item_price_must_be_positive[-0.01] PASSED
tests/test_schema.py::test_order_item_quantity_must_be_positive  PASSED
tests/test_schema.py::test_seed_is_idempotent                    PASSED
tests/test_schema.py::test_seed_restaurant_names                 PASSED
tests/test_schema.py::test_each_restaurant_has_two_items         PASSED

11 passed in ~0.6s
```

Each test uses a temporary, isolated database — the real `swiggy.db` is
never modified by the test suite.

---

## Inspecting the database manually

```powershell
# List all tables
sqlite3 swiggy.db ".tables"

# Show all restaurants
sqlite3 swiggy.db "SELECT * FROM restaurants;"

# Show all menu items with restaurant names
sqlite3 swiggy.db "SELECT r.name, m.name, m.price FROM menu_items m JOIN restaurants r ON m.restaurant_id = r.restaurant_id;"

# Show table schema
sqlite3 swiggy.db ".schema restaurants"
```

---

## Recreating the database from scratch

If you delete `swiggy.db`, recreate it in one command:

```powershell
python main.py all
```

This runs `init_db()` (creates tables) then `seed_data()` (inserts sample
restaurants and menu items) and finally prints the directory.

---

## Database tables at a glance

| Table | What it stores |
|-------|----------------|
| `users` | All actors: customers, sellers, delivery partners, admins |
| `restaurants` | Restaurants listed on the platform |
| `menu_items` | Food items for each restaurant (price > 0 enforced) |
| `orders` | A customer's purchase from one restaurant |
| `order_items` | Line items in an order (quantity > 0; price snapshot) |
| `payments` | One payment record per order |
| `delivery_partners` | Operational profile for delivery users |
| `deliveries` | Assignment of a partner to an order |

See [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) for the full entity-
relationship diagram, engineering decisions, and version roadmap.

---

## Sample seed data

| Restaurant | Item | Price |
|------------|------|-------|
| Hyderabad Spice House | Chicken Biryani | Rs 180 |
| Hyderabad Spice House | Veg Biryani | Rs 120 |
| Dosa Corner | Masala Dosa | Rs 90 |
| Dosa Corner | Idli Sambar | Rs 60 |
| Pizza Junction | Paneer Pizza | Rs 220 |
| Pizza Junction | Margherita Pizza | Rs 180 |

---

## What V0 does NOT include

- No TCP networking or socket programming
- No user authentication or login flows
- No payment gateway integration
- No web or mobile frontend
- No microservices or distributed components
- No fault tolerance or replication

These will be added in subsequent versions.

---

## Contributing

This is a personal educational project built step by step.
Pull requests are welcome for bug fixes and documentation improvements.
Please open an issue before starting any large feature work.

---

## License

MIT — free to use and learn from.
