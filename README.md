# Gungnir Inventory Management

A native C++23 inventory management application written in Gungnir's structured `.gnr` language.

> **Status:** Group 1 foundation. The application currently exposes read-only product pages/API, a health endpoint, and inventory database schema. Authentication and stock-changing endpoints are intentionally not implemented. **Do not expose this prototype as a production inventory service.**

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

## Routes

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/` | Welcome/dashboard placeholder |
| GET | `/products` | Read-only server-rendered product list |
| GET | `/api/products` | Read-only JSON list (first 100 products) |
| GET | `/health` | Process-level health response (not database readiness) |

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

The GitHub Actions workflow also builds and migrates a fresh SQLite database, then runs a local HTTP smoke test.

## Policy

- Make database changes through versioned migrations.
- Prices are integer minor units, not floating-point money.
- Inventory writes must be atomic, validated, audited, and authorization-checked.
- No automatic releases or deployments.
- This repository is an application and must not vendor generated `.gungnir/` code.
