# Inventory domain decisions

## Canonical tables

- `categories`: stable code and display name; uniqueness on code.
- `products`: SKU (unique), category, name, integer unit price in the currency's minor units, stock-on-hand counter, reorder level, active flag.
- `stock_movements`: append-only intended audit journal; quantity delta, reason, reference and product foreign key.

## Warehouse and location reconciliation (Group 5)

- Warehouses and globally unique location codes are registered by managers/admins. Each location belongs to one warehouse. Both levels have active flags.
- The central `products.stock_on_hand` remains the authoritative total. `SUM(location_balances.quantity)` is located stock. `unallocated = central - located` is intentionally explicit for legacy or not-yet-assigned stock. Existing inventory is **not** silently allocated to a default warehouse.
- `/api/inventory/allocations` moves stock from unallocated to a chosen location without changing central stock or creating a central movement. It creates a location journal row and global idempotency operation.
- `/api/inventory/transfers` debits a source and credits a distinct destination atomically, writing two location journal rows and a transfer record without changing global stock. Both same-warehouse and cross-warehouse moves are supported.
- `/api/inventory/location-movements` adjusts both central and location balances in one transaction, and creates both canonical global and per-location movement rows. Located corrections mean **the location's absolute target quantity**, unlike legacy Group 4 global corrections.
- Legacy Group 4 writes now reject reductions below located stock. Unknown input keys, missing roles, missing CSRF and invalid bounds are rejected without mutations. A globally unique reference prevents replay across allocation, transfer, located and unlocated movements.
- Reconciliation checks compare central balance, location sums, and signed movement journals. All write APIs use an optimistic balance predicate or SQLite's transaction locking; concurrent collisions fail/retry rather than oversell.

## Transactional stock ledger (Group 4)

See [Inventory Transactions](INVENTORY_TRANSACTIONS.md). A new `inventory_operations` table records actor identity and the unique operation reference. A Gungnir native extension commits an idempotency claim, bounded conditional stock update and canonical stock-movement entry in one transaction. The operation reference is global, including archived products. Receipts and returns increase quantity, issues decrease it, and corrections set the absolute target. History remains available on archived SKUs.

## Write-path invariants (enforced in Group 4)

1. Stock may never become negative unless an explicitly authorized backorder policy is introduced.
2. A stock increase or decrease and its ledger entry must commit **atomically** in one database transaction. Never update the counter without its audit entry.
3. Concurrent requests must not oversell stock. Test two simultaneous decrements against the same SKU.
4. Inventory adjustments require a validated reason, actor identity, appropriate role, and a globally unique idempotency token/reference. Both global and location ledgers record the actor through `inventory_operations`.
5. No GET request may mutate inventory state. All POST write routes validate permissions and CSRF.
6. Stored money values are integers; currency is not yet modeled. Do not assume minor units imply AED for every product.
7. Product deactivation should preserve historical ledger entries. Permanent deletion of referenced products should be disallowed.
8. Product/category catalogs use bounded cursor pagination (20 records). Warehouse lists use a conservative 100-record bound; broader pagination is deferred.

## Catalog CRUD (Group 3)

- Category code and product SKU are unique database-indexed identifiers. Friendly uniqueness prechecks supplement, not replace, database constraints.
- Managers/admins may create/update categories and product metadata. Categories linked to any products cannot be removed.
- All products start with zero stock; product deletion archives with active=false, preserving ledger history. A separate restore endpoint reactivates.
- Stock and active fields are allowlisted in the model solely so creation can explicitly initialize them; the HTTP create/update controllers never accept untrusted stock/active values. Never pass an unvalidated request body directly to ORM create/update.
- POST/PUT/PATCH mutation requests require a valid session, role, CSRF, and JSON body. Money uses nonnegative integer minor units.
- Category-code and product-SKU cursor pagination has 20 entries per page and explicit has_more/next_cursor.
- Quantity changes are accepted only through Group 4/5 atomic inventory transaction endpoints.

## User accounts (Group 2)

Users have a private password hash, a unique email address, an active flag, and one of three roles: `admin`, `manager`, `viewer`. Session identity restoration rechecks the active flag and role on every request. There is no public signup. The initial host-local CLI provisions accounts after migrations; role checks are currently applied to read/role-probe endpoints only. Future stock-changing operations must independently enforce role permissions and actor attribution.

## Migration ordering

Migrations run in deterministic filename order: categories → products → stock movements → users → inventory operations → warehouse/location tables. This order matches foreign key dependencies. Do not rename prior applied migration files. Database migrations and ORM declarations must be changed together.

## Scope limitations

This remains a **development prototype**, not a released warehouse system. No public registration, barcode scanning, purchasing, suppliers, currency settings, persistent sessions, or rich warehouse UI yet.
