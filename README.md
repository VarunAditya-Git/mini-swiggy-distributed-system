# Mini Swiggy — Distributed Food Delivery System

> **Current branch: `feature/v1-tcp-client-server`**
> V0 (database foundation) is on `main`.
> V1 (TCP client–server) is being developed on this branch.

---

## What this project is

Mini Swiggy is an **educational, incremental** food-delivery application built
to teach Python and distributed-systems concepts one layer at a time.

| Version | Branch | Scope |
|---------|--------|-------|
| **V0 ✅** | `main` | Project structure · SQLite schema · Seed data · Automated tests |
| **V1 (this)** | `feature/v1-tcp-client-server` | TCP sockets · Newline-delimited JSON · Client–server communication |
| V2 (planned) | — | Concurrent clients (threading) |
| V3 (planned) | — | Independent networked services |
| V4+ (planned) | — | Messaging · Fault handling · Distributed concepts |

> **V1 is not a fully distributed system.** The client and server run on the
> same machine and communicate over the loopback interface (127.0.0.1).
> True distribution (separate machines, network partitions, consensus) comes later.

---

## Technology stack

| Tool | Purpose |
|------|---------|
| Python 3.11+ | Application language |
| `socket` (stdlib) | TCP networking |
| `json` (stdlib) | Message serialisation |
| SQLite (`sqlite3` stdlib) | Persistent local database |
| `pytest` | Automated testing |

No external Python packages are required beyond `pytest`.

---

## Project structure

```
MiniSwiggy/
├── .gitignore               ← Excludes swiggy.db, __pycache__, secrets
├── README.md                ← This file
├── main.py                  ← V0 CLI (init / seed / show / all)
│
├── database/
│   ├── __init__.py
│   ├── db.py                ← Connection factory + init_db()
│   ├── schema.sql           ← 8-table SQLite schema
│   └── seed.py              ← Idempotent seed data (3 restaurants, 6 items)
│
├── server/
│   ├── __init__.py
│   ├── tcp_server.py        ← V1: TCP server (single-client, sequential)
│   └── request_handler.py  ← V1: Validates requests, queries SQLite, builds responses
│
├── clients/
│   ├── __init__.py
│   └── tcp_client.py       ← V1: TCP client library + CLI demo
│
├── services/
│   └── __init__.py          ← Reserved for V3 microservices
│
├── docs/
│   ├── ARCHITECTURE.md      ← Database design, table relationships
│   └── V1_TCP_PROTOCOL.md  ← V1 request/response format, framing, error codes
│
└── tests/
    ├── test_schema.py        ← V0: 11 database schema and seed tests
    ├── test_request_handler.py ← V1: 20 unit tests for the request handler
    └── test_tcp_server.py   ← V1: 15 integration tests over real TCP sockets
```

> `swiggy.db` is **not committed** — recreate with `python main.py all`.

---

## Requirements

- **Python 3.11 or newer** — check with `python --version`
- **pytest** — `pip install pytest`

---

## Windows PowerShell — Complete command reference

> Run all commands from inside the `MiniSwiggy/` project folder.

### Database setup (V0 — same as before)

```powershell
# Create swiggy.db, all tables, and seed sample data in one step:
python main.py all

# Or step by step:
python main.py init    # create tables
python main.py seed    # insert sample restaurants and menu items
python main.py show    # print restaurants and menus to the terminal
```

### Start the TCP server (V1)

Open **Terminal 1** and run:

```powershell
python -m server.tcp_server
```

Expected output:
```
[server] Mini Swiggy V1 TCP server listening on 127.0.0.1:5000
[server] Press Ctrl+C to stop.
```

Custom host/port:
```powershell
python -m server.tcp_server --host 127.0.0.1 --port 6000
```

### Use the TCP client (V1)

Open **Terminal 2** (server must be running in Terminal 1):

```powershell
# Health check
python -m clients.tcp_client ping

# List all restaurants (reads from SQLite)
python -m clients.tcp_client list_restaurants

# List menu for restaurant ID 1
python -m clients.tcp_client list_menu 1

# List menu for restaurant ID 2
python -m clients.tcp_client list_menu 2

# List menu for restaurant ID 3
python -m clients.tcp_client list_menu 3

# Custom host/port
python -m clients.tcp_client --host 127.0.0.1 --port 6000 ping
```

### Run all tests

```powershell
# All tests (V0 + V1)
python -m pytest tests/ -v

# V0 schema tests only
python -m pytest tests/test_schema.py -v

# V1 request handler tests (no server needed)
python -m pytest tests/test_request_handler.py -v

# V1 TCP integration tests (server starts in background thread)
python -m pytest tests/test_tcp_server.py -v
```

---

## How a request travels end to end

```
[Client]                [Network]               [Server]
  |                                                 |
  | SwiggyClient.send("list_menu", {restaurant_id: 1})
  |                                                 |
  | Serialise to JSON + "\n"                        |
  | {"request_id":"r1","action":"list_menu","payload":{"restaurant_id":1}}\n
  |                                                 |
  |------------ sendall(bytes) ------------------->  |
  |              (TCP stream)                       |
  |                                           recv() + buffer
  |                                           finds "\n" → complete message
  |                                           json.loads(line)
  |                                           handle_request(raw_dict)
  |                                           → list_menu handler
  |                                           → SELECT from menu_items WHERE restaurant_id=1
  |                                           → build response dict
  |                                           json.dumps(response) + "\n"
  |<----------- sendall(response bytes) -----------  |
  |                                                  |
  | _read_response(): recv() loop until "\n"         |
  | json.loads(line_bytes)                           |
  | return dict to caller                            |
```

---

## Sample client output

### ping
```json
{
  "request_id": "a1b2c3d4",
  "ok": true,
  "data": { "status": "ok" }
}
```

### list_restaurants (excerpt)
```json
{
  "request_id": "a1b2c3d4",
  "ok": true,
  "data": [
    { "restaurant_id": 1, "name": "Hyderabad Spice House", "is_open": true, ... },
    { "restaurant_id": 2, "name": "Dosa Corner", "is_open": true, ... },
    { "restaurant_id": 3, "name": "Pizza Junction", "is_open": true, ... }
  ]
}
```

### list_menu (restaurant_id=1)
```json
{
  "request_id": "a1b2c3d4",
  "ok": true,
  "data": [
    { "item_id": 1, "name": "Chicken Biryani", "price": 180.0, "is_available": true },
    { "item_id": 2, "name": "Veg Biryani",     "price": 120.0, "is_available": true }
  ]
}
```

---

## Database tables

| Table | What it stores |
|-------|----------------|
| `users` | All actors: customers, sellers, delivery partners, admins |
| `restaurants` | Restaurants listed on the platform |
| `menu_items` | Food items for each restaurant |
| `orders` | A customer's purchase from one restaurant |
| `order_items` | Line items in an order |
| `payments` | One payment record per order |
| `delivery_partners` | Operational profile for delivery users |
| `deliveries` | Assignment of a partner to an order |

See [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) and
[`docs/V1_TCP_PROTOCOL.md`](docs/V1_TCP_PROTOCOL.md) for full details.

---

## V1 limitations

- **One client at a time.** A second client must wait until the first disconnects.
  Concurrent client support (threading) is planned for V2.
- **No authentication.** Any client can call any action.
- **Read-only in V1.** list_restaurants and list_menu only. No order placement yet.
- **Loopback only by default.** The server binds to 127.0.0.1 and is not reachable
  from other machines without changing the `--host` flag.
- **Not a distributed system yet.** Microservices, network partitions, and distributed
  consensus are future milestones.

---

## Recreating the database from scratch

```powershell
# Delete swiggy.db if it exists, then:
python main.py all
```

---

## Contributing

Educational project built step by step. Pull requests welcome for bug fixes and
documentation improvements. Please open an issue before starting large feature work.

---

## License

MIT — free to use and learn from.
