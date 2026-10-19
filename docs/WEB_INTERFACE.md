# Mini Swiggy — Customer Web Interface & HTTP Gateway

> **Version 2 Web Extension**: A lightweight, modern web interface and HTTP API gateway for the Mini Swiggy distributed food-delivery platform.

---

## 1. Architecture Overview

Mini Swiggy uses an **API Gateway** pattern to bridge standard web browsers with the internal backend TCP socket server:

```
┌──────────────────────────────────────┐
│   Browser Client (HTML / CSS / JS)   │
└──────────────────┬───────────────────┘
                   │
                   │ HTTP fetch() requests (JSON & Static Assets)
                   ▼
┌────────────────────────────────────────────────────────┐
│     Python HTTP Gateway (gateway/http_gateway.py)      │
│               Listening on 127.0.0.1:8000              │
│                                                        │
│  - Serves static web frontend (web/index.html, etc.)   │
│  - Enforces path-traversal security guards            │
│  - Enforces customer ownership on order receipts       │
│  - Opens short-lived TCP socket per API request        │
└──────────────────┬─────────────────────────────────────┘
                   │
                   │ Newline-Delimited JSON (NDJSON) over TCP
                   ▼
┌────────────────────────────────────────────────────────┐
│    Mini Swiggy TCP Socket Server (server/tcp_server.py)│
│               Listening on 127.0.0.1:5000              │
│                                                        │
│  - Receives NDJSON stream, buffers until '\n'          │
│  - Dispatches to pure application handler              │
└──────────────────┬─────────────────────────────────────┘
                   │
                   ▼
┌────────────────────────────────────────────────────────┐
│      Application Logic (server/request_handler.py)     │
│                                                        │
│  - Validates inputs & calculates prices on server      │
│  - Atomic transactions (orders + order_items)          │
│  - Idempotent payment simulation & receipt generation  │
└──────────────────┬─────────────────────────────────────┘
                   │
                   ▼
┌────────────────────────────────────────────────────────┐
│          SQLite Database Storage (swiggy.db)           │
└────────────────────────────────────────────────────────┘
```

---

## 2. Why the Browser Cannot Connect Directly to the TCP Server

1. **Protocol Sandbox**: Web browsers run in a secure sandbox. JavaScript running in a browser (`fetch`, `XMLHttpRequest`) can only make HTTP/HTTPS or WebSocket requests. It **cannot open raw TCP sockets** to arbitrary ports.
2. **Framing Mismatch**: If a browser attempts to send a raw HTTP request (such as `GET / HTTP/1.1\r\nHost: ...`) to the TCP socket on port 5000, the TCP server reads until the first `\n` and attempts `json.loads("GET / HTTP/1.1\r")`. This immediately raises `json.JSONDecodeError` because HTTP headers are not JSON.
3. **The Gateway Solution**: The Python HTTP gateway (`http.server.ThreadingHTTPServer`) acts as a bridge. It accepts HTTP requests from the browser, opens a TCP connection to `127.0.0.1:5000`, sends the expected NDJSON request line, reads the NDJSON response, and translates it into an HTTP response.

---

## 3. How HTTP Requests Are Translated to NDJSON

| HTTP Route | Method | Payload / Param | Translated TCP Action | Translated TCP Payload |
|---|---|---|---|---|
| `/api/health` | `GET` | None | `ping` | `{}` |
| `/api/restaurants` | `GET` | None | `list_restaurants` | `{}` |
| `/api/restaurants/{id}/menu` | `GET` | `{id}` from URL | `list_menu` | `{"restaurant_id": id}` |
| `/api/orders` | `POST` | JSON body | `create_order` | `{"customer_id": id, "restaurant_id": id, "items": [...]}` |
| `/api/orders/{id}/payment` | `POST` | JSON body + `{id}` | `simulate_payment` | `{"order_id": id, "payment_method": "...", "simulate_status": "..."}` |
| `/api/orders/{id}?customer_id=1` | `GET` | `{id}` + query param | `get_order` | `{"order_id": id}` *(Gateway verifies customer ownership)* |
| `/api/customers/{id}/orders` | `GET` | `{id}` from URL | `get_order_history` | `{"customer_id": id}` |

### Short-Lived TCP Connection Model
To keep the single-threaded TCP server responsive and avoid blocking other clients, the HTTP gateway:
1. Opens a fresh TCP socket connection to `127.0.0.1:5000`.
2. Sends exactly one NDJSON request string terminated by `\n`.
3. Reads the single NDJSON response string terminated by `\n`.
4. Immediately closes the TCP socket in a `finally` block.

---

## 4. Startup Instructions & How to Run

### Step 1: Initialize Database (if not already done)
```powershell
python main.py all
```

### Step 2: Start the TCP Server (Terminal 1)
```powershell
python -m server.tcp_server
```
Expected output:
```
[server] Mini Swiggy V1 TCP server listening on 127.0.0.1:5000
[server] Press Ctrl+C to stop.
```

### Step 3: Start the HTTP Gateway (Terminal 2)
```powershell
python -m gateway.http_gateway
```
Expected output:
```
============================================================
  Mini Swiggy HTTP Web Gateway
============================================================
  HTTP Frontend URL : http://127.0.0.1:8000
  TCP Backend Target: 127.0.0.1:5000
  Static Web Directory: E:\Mini Swiggy\web
  Press Ctrl+C to stop the gateway.
============================================================
```

### Step 4: Open the Customer Website
Open your browser and navigate to:
```
http://127.0.0.1:8000
```

---

## 5. Website Features & Walkthrough

1. **Top Header**:
   - **Mini Swiggy Brand**: Click anytime to return to the restaurant listing.
   - **Backend Status Indicator**: Live indicator showing `TCP Connected (5000)` (green) or `TCP Disconnected` (red).
   - **Customer Identity**: Displays `Ordering as: Alice Customer (ID: 1)` (the seeded active customer).
   - **My Orders**: Quick navigation to view all past orders.
   - **Cart**: Shows live item count badge.
2. **Browse Kitchens**:
   - Displays real-time restaurants queried from SQLite via the TCP backend.
   - Indicates whether kitchens are Open or Closed.
3. **Menu & Cart Controls**:
   - Click **View Menu** on any kitchen.
   - Add items or use the `+` / `-` quantity buttons.
   - **Single-Restaurant Guard**: Adding food from a second restaurant prompts a confirmation dialog asking whether to clear the current cart or keep it.
4. **Checkout & Simulated Payment**:
   - Click **Cart** &rarr; **Proceed to Checkout**.
   - Review items and click **Place Food Order**.
   - The authoritative server-calculated total is displayed.
   - Select UPI or Card, and choose **Simulate Success** or **Simulate Failure** (for testing fault handling).
   - Click **Pay Now (Simulated)**.
5. **Receipt & History**:
   - View the snapshot receipt with exact unit prices, order status (`accepted`), and transaction reference (`TXN-SIM-...`).
   - Click **My Orders** to review all past orders in newest-first order.

---

## 6. How to Stop the Services

In both terminal windows:
- Press `Ctrl+C` to cleanly shut down the server or gateway.

---

## 7. Known Architectural Limitations

1. **Sequential TCP Socket Server**: The backend TCP server processes one client connection at a time. The gateway mitigates this by using ultra-short connections (1–3ms per request). Concurrency with worker thread pools is scheduled for Version 3.
2. **No Production Authentication**: Customer identification is fixed at `Alice Customer (ID: 1)` for demonstration. Real user login with passwords/tokens is out of scope for V2.
3. **Simulated Payments**: Payments are verified and recorded locally in SQLite; no real external banking APIs are involved.

---

## 8. Troubleshooting & Verification

### Running the Test Suite
```powershell
# Run all 112 automated tests (V0, V1, V2, and Gateway):
python -m pytest tests/ -v

# Run gateway tests only:
python -m pytest tests/test_web_gateway.py -v
```

### Common Issues
- **"TCP Backend Disconnected" in browser**: Make sure `python -m server.tcp_server` is running in Terminal 1 on port 5000.
- **Port 8000 already in use**: Start the gateway on a custom port:
  ```powershell
  python -m gateway.http_gateway --port 8080
  ```
  Then open `http://127.0.0.1:8080` in your browser.
