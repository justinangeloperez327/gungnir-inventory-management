# Gungnir Inventory Management roadmap

The application doubles as an end-to-end acceptance project for Gungnir. A feature is complete only after Gungnir compiler validation, native compilation, database integration tests, and HTTP smoke tests pass.

| Group | Deliverable | Acceptance gate |
| --- | --- | --- |
| 1 | Application SDK bootstrap, schema, read-only pages/API, CI | Native build, migrations, HTTP smoke on SQLite |
| 2 | User accounts, login/logout, roles and protected routes | Compiler, native build, CSRF/session, role tests |
| 3 | Product/category CRUD and validation | Role/CSRF enforcement, uniqueness, FK integrity, cursor bounds and stock isolation |
| 4 | Inventory receipts, issues, returns and corrections | Unique reference, SQLite transaction and CAS, audit history, concurrent HTTP tests |
| 5 | Warehouse/location tracking | Correct location-level quantities and transfers |
| 6 | Suppliers, purchase orders and receiving | PO receipt creates audited movements |
| 7 | Sales orders, reservations and fulfillment | No double allocation / oversell |
| 8 | Stock reconciliation and physical counts | Reconciliation history and approval audit |
| 9 | Dashboard, low-stock alerts, search and pagination | Accurate totals, accessible UI, bounded queries |
| 10 | CSV import/export and bulk operations | Deduplication, validation, rollback and large-file tests |
| 11 | Audit trails, observability, backup/restore | Actor attribution, structured logs, restore drill |
| 12 | Security, load, accessibility and production packaging | End-to-end test matrix, release checklist |

## Progress

Groups 1–3 passed native CI. Group 4 adds SQLite-atomic receipts/issues/returns/corrections with operation deduplication and actor-attributed movement history. Acceptance requires the new concurrency and rollback HTTP tests to pass.

## Constraints

Use Gungnir for application logic and native runtime, SQLite in the first implementation, Spear for simple SSR pages. Do not add Next.js, React or Node dependencies until a concrete frontend requirement justifies them. Node 24 applies if a Node-based frontend is later introduced. Do not automatically deploy or publish releases.
