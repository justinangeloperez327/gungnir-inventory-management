"""Group 4 invariants enforced in addition to native SQLite integration tests."""
import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]


def read(path):
    return (ROOT / path).read_text(encoding="utf-8")


class InventoryContractTests(unittest.TestCase):
    def test_migration_is_additive_and_late(self):
        names = sorted(x.name for x in (ROOT / "database/migrations").glob("*.gnr"))
        self.assertEqual(names[-3:], ["create_zz_inventory_operations_table.gnr",
                                      "create_zzz_warehouse_locations_table.gnr",
                                      "create_zzzz_procurement_table.gnr"])
        migration = read("database/migrations/create_zz_inventory_operations_table.gnr")
        self.assertIn("table.string('reference', 64).unique()", migration)
        self.assertIn("table.foreign('actor_id').references('id').on('users')", migration)
        self.assertIn("table.foreign('product_id').references('id').on('products')", migration)

    def test_atomic_write_and_fingerprint(self):
        source = read("bootstrap/inventory_transactions.hpp")
        self.assertIn("DB::transaction(", source)
        self.assertIn("ON CONFLICT(reference) DO NOTHING", source)
        self.assertIn("stock_on_hand = ?", source)
        self.assertIn("stock_on_hand = ? AND active = 1", source)
        self.assertIn("INSERT INTO stock_movements", source)
        self.assertIn("Reference already used for another operation", source)
        self.assertIn("std::lock_guard lock{gate}", source)

    def test_auth_csrf_boundaries(self):
        bootstrap = read("bootstrap/app.hpp")
        self.assertIn("inventory::transactions::register_routes(app)", bootstrap)
        self.assertLess(bootstrap.index("session::middleware"), bootstrap.index("http::csrf()"))
        self.assertLess(bootstrap.index("http::csrf()"), bootstrap.index("auth::guard(guard)"))
        source = read("bootstrap/inventory_transactions.hpp")
        self.assertIn('actor->role("admin")', source)
        self.assertIn('actor->role("manager")', source)
        self.assertIn("request.authenticated()", source)
        self.assertIn("request.is_json()", source)

    def test_catalog_endpoints_still_cannot_assign_stock(self):
        catalog = read("app/controllers/ProductController.gnr")
        self.assertIn("stock_on_hand: 0", catalog)
        self.assertNotIn("request.input('stock_on_hand')", catalog)
        self.assertNotIn("'stock_on_hand': '", catalog)
        self.assertNotIn("'stock_on_hand': 'required", catalog)


if __name__ == "__main__":
    unittest.main()
