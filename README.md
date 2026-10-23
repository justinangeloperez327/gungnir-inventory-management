# Gungnir Inventory Management

A native C++23 inventory management application written in Gungnir's structured `.gnr` language.

> **Status:** Group 5 warehouse/location tracking prototype. Authenticated users can inspect warehouse stock, and managers/admins can allocate unlocated inventory, perform location-specific adjustments and transfer stock between warehouse locations. All location and global stock mutations commit atomically. **Do not deploy publicly:** sessions remain process-local and production security controls are incomplete.

## Requirements

- [Gungnir v1.0.2 Application SDK](https://github.com/justinangeloperez327/gungnir/releases/tag/v1.0.2) (not Core SDK): bundles SQLite persistence.
- C++23 compiler and CMake 3.25+.
- Linux or Windows matching the SDK toolchain.

## Start

```sh
git clone https://github.com/justinangeloperez327/gungnir-inventory-management.git
cd gungnir-inventory-management
cp .env.example .env # on Windows, use: copy .env.example .env
gungnirc --project . --check
gungnirc --project . --verify-ir
gungnir build
gungnir migrate:plan
gungnir migrate
gungnir run
```

Open [http://127.0.0.1:8000](http://127.0.0.1:8000). The default SQLite database is `storage/inventory.sqlite` (the `storage/` directory is included). Use `gungnir dev` for source-change rebuilds.

## Provision the first account

After migrations, build the **native provisioning tool** and create an admin:

```sh
cmake -S tools -B build/provision -DCMAKE_PREFIX_PATH=/path/to/gungnir-application
cmake --build build/provision --config Release
python3 scripts/provision_user.py --role admin
```

The provisioning script prompts for an email, name and password (12–128 characters), then creates an account with a Gungnir-hashed password. No credentials are committed or included in production examples. Create `viewer` or `manager` accounts via the same local tool. See [Authentication](docs/AUTHENTICATION.md).

## Routes

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/` | Authenticated dashboard |
| GET | `/products` | Authenticated product list |
| GET | `/api/products` | Authenticated cursor-paginated product list (20/page) |
| GET | `/health` | Public process-level health (not database readiness) |
| GET | `/login` | Session + CSRF sign-in form |
| POST | `/login` | Credential sign-in (rate limited) |
| POST | `/logout` | Session logout (CSRF-protected) |
| GET | `/api/me` | Authenticated public user identity |
| GET | `/api/management/status` | Admin / manager authorization check |
| GET | `/api/admin/status` | Admin-only authorization check |
| GET | `/categories` | Categories page (20/cursor) |
| GET | `/api/categories` | Category list (20/cursor) |
| GET | `/api/categories/{category}` | Category detail |
| POST | `/api/categories` | Create category (manager/admin) |
| PUT | `/api/categories/{category}` | Update category (manager/admin) |
| DELETE | `/api/categories/{category}` | Delete category only when empty |
| GET | `/api/products/{product}` | Product detail including archives |
| POST | `/api/products` | Create zero-stock product (manager/admin) |
| PUT | `/api/products/{product}` | Update product metadata (manager/admin) |
| DELETE | `/api/products/{product}` | Archive product (manager/admin) |
| POST | `/api/products/{product}/restore` | Reactivate archived product |
| POST | `/api/inventory/movements` | Transactional receipt/issue/return/correction (manager/admin) |
| GET | `/api/inventory/products/{product}/movements` | Paginated signed movement ledger (authenticated) |
| GET / POST | `/api/warehouses` | List warehouses / create warehouse (manager/admin) |
| GET / POST | `/api/locations` | List locations / create location (manager/admin) |
| GET | `/api/inventory/products/{product}/balances` | Total, located and unallocated stock |
| POST | `/api/inventory/allocations` | Allocate existing unlocated stock to a location |
| POST | `/api/inventory/transfers` | Atomic inter-location transfer, no global change |
| POST | `/api/inventory/location-movements` | Located receipt/issue/return/correction |
| GET | `/api/inventory/locations/{location}/movements` | Paginated location ledger |

Catalog create/update calls require JSON bodies and a session CSRF token in the `X-CSRF-Token` header. `after` paginates by unique category code or SKU; responses expose `data`, `has_more`, and `next_cursor`. Catalog CRUD does not change stock quantities.

The initial database schema includes `categories`, `products` (integer prices in minor units and a stock-on-hand counter), and `stock_movements` (quantity deltas for auditability). Stock movements must go through the Group 4 [transaction API](docs/INVENTORY_TRANSACTIONS.md) or the Group 5 [warehouse API](docs/WAREHOUSES.md). Group 4 requests without a location only affect **unallocated** stock; they cannot decrease global inventory below the quantity held in warehouses. Group 5 allocation and transfers preserve global stock; located receipts/issues/corrections atomically adjust the location and global total. Never modify `stock_on_hand` using catalog CRUD. Detailed delivery gates: [docs/ROADMAP.md](docs/ROADMAP.md), [docs/DOMAIN.md](docs/DOMAIN.md).

## Verify

On a machine with the Application SDK installed:

```sh
python3 -m unittest discover -s tests -v
gungnirc --project . --check
gungnirc --project . --verify-ir
gungnir build
gungnir migrate:plan
```

The GitHub Actions workflow additionally provisions CI-only test users, exercises login, CSRF, session rotation, logout and role separation, and runs native HTTP smoke tests, including transactional inventory concurrency and rollback scenarios.

## Policy

- Make database changes through versioned migrations.
- Prices are integer minor units, not floating-point money.
- Inventory writes must be atomic, validated, audited, and authorization-checked.
- No automatic releases or deployments.
- This repository is an application and must not vendor generated `.gungnir/` code.
