# Gungnir Inventory Management roadmap

The application doubles as an end-to-end acceptance project for Gungnir. A feature is complete only after Gungnir compiler validation, native compilation, database integration tests, and HTTP smoke tests pass.

| Group | Deliverable | Acceptance gate |
| --- | --- | --- |
| 1 | Application SDK bootstrap, schema, read-only pages/API, CI | Native build, migrations, HTTP smoke on SQLite |
| 2 | User accounts, login/logout, roles and protected routes | Compiler, native build, CSRF/session, role tests |
| 3 | Product/category CRUD and validation | Role/CSRF enforcement, uniqueness, FK integrity, cursor bounds and stock isolation |
| 4 | Inventory receipts, issues, returns and corrections | Unique reference, SQLite transaction and CAS, audit history, concurrent HTTP tests |
| 5 | Warehouse/location tracking | Located/unallocated reconciliation, cross-warehouse transfers, role/CSRF and concurrent HTTP tests |
| 6 | Suppliers, purchase orders and receiving | Approval separation, partial receiving, global and located stock journals, idempotency and concurrency |
| 7 | Sales orders, reservations and fulfillment | No double allocation / oversell |
| 8 | Stock reconciliation and physical counts | Reconciliation history and approval audit |
| 9 | Dashboard, low-stock alerts, search and pagination | Accurate totals, accessible UI, bounded queries |
| 10 | CSV import/export and bulk operations | Deduplication, validation, rollback and large-file tests |
| 11 | Audit trails, observability, backup/restore | Actor attribution, structured logs, restore drill |
| 12 | Security, load, accessibility and production packaging | End-to-end test matrix, release checklist |

## Progress

Groups 1–5 passed native CI on Linux. Group 6 adds supplier registration, immutable purchase-order line snapshots, admin-only independent approvals and partial warehouse receiving. Every receipt atomically updates ordered and received quantities, both inventory balances and both movement journals under the existing global idempotency reference. The Group 6 acceptance gate is native Linux/Windows compiler, migration and HTTP tests including concurrency and retries.

## Constraints

Use Gungnir for application logic and native runtime, SQLite in the first implementation, Spear for simple SSR pages. Do not add Next.js, React or Node dependencies until a concrete frontend requirement justifies them. Node 24 applies if a Node-based frontend is later introduced. Do not automatically deploy or publish releases.
