"""Group 5 static invariants supplement the real SQLite HTTP smoke tests."""
import pathlib
import unittest

ROOT=pathlib.Path(__file__).resolve().parents[1]
def content(path):
    return (ROOT/path).read_text(encoding="utf-8")

class WarehouseContractTests(unittest.TestCase):
    def test_additive_migration_and_fk(self):
        names=sorted(p.name for p in (ROOT/"database/migrations").glob("*.gnr"))
        self.assertEqual(names[-1],"create_zzz_warehouse_locations_table.gnr")
        migration=content("database/migrations/create_zzz_warehouse_locations_table.gnr")
        for table in ("warehouses","warehouse_locations","location_balances",
                      "location_movements","warehouse_transfers",
                      "inventory_location_operations"):
            self.assertIn("Table::create('"+table+"'",migration)
        self.assertIn("table.unique(['product_id', 'location_id'])",migration)

    def test_global_stock_cannot_fall_below_locations(self):
        native=content("bootstrap/inventory_transactions.hpp")
        self.assertIn("SELECT COALESCE(SUM(quantity), 0)",native)
        self.assertIn('Insufficient unallocated stock',native)
        self.assertIn("Reference belongs to a location-specific movement",native)
        self.assertIn("shared_gate()",native)

    def test_all_location_mutations_are_atomic(self):
        native=content("bootstrap/inventory_locations.hpp")
        for name in ("allocate", "transfer", "location_movement"):
            self.assertIn("Response "+name+"(Request& request, std::mutex& gate)",native)
        self.assertEqual(native.count("DB::transaction([&]() -> Response"),3)
        self.assertIn("ON CONFLICT(reference) DO NOTHING",native)
        self.assertIn("INSERT INTO location_movements",native)
        self.assertIn("INSERT INTO warehouse_transfers",native)
        self.assertIn("UPDATE products SET stock_on_hand=?",native)
        self.assertIn("WHERE product_id=? AND location_id=? AND quantity=?",native)
        self.assertIn('require_manager(request)',native)

    def test_routes_behind_group4_middleware(self):
        app=content("bootstrap/app.hpp")
        self.assertIn('inventory::locations::register_routes(app)',app)
        self.assertLess(app.index("http::csrf()"),app.index("inventory::locations::register_routes(app)"))
        self.assertIn('inventory::transactions::register_routes(app)',app)
