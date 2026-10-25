# Procurement: suppliers, purchase orders, approval and receiving (Group 6)

## Overview

This phase builds on the existing SQLite transaction, warehouse and idempotency mechanisms. It creates five new **additive** tables, without rewriting previous stock movements. Currency is **not configured**: `unit_price_minor` is a price snapshot in minor currency units. Do not interpret it as AED or post invoices/tax against it until currency handling is designed.

- Suppliers have a unique code, name and active status. The initial API supports creation and bounded listing.
- Purchase orders have unique human-readable `po_number`, supplier, creator and immutable order lines. Each line stores product ID, ordered quantity, received quantity (starts at zero), and unit price snapshot. Identical products cannot appear twice in one order.
- New orders are **draft**. Only an administrator other than the creator can approve a draft, through an audited separate approval record. Only approved or partially received orders may accept goods.
- Goods may arrive in **partial receipts** into an active warehouse location. Each receipt records the actor, line, warehouse location, unique reference and the quantity/balance after receiving. The status transitions `draft → approved → partially_received → received`.
- Every receipt atomically changes `purchase_order_lines.received_quantity`, global `products.stock_on_hand`, warehouse `location_balances.quantity`, `stock_movements`, `location_movements`, `inventory_operations` and `purchase_receipts`.
- A globally unique idempotency key prevents duplicate receipts and collisions with earlier inventory operations. Identical retries after completion return 200 and original stock snapshots. A conflicting payload returns 409.
- Purchase order lines are immutable after creation. Pricing/tax changes, amendments, cancellation, supplier editing, return-to-supplier and invoices are **not implemented**.

## Endpoints

| Method | URL | Role |
| --- | --- | --- |
| GET | `/api/suppliers` | authenticated |
| POST | `/api/suppliers` | manager/admin |
| GET | `/api/purchase-orders?after=ID` | authenticated, paginated 20 |
| POST | `/api/purchase-orders` | manager/admin |
| GET | `/api/purchase-orders/{order}` | authenticated |
| POST | `/api/purchase-orders/{order}/approve` | admin, not creator |
| GET | `/api/purchase-orders/{order}/receipts` | authenticated, bounded 100 |
| POST | `/api/purchase-orders/{order}/receipts` | manager/admin |

All state-changing calls use JSON and the authenticated session's `X-CSRF-Token` header.

Create a supplier:

```json
{"code":"SUPPLIER-A","name":"Supplier A"}
```

Create a purchase order:

```json
{
  "po_number":"PO-2026-0001",
  "supplier_id":1,
  "lines":[
    {"product_id":10,"quantity":6,"unit_price_minor":12500},
    {"product_id":11,"quantity":2,"unit_price_minor":30000}
  ]
}
```

Approve with `POST /api/purchase-orders/1/approve` and `{}`. Receiving can then occur at the desired location:

```json
{
  "line_id":1,
  "location_id":4,
  "quantity":3,
  "reference":"RECV-PO-2026-0001-01"
}
```

The receipt returns `line_received_after`, `stock_after`, `location_after` and `replayed`. A second partial receipt can use another unique reference for the outstanding quantity. Quantity must be a positive integer ≤ 1,000,000 and must not exceed the outstanding line balance. The global and location balance must also stay within the signed 32-bit stock ceiling.

## Security and concurrency

An order's creator cannot approve it, including if the creator is an administrator. Viewers may inspect order data but cannot create, approve, or receive. Unknown JSON fields, nonexistent/archived products, invalid locations and invalid states reject without committing any stock changes.

The application uses Gungnir `DB::transaction`, a shared in-process inventory mutex, a unique global operation reference, and conditional updates on the line, global product and location balances. Transactions roll back if any step fails. SQLite writers in separate processes are serialized by its database locking; clients receiving a transient 503 under contention must retry with **the same reference**, not a new one.

The implementation remains a development prototype. Sessions and login rate limits are process-local. Orders lack currency/tax, procurement authorization beyond roles, supplier edits and formal approval workflows; public production deployment is not recommended.
