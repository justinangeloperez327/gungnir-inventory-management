# Gungnir Inventory Management

A native C++23 inventory management application written in Gungnir's structured `.gnr` language.

> **Status:** Group 2 authentication prototype. SQLite users, credential checking, session login/logout, CSRF and role-protected routes are included. Stock-changing operations remain intentionally unavailable. **Do not deploy publicly:** sessions are currently process-local, and production security controls are incomplete.

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
| GET | `/api/products` | Authenticated JSON list (first 100 products) |
| GET | `/health` | Public process-level health (not database readiness) |
| GET | `/login` | Session + CSRF sign-in form |
| POST | `/login` | Credential sign-in (rate limited) |
| POST | `/logout` | Session logout (CSRF-protected) |
| GET | `/api/me` | Authenticated public user identity |
| GET | `/api/management/status` | Admin / manager authorization check |
| GET | `/api/admin/status` | Admin-only authorization check |

The initial database schema includes `categories`, `products` (integer prices in minor units and a stock-on-hand counter), and `stock_movements` (quantity deltas for auditability). No stock changes are accepted until we have transactional services, concurrency tests, input validation, and authentication. Detailed delivery gates: [docs/ROADMAP.md](docs/ROADMAP.md), [docs/DOMAIN.md](docs/DOMAIN.md).

## Verify

On a machine with the Application SDK installed:

```sh
python3 -m unittest discover -s tests -v
gungnirc --project . --check
gungnirc --project . --verify-ir
gungnir build
gungnir migrate:plan
```

The GitHub Actions workflow additionally provisions CI-only test users, exercises login, CSRF, session rotation, logout and role separation, and runs native HTTP smoke tests.

## Policy

- Make database changes through versioned migrations.
- Prices are integer minor units, not floating-point money.
- Inventory writes must be atomic, validated, audited, and authorization-checked.
- No automatic releases or deployments.
- This repository is an application and must not vendor generated `.gungnir/` code.
