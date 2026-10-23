import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]


def source(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


class Group3ContractTests(unittest.TestCase):
    def test_structured_project_marker(self):
        self.assertIn("profile=structured", source(".gungnir-project"))

    def test_only_expected_public_endpoints(self):
        routes = source("routes/web.gnr")
        self.assertEqual(routes.count("Route::"), 21)
        self.assertIn("Route::get('/health', HomeController::health);", routes)
        self.assertIn("Route::get('/login', AuthController::index);", routes)
        self.assertIn("Route::post('/login', AuthController::store)", routes)
        for protected in ("/products", "/api/products", "/api/me"):
            route = next(line for line in routes.splitlines() if f"'{protected}'" in line)
            self.assertIn("middleware(RequireLoginMiddleware)", route)
        self.assertIn("RequireAdminMiddleware", routes)
        self.assertIn("RequireManagerMiddleware", routes)
        for path in ("/api/categories", "/api/products"):
            self.assertIn(f"Route::post('{path}'", routes)
        for line in routes.splitlines():
            if line.startswith(("Route::post('/api/categories", "Route::put('/api/categories",
                                "Route::delete('/api/categories", "Route::post('/api/products",
                                "Route::put('/api/products", "Route::delete('/api/products")):
                self.assertIn("middleware(RequireManagerMiddleware)", line)

    def test_schema_dependency_order(self):
        files = sorted(p.name for p in (ROOT / "database/migrations").glob("*.gnr"))
        self.assertEqual(files, [
            "create_categories_table.gnr",
            "create_products_table.gnr",
            "create_stock_movements_table.gnr",
            "create_users_table.gnr",
        ])
        self.assertIn("table.foreign('category_id')", source("database/migrations/create_products_table.gnr"))
        self.assertIn("table.foreign('product_id')", source("database/migrations/create_stock_movements_table.gnr"))
        self.assertIn("table.string('email', 254).unique()", source("database/migrations/create_users_table.gnr"))

    def test_credentials_never_serialized(self):
        self.assertIn("hidden = ['password_hash']", source("app/models/User.gnr"))
        self.assertNotIn("password_hash", source("app/controllers/AuthController.gnr"))
        self.assertIn("Password::hash(password)", source("tools/provision_user.cpp"))

    def test_login_security_order(self):
        bootstrap = source("bootstrap/app.hpp")
        order = [
            "session::middleware(session_store, session_options)",
            "http::csrf()",
            "auth::session(identities)",
            "auth::guard(guard)",
        ]
        self.assertEqual(sorted(bootstrap.index(part) for part in order),
                         [bootstrap.index(part) for part in order])
        self.assertIn("auth.logout(request)", source("app/controllers/AuthController.gnr"))

    def test_stock_writes_are_still_disabled(self):
        controllers = "\n".join(f.read_text(encoding="utf-8") for f in (ROOT / "app/controllers").glob("*.gnr"))
        product = source("app/controllers/ProductController.gnr")
        self.assertIn("stock_on_hand: 0", product)
        self.assertNotIn("request.input('stock_on_hand')", product)
        self.assertNotIn("'stock_on_hand': '", product)
        self.assertIn("stock_on_hand: 0", product)
        self.assertNotIn("stock_on_hand:", product.split("update(Request request")[1].split("destroy(Request request")[0])
        self.assertNotIn("Route::post('/api/stock", source("routes/web.gnr"))

    def test_cursor_bounds(self):
        for name in ("ProductController", "CategoryController"):
            body = source(f"app/controllers/{name}.gnr")
            self.assertIn(".limit(21).get()", body)
            self.assertIn(".take(20)", body)
            self.assertIn("next_cursor", body)


if __name__ == "__main__":
    unittest.main()
