# Mini Swiggy V3.2 — Refund Management and Payment Consistency

## 1. Executive Summary

Mini Swiggy **Version 3.2** introduces a robust, simulated refund management subsystem that maintains transactional integrity and payment consistency across customer ordering, deferred payments (V3.1), and seller kitchen operations (V3).

In real-world distributed commerce platforms (e.g., Swiggy, Zomato, Uber Eats), orders that have been successfully paid by customers may be rejected or cancelled by a restaurant due to operational constraints (e.g., kitchen overload, unavailable ingredients). When this occurs:
1. The original payment record must **never** be deleted or overwritten—it serves as an immutable audit log that customer funds were captured.
2. A separate, dedicated **Refund Lifecycle** must track refund state independently of payment status.
3. The refund amount must be authoritatively derived from the database, never from client input.
4. Idempotency must be guaranteed: repeated rejections, concurrent requests, or network retries must never issue duplicate refunds or alter the database state inconsistently.

> **IMPORTANT**: This is an educational distributed-systems project. No real external payment gateway is integrated. All payments and refunds are simulated via deterministic, auditable backend state machines.

---

## 2. Architecture & Design Decisions

### 2.1 Independent Lifecycles

The order status, payment status, and refund status operate as independent, non-conflicting state machines:

| Dimension | States | Governed By |
|---|---|---|
| **Order Status** | `placed` / `pending` $\rightarrow$ `accepted` $\rightarrow$ `preparing` $\rightarrow$ `ready_for_pickup` $\rightarrow$ `rejected` / `cancelled` | Kitchen operations & state machine |
| **Payment Status** | `pending` $\rightarrow$ `paid` (or `failed`) | Customer payment simulation |
| **Refund Status** | `pending` $\rightarrow$ `processing` $\rightarrow$ `refunded` (or `failed`) | Automated refund engine |

#### Example State Progression: Paid Order Rejected by Kitchen
1. Customer places order: `order_status = 'pending'`, `payment_status = 'pending'`, `refund = None`.
2. Customer pays: `order_status = 'accepted'`, `payment_status = 'paid'`, `refund = None`.
3. Seller rejects order: `order_status = 'rejected'`, `payment_status = 'paid'`, `refund = 'refunded'`.

Notice that `payment_status` remains `paid`. The database preserves the immutable payment transaction reference, and the new `refunds` table stores the refund reference (`REF-SIM-...`) and reason.

---

## 3. Database Schema & Migration

### 3.1 The `refunds` Table

A dedicated `refunds` table was added to `database/schema.sql`:

```sql
CREATE TABLE IF NOT EXISTS refunds (
    refund_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id        INTEGER NOT NULL UNIQUE
                    REFERENCES orders(order_id)
                    ON DELETE RESTRICT,
    amount          REAL    NOT NULL
                    CHECK (amount > 0),
    refund_status   TEXT    NOT NULL DEFAULT 'pending'
                    CHECK (refund_status IN ('pending', 'processing', 'refunded', 'failed')),
    reason          TEXT,
    transaction_ref TEXT    NOT NULL UNIQUE,
    attempt_count   INTEGER NOT NULL DEFAULT 1
                    CHECK (attempt_count >= 1),
    created_at      TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at      TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_refunds_order_id ON refunds(order_id);
CREATE INDEX IF NOT EXISTS idx_refunds_status ON refunds(refund_status);
```

### 3.2 Design Constraints & Guarantees
- **1:1 Full Refund**: `order_id UNIQUE` enforces that at most one full refund record can ever exist for an order in this milestone.
- **Foreign Key Integrity**: `REFERENCES orders(order_id) ON DELETE RESTRICT` prevents orders with refund histories from being pruned.
- **Positive Amounts**: `CHECK (amount > 0)` prevents invalid zero or negative refund insertions.
- **Safe Migrations**: `migrate_db()` in `database/db.py` executes `CREATE TABLE IF NOT EXISTS refunds` and index creation idempotently on existing databases without dropping tables or losing historical data.

---

## 4. Refund Engine & Rules

### 4.1 When is a Refund Created?

| Scenario | Prior State | Action | Outcome |
|---|---|---|---|
| **Scenario A** | Order `paid` | Seller rejects order | Automated full refund initiated (`refund_status = 'refunded'`) |
| **Scenario B** | Order `pending` (unpaid) | Seller rejects order | Order rejected; **NO** refund created |
| **Scenario C** | Order payment `failed` | Seller rejects order | Order rejected; **NO** refund created |
| **Scenario D** | Duplicate rejection | Seller re-rejects already rejected order | Idempotent response; **NO** duplicate refund created |

### 4.2 Failure Simulation and Retry Mechanics
- If an operational failure occurs during refund processing (simulated via `simulate_refund_failure=True`), the refund record is persisted with `refund_status = 'failed'`.
- The order remains strictly `rejected`.
- The customer or authorized seller can trigger `retry_refund`.
- Upon retry, the attempt counter increments (`attempt_count = attempt_count + 1`), a new refund transaction reference is issued, and status updates to `refunded`.
- If an already completed refund is retried, the backend rejects it with `ERR_REFUND_ALREADY_PROCESSED`.

---

## 5. TCP Protocol & API Specification

### 5.1 New TCP Actions

#### 1. `get_refund`
Retrieve refund details for a specific order with customer or seller authorization.
```json
// Request
{
  "request_id": "req-01",
  "action": "get_refund",
  "payload": {
    "order_id": 42,
    "customer_id": 1
  }
}

// Response
{
  "request_id": "req-01",
  "ok": true,
  "data": {
    "refund_id": 1,
    "order_id": 42,
    "amount": 180.0,
    "refund_status": "refunded",
    "reason": "Kitchen out of stock",
    "transaction_ref": "REF-SIM-42-B8E9A10F",
    "attempt_count": 1,
    "created_at": "2026-11-03 00:15:00",
    "updated_at": "2026-11-03 00:15:00"
  }
}
```

#### 2. `get_refund_history`
Retrieve scoped refund history for a customer (`customer_id`) or a seller's restaurant (`seller_id`).
```json
// Request
{
  "request_id": "req-02",
  "action": "get_refund_history",
  "payload": {
    "customer_id": 1
  }
}
```

#### 3. `retry_refund`
Retry a failed refund for an order.
```json
// Request
{
  "request_id": "req-03",
  "action": "retry_refund",
  "payload": {
    "order_id": 42,
    "customer_id": 1,
    "simulate_failure": false
  }
}
```

---

### 5.2 HTTP Gateway Routes

All HTTP endpoints bridge directly to the TCP Socket Server (zero direct database queries from the gateway):

| Method | HTTP Path | Maps to TCP Action | Authorization | Status Codes |
|---|---|---|---|---|
| `GET` | `/api/orders/{id}/refund?customer_id=1` | `get_refund` | Customer or Seller | `200`, `400`, `403`, `404` |
| `POST` | `/api/orders/{id}/refund/retry` | `retry_refund` | Customer or Seller | `200`, `400`, `403`, `404`, `409` |
| `GET` | `/api/customers/{id}/refunds` | `get_refund_history` | Customer Scoped | `200`, `400` |
| `GET` | `/api/seller/refunds?seller_id=2` | `get_refund_history` | Restaurant Scoped | `200`, `400`, `403` |

---

## 6. Frontend User Experience

### 6.1 Customer Interface (`web/app.js` & `web/styles.css`)
- **Order History**:
  - Displays distinct badges for refunded orders: `Refunded · ₹180` (purple pill), `Refund Failed` (red pill), `Refund Processing` (blue pill).
  - Displays `No payment · No refund applicable` for unpaid rejected orders.
  - If a refund failed, an orange **Retry Refund** button is rendered directly on the order history card.
- **Receipt Modal**:
  - Displays a dedicated `Simulated Refund Details` callout with amount, refund transaction reference, reason, and processing attempts.
  - Provides a **Retry Refund** action button inside the receipt when refund status is `failed`.

### 6.2 Seller Dashboard (`web/seller.js` & `web/seller.css`)
- **Rejection Confirmation**:
  - When the seller clicks `Reject Order`, the application verifies whether the order was paid.
  - If paid, a prominent confirmation prompt warns:
    > *"Order #42 was successfully PAID (₹180.00). Rejecting this order will automatically initiate a simulated FULL REFUND of ₹180.00 to the customer. Proceed with rejection and refund?"*
- **Order Cards & Details Modal**:
  - Rejected cards display `PAID · REFUNDED (₹180.00)` or `PAID · REFUND FAILED`.
  - The seller order details modal includes a dedicated `Simulated Refund Record` section.

---

## 7. Distributed Systems Analysis: Simulation vs. Reality

Mini Swiggy models payment and refund processing using a simulated internal engine. In an actual multi-node production architecture:

1. **Transactional vs. External Asynchrony**:
   - SQLite transactions can atomically commit internal state changes (e.g. updating `order_status = 'rejected'`).
   - However, **external payment gateways** (Stripe, Razorpay, Adyen) exist outside the local database boundary. Network calls to third-party gateways can fail, timeout, or hang.
   - Therefore, local database transactions **cannot** make external payment transfers atomic.
2. **Idempotency Keys**:
   - In production, outbound refund API requests to payment gateways must include a unique idempotency key (such as `refund_id` or `order_id + attempt_count`) to guarantee that retries never double-charge or double-refund.
3. **Reconciliation & Webhooks**:
   - Gateways typically acknowledge refund requests with `status = 'pending'`, and notify the platform later via signed webhook events (`refund.succeeded`, `refund.failed`).
   - Offline batch reconciliation jobs periodically query the payment gateway for stuck or non-terminal refund states.

---

## 8. Manual Verification Guide

### 8.1 Scenario 1: Reject Paid Order (Full Refund)
1. Start backend: `python server/tcp_server.py`
2. Start gateway: `python gateway/http_gateway.py`
3. Open Customer UI: `http://localhost:8000`
4. Place an order for Chicken Biryani (₹180) and complete payment. Note the Order ID (e.g. `#1`).
5. Open Seller Dashboard: `http://localhost:8000/seller.html`
6. Locate Order `#1` and click `Reject`.
7. Confirm the prompt acknowledging the automatic full refund.
8. Switch back to Customer UI $\rightarrow$ **Past Orders**.
9. Verify that Order `#1` displays `Order: REJECTED`, `Payment: PAID`, and badge `Refunded · ₹180.00`.
10. Click **View Receipt** and confirm the `Simulated Refund Details` callout.

### 8.2 Scenario 2: Reject Unpaid Order
1. Place a new order on `http://localhost:8000` but close checkout without paying (`payment_status = 'pending'`).
2. On Seller Dashboard, reject the order.
3. Verify on Customer Past Orders that it shows `No payment · No refund applicable`.

### 8.3 Scenario 3: Retry Failed Refund
1. Using TCP client CLI:
   ```powershell
   python clients/tcp_client.py update_order_status 2 <order_id> rejected --fail-refund
   ```
2. Verify in Customer UI that the order card displays `Refund Failed` and a `Retry Refund` button.
3. Click `Retry Refund`. Verify toast notification and status transitions to `Refunded`.
