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

## Migration ordering

Migrations run in deterministic filename order: categories → products → stock movements. This order matches foreign key dependencies. Database migrations and ORM declarations must be changed together.

## Scope limitations

This is a **development skeleton**, not a released warehouse system. No seed records, login/registration, user roles, barcode scanning, purchasing, suppliers, warehouses, currency settings, or stock write UI yet.
