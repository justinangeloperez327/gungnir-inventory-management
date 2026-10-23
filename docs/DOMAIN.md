# Inventory domain decisions

## Canonical tables

- `categories`: stable code and display name; uniqueness on code.
- `products`: SKU (unique), category, name, integer unit price in the currency's minor units, stock-on-hand counter, reorder level, active flag.
- `stock_movements`: append-only intended audit journal; quantity delta, reason, reference and product foreign key.

## Write-path invariants (planned for Group 3 onwards)

1. Stock may never become negative unless an explicitly authorized backorder policy is introduced.
2. A stock increase or decrease and its ledger entry must commit **atomically** in one database transaction. Never update the counter without its audit entry.
3. Concurrent requests must not oversell stock. Test two simultaneous decrements against the same SKU.
4. Inventory adjustments require a validated reason, actor identity, appropriate role, and an idempotency token/reference. The initial schema only reserves `reason` and `reference` columns; actor attribution and idempotency enforcement are **not implemented**.
5. No GET request may mutate inventory state. Write endpoints remain unregistered until access controls and tests exist.
6. Stored money values are integers; currency is not yet modeled. Do not assume minor units imply AED for every product.
7. Product deactivation should preserve historical ledger entries. Permanent deletion of referenced products should be disallowed.
8. The first product list is deliberately limited to 100 entries. Add pagination and search before larger datasets.

## User accounts (Group 2)

Users have a private password hash, a unique email address, an active flag, and one of three roles: `admin`, `manager`, `viewer`. Session identity restoration rechecks the active flag and role on every request. There is no public signup. The initial host-local CLI provisions accounts after migrations; role checks are currently applied to read/role-probe endpoints only. Future stock-changing operations must independently enforce role permissions and actor attribution.

## Migration ordering

Migrations run in deterministic filename order: categories → products → stock movements → users. This order matches foreign key dependencies. Database migrations and ORM declarations must be changed together.

## Scope limitations

This is a **development skeleton**, not a released warehouse system. No product seed records, public registration, barcode scanning, purchasing, suppliers, warehouses, currency settings, or stock write UI yet.
