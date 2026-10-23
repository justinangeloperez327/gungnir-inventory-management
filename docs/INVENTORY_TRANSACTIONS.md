# Inventory transactions — Group 4

This group adds a native Gungnir C++23 inventory service integrated with the existing `.gnr` application. The catalog remains in Gungnir controllers. The native extension encapsulates SQLite-specific concurrency-safe stock mutation because it requires a single conditional SQL update and durable idempotency enforcement.

## HTTP contract

**POST** `/api/inventory/movements` (manager/admin only, authenticated session and CSRF header).

```json
{
  "product_id": 1,
  "kind": "receipt",
  "quantity": 10,
  "reason": "delivery",
  "reference": "PO-2026-0001"
}
```

- `kind`: `receipt` (+quantity), `issue` (-quantity), `return` (+quantity), `correction` (set target stock).
- `quantity`: integer 1–1,000,000 for receipts/issues/returns. For a correction, **target** stock-on-hand, 0–2,147,483,647. Zero-delta corrections return 409.
- `reference`: globally unique case-sensitive idempotency key, 8–64 characters from ASCII letters, numbers, `.`, `_`, `:`, `-`. Always retry the **same logical request** with the same reference. Another payload using the same reference returns 409.
- `reason`: nonempty UTF-8 text, maximum 32 bytes, without ASCII control characters.
- Other submitted JSON keys (including direct `stock_on_hand` overrides) are rejected.
- `actor_id` comes exclusively from the verified session, not the request body.

On first success, HTTP **201** returns:
```json
{
  "product_id": 1,
  "actor_id": 1,
  "kind": "receipt",
  "quantity": 10,
  "quantity_delta": 10,
  "stock_after": 10,
  "reference": "PO-2026-0001",
  "reason": "delivery",
  "replayed": false
}
```
Identical successful retries return HTTP **200**, `replayed: true`, and the original movement summary. Invalid data yields 422, missing products 404, archived products or insufficient stock 409, unauthenticated requests 401, and role denials 403. Global CSRF middleware rejects missing tokens before the controller (419). Transient database errors yield 503 without exposing SQL.

**GET** `/api/inventory/products/{product}/movements` allows all authenticated roles, including viewers. Returns `data` (up to 20 oldest-first entries), `has_more`, and `next_cursor`. Use `?after=<movement_id>` for the next page; this remains readable for archived products. Query identifiers are bound to SQL statements.

## Atomicity and audit contract

1. The new `inventory_operations` table is an **additive migration** applied after the existing products/users tables. Its unique `reference` enforces global request deduplication. It records the actor, operation kind, input quantity, reason, signed stock delta and final balance.
2. The canonical `stock_movements` table retains the signed delta and the reference. One successful operation has exactly one movement row. Old rows remain intact.
3. A single Gungnir `DB::transaction` inserts the unique operation claim, reads the current product, conditionally updates `stock_on_hand` using the old balance predicate, inserts the movement, and finalizes the operation summary. Any failure throws and rolls back the entire transaction, including the claim.
4. SQLite uses a 5-second busy timeout. A process-local mutex prevents this instance's snapshot-upgrade races, while SQLite's write locking, unique reference constraint and conditional UPDATE prevent duplicate application and stale balance writes across processes. Multiple writer processes may still see retryable 503 errors under high contention: clients must use the **same** idempotency reference when retrying.
5. Stock is bounded 0–2,147,483,647. Negative stock and stock overflow are rejected. Zero-stock issues fail. Archived products cannot accept new movements.
6. This feature changes stock **only** through the transaction service; product metadata APIs remain unable to assign quantities. Direct external SQL writes are outside the application's authorization boundary.

## Security and deployment limits

Application sessions are still process-local, not durable/distributed. Authentication cookie settings and process-local login rate-limiting are development-only. This group does **not** imply production readiness or authorization of unaudited direct database access. The SQLite-specific SQL must be ported and retested if a non-SQLite adapter is selected. Warehouse-specific ledger partitioning is deferred to Group 5.

## CI

CI runs compiler/IR checks, full native compilation, migrations, account provisioning, Groups 2/3 regression tests and a Group 4 HTTP smoke suite. The suite exercises over-issue rollback, idempotent retries, collision rejection, forbidden writes, corrections, archive safety, pagination, and concurrent requests against real SQLite.
