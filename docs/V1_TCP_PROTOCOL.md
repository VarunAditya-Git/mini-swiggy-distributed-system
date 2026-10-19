# Mini Swiggy V1 — TCP Protocol Reference

> **Version 1 adds real TCP client–server communication to the V0 database foundation.**
> This document is the authoritative reference for the newline-delimited JSON
> protocol used between the Mini Swiggy TCP client and server.

---

## Why TCP? Why not HTTP?

TCP (Transmission Control Protocol) is the building block that HTTP, WebSockets, and
most other protocols are built on. Learning it directly teaches:
- How to open and close network connections.
- Why message boundaries must be managed by the application.
- How bytes actually travel across a network.

HTTP would hide all of this behind library abstractions. We use raw TCP sockets
intentionally so every concept is visible.

---

## The fundamental challenge: TCP is a byte stream

When a client sends a 200-byte JSON message, the server might receive it as:
- One 200-byte chunk (common on localhost).
- Two 100-byte chunks.
- 200 one-byte chunks.
- Any other split.

**TCP does not preserve message boundaries.** A single `recv()` call is not
guaranteed to return exactly one application message.

---

## Our solution: Newline-delimited JSON (NDJSON)

Every request and every response is:
1. One JSON object serialised to a single line.
2. Immediately followed by `\n` (ASCII newline, byte `0x0A`).

```
{"request_id":"req-001","action":"ping","payload":{}}\n
```

The receiver buffers incoming bytes until it finds `\n`, then treats everything
before the newline as one complete JSON message.

### Rules

| Rule | Detail |
|------|--------|
| One JSON object per line | No pretty-printing across lines |
| Always end with `\n` | Required by both client and server |
| UTF-8 encoding | Both sides must encode and decode as UTF-8 |
| Max message size | 64 KiB (65 536 bytes) per message |
| Oversized messages | Server closes the connection after sending an error |

---

## Request format

```json
{
  "request_id": "req-001",
  "action":     "ping",
  "payload":    {}
}
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `request_id` | string | Yes | Client-chosen ID; echoed unchanged in the response |
| `action` | string | Yes | The operation to perform |
| `payload` | object | Yes | Action-specific parameters (may be empty `{}`) |

---

## Response format — success

```json
{
  "request_id": "req-001",
  "ok":         true,
  "data":       { ... }
}
```

## Response format — error

```json
{
  "request_id": "req-001",
  "ok":         false,
  "error": {
    "code":    "UNKNOWN_ACTION",
    "message": "Unknown action 'fly'. Supported: ['list_menu', 'list_restaurants', 'ping']."
  }
}
```

---

## Supported actions (V1)

### `ping` — Health check

**Request:**
```json
{"request_id":"req-001","action":"ping","payload":{}}
```

**Response:**
```json
{"request_id":"req-001","ok":true,"data":{"status":"ok"}}
```

---

### `list_restaurants` — List all restaurants

**Request:**
```json
{"request_id":"req-002","action":"list_restaurants","payload":{}}
```

**Response:**
```json
{
  "request_id": "req-002",
  "ok": true,
  "data": [
    {
      "restaurant_id": 1,
      "name": "Hyderabad Spice House",
      "description": "Authentic Hyderabadi cuisine.",
      "approval_status": "approved",
      "is_open": true
    },
    {
      "restaurant_id": 2,
      "name": "Dosa Corner",
      "description": "South Indian breakfast specialities.",
      "approval_status": "approved",
      "is_open": true
    }
  ]
}
```

Data is read directly from the SQLite `restaurants` table — nothing is hardcoded.

---

### `list_menu` — List a restaurant's menu items

**Request:**
```json
{"request_id":"req-003","action":"list_menu","payload":{"restaurant_id":1}}
```

**Successful response:**
```json
{
  "request_id": "req-003",
  "ok": true,
  "data": [
    {
      "item_id": 1,
      "restaurant_id": 1,
      "name": "Chicken Biryani",
      "description": "Slow-cooked Dum Biryani with tender chicken pieces.",
      "price": 180.0,
      "is_available": true
    },
    {
      "item_id": 2,
      "restaurant_id": 1,
      "name": "Veg Biryani",
      "description": "Aromatic basmati rice with fresh vegetables.",
      "price": 120.0,
      "is_available": true
    }
  ]
}
```

**Error — restaurant not found:**
```json
{
  "request_id": "req-003",
  "ok": false,
  "error": { "code": "NOT_FOUND", "message": "No restaurant found with id 9999." }
}
```

**Error — invalid payload:**
```json
{
  "request_id": "req-003",
  "ok": false,
  "error": { "code": "INVALID_PAYLOAD", "message": "'restaurant_id' must be an integer." }
}
```

---

## Error codes

| Code | When it occurs |
|------|----------------|
| `MALFORMED_JSON` | The request bytes cannot be parsed as JSON |
| `INVALID_REQUEST` | Missing or invalid `action` field |
| `INVALID_PAYLOAD` | `payload` is wrong type or missing a required key |
| `UNKNOWN_ACTION` | `action` value is not recognised |
| `NOT_FOUND` | The requested resource (e.g. restaurant) does not exist |
| `DB_ERROR` | Internal database error (details are not exposed to the client) |
| `MESSAGE_TOO_LARGE` | Message exceeded 64 KiB without a newline |

---

## Socket lifecycle

```
Client                              Server
  |                                    |
  |------ TCP SYN ─────────────────>   | bind() + listen() waiting
  |<----- TCP SYN-ACK ──────────────   | accept() returns conn_sock
  |------ TCP ACK ─────────────────>   |
  |  (3-way handshake complete)        |
  |                                    |
  |-- "{"action":"ping",...}\n" ─────> | recv() + buffer until \n
  |<-- "{"ok":true,...}\n" ──────────  | send_response()
  |                                    |
  |-- "{"action":"list_menu",...}\n" -> | second request, same connection
  |<-- "{"ok":true,...}\n" ──────────  |
  |                                    |
  |------ FIN ─────────────────────>   | client closes connection
  |<----- FIN-ACK ──────────────────   |
```

---

## Key socket functions explained

| Function | What it does |
|----------|-------------|
| `socket()` | Creates a new socket object (no network activity yet) |
| `bind(host, port)` | Reserves an address on this machine for this socket |
| `listen(backlog)` | Marks the socket as passive — ready to accept clients |
| `accept()` | Blocks until a client connects; returns `(conn_sock, addr)` |
| `recv(bufsize)` | Reads up to `bufsize` bytes from the network |
| `sendall(data)` | Sends all bytes (loops internally if needed) |
| `close()` | Closes the connection and releases the port |

---

## V1 limitations

- **Single client at a time.** A second client must wait until the first disconnects.
- **No authentication.** Any client on localhost can call any action.
- **No order placement, payments, or delivery tracking** — V0 database tables exist
  but the server does not expose them yet.
- **Not a distributed system.** Client and server run as threads in the same OS,
  communicating over the loopback interface. Distributed deployment is a future milestone.

---

## Planned for future versions

| Feature | Version |
|---------|---------|
| Concurrent clients (threading) | V2 |
| Customer registration / login | V2 |
| Order placement | V3 |
| Independent microservices | V3+ |
| Message broker (Kafka/Redis) | V4+ |
