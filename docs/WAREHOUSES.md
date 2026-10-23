# Warehouse and location tracking — Group 5

## Inventory model

The **central stock total** is `products.stock_on_hand`. A product can be held at multiple warehouse locations. The allocated sum is calculated from `location_balances`. The difference, `unallocated = stock_on_hand - located`, represents stock that is owned but not yet assigned to a warehouse location. No existing stock is moved implicitly during migration.

Locations are globally identified by unique 1–32 byte codes and belong to a warehouse with its own unique code. The prototype provides creation/list endpoints; editing and deactivation are deferred. A warehouse/location must be active for new inventory movement.

All mutation endpoints require an authenticated **admin or manager** session and the session CSRF token (`X-CSRF-Token`). Viewer accounts can inspect balances/history but cannot write.

## API

| Method | Endpoint | Result |
| --- | --- | --- |
| POST | `/api/warehouses` | Create warehouse from `{"code":"MAIN","name":"Main"}` |
| GET | `/api/warehouses` | Up to 100 warehouses |
| POST | `/api/locations` | Create location from `{"warehouse_id":1,"code":"BIN-A","name":"Bin A"}` |
| GET | `/api/locations?warehouse_id=1` | Up to 100 warehouse locations |
| GET | `/api/inventory/products/{product}/balances` | Central stock, located sum, unallocated and location details |
| POST | `/api/inventory/allocations` | Assign existing unallocated quantity to one location |
| POST | `/api/inventory/transfers` | Move quantity between locations, including cross-warehouse transfers |
| POST | `/api/inventory/location-movements` | Receipt, issue, return or absolute correction at one location |
| GET | `/api/inventory/locations/{location}/movements?after={id}` | Cursor-paginated location audit journal (20/page) |

Example allocation (does not change total stock):

```json
{"product_id":1,"location_id":3,"quantity":10,
 "reason":"initial assignment","reference":"ALLOC-2026-0001"}
```

Example warehouse transfer (does not change total stock):

```json
{"product_id":1,"source_location_id":3,"destination_location_id":9,
 "quantity":4,"reason":"branch request","reference":"XFER-2026-0001"}
```

Example local receipt (increases both local and global stock):

```json
{"product_id":1,"location_id":9,"kind":"receipt","quantity":5,
 "reason":"PO received","reference":"RCPT-2026-0001"}
```

For located correction, `quantity` means the desired **location balance**. For the legacy unlocated Group 4 correction endpoint, `quantity` still means the desired **global balance**. In both cases, reducing the central quantity below located stock is prohibited.

References are globally unique across all stock operations. Identical successful retries by the same actor return 200 with `replayed:true`; reusing a reference for another product, actor, operation kind, location, source or destination returns 409. Transient SQLite contention may return 503: retry the same request using the same reference.

## Consistency and auditability

- Allocation creates one positive `location_movements` row and one idempotency operation, but no global stock movement.
- Transfer creates a signed pair of location movements, one transfer record, and one idempotency operation, but no global stock movement.
- Located receipt/issue/return/correction atomically updates both balances and writes one global movement and one location movement.
- All tables are created by one additive, last-ordered migration. Existing inventory and existing stock movement history are preserved.
- SQLite is the supported backend for this phase; the SQL uses `ON CONFLICT`, bound parameters and transaction-scoped writes. Do not switch database engines without porting/tests.
- Historical location movements remain readable after product archive.
- Data invariants: `0 <= located <= total <= 2,147,483,647`, each location quantity is nonnegative, `total == SUM(global movement deltas)` for newly created products and `location quantity == SUM(location movement deltas)` for every location since its introduction.
- If a production migration ever encounters external database writes that bypassed the ledger, reconcile them explicitly before relying on location totals.

## Production readiness

Only development SQLite, in-memory sessions and local throttling have been validated. This is **not production ready**. Warehouse/location list endpoints are intentionally bounded at 100 results without a pagination protocol (deferred), and physical inventory approvals, reserved stock, per-warehouse permissions and multi-database deployment are out of scope.
