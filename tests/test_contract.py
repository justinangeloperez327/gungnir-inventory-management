import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]


def source(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


class FoundationContractTests(unittest.TestCase):
    def test_structured_project_marker(self):
        self.assertIn("profile=structured", source(".gungnir-project"))

    def test_read_only_http_surface(self):
        routes = [line.strip() for line in source("routes/web.gnr").splitlines() if line.strip()]
        self.assertEqual(len(routes), 4)
        self.assertTrue(all(line.startswith("Route::get(") for line in routes))
        for endpoint in ("'/'", "'/products'", "'/api/products'", "'/health'"):
            self.assertTrue(any(endpoint in line for line in routes))

    def test_schema_dependency_order(self):
        files = sorted(p.name for p in (ROOT / "database/migrations").glob("*.gnr"))
        self.assertEqual(files, [
            "create_categories_table.gnr",
            "create_products_table.gnr",
            "create_stock_movements_table.gnr",
        ])
        self.assertIn("table.foreign('category_id')", source("database/migrations/create_products_table.gnr"))
        self.assertIn("table.foreign('product_id')", source("database/migrations/create_stock_movements_table.gnr"))

    def test_stock_changes_not_implemented_without_auth(self):
        controllers = "\n".join(f.read_text(encoding="utf-8") for f in (ROOT / "app/controllers").glob("*.gnr"))
        for forbidden in ("::create(", ".save(", ".remove(", "::update(", "::delete("):
            self.assertNotIn(forbidden, controllers)


if __name__ == "__main__":
    unittest.main()
