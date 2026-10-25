"""Real HTTP and SQLite integration checks for Group 3 catalog CRUD."""
import http.cookiejar
import json
import os
from pathlib import Path
import re
import signal
import sqlite3
import subprocess
from smoke_process import stop
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
BASE = "http://127.0.0.1:8000"
PASSWORD = "CI-Only-Inventory-Passphrase-12345!"


def client():
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
    return opener


def call(opener, path, method="GET", payload=None, csrf=None):
    headers = {}
    body = None
    if payload is not None:
        headers["Content-Type"] = "application/json"
        body = json.dumps(payload).encode("utf-8")
    if csrf is not None:
        headers["X-CSRF-Token"] = csrf
    request = urllib.request.Request(BASE + path, data=body, headers=headers, method=method)
    try:
        with opener.open(request, timeout=7) as response:
            return response.status, response.read().decode("utf-8")
    except urllib.error.HTTPError as error:
        return error.code, error.read().decode("utf-8")


def login(role):
    opener = client()
    code, form = call(opener, "/login")
    assert code == 200, (role, code)
    token = re.search(r'name="_token" value="([a-zA-Z0-9]+)"', form).group(1)
    body = urllib.parse.urlencode({
        "_token": token, "email": role + "@inventory.test", "password": PASSWORD
    }).encode()
    with opener.open(urllib.request.Request(
        BASE + "/login", data=body, headers={"Content-Type": "application/x-www-form-urlencoded"}
    ), timeout=7) as response:
        assert response.status == 200
    code, dashboard = call(opener, "/")
    assert code == 200 and "Signed in:" in dashboard
    token = re.search(r'name="_token" value="([a-zA-Z0-9]+)"', dashboard).group(1)
    return opener, token


def assert_code(opener, path, method, expected, payload=None, token=None):
    code, body = call(opener, path, method, payload, token)
    assert code == expected, (path, method, "expected", expected, "got", code, body[:500])
    return json.loads(body) if body.strip().startswith("{") else body


def run():
    anonymous = client()
    viewer, viewer_token = login("viewer")
    manager, token = login("manager")
    admin, admin_token = login("admin")

    assert_code(anonymous, "/api/categories", "GET", 401)
    assert_code(anonymous, "/api/products", "GET", 401)
    assert_code(manager, "/api/categories", "POST", 419, {"code": "ENG", "name": "Engineering"})
    assert_code(viewer, "/api/categories", "POST", 403, {"code": "ENG", "name": "Engineering"}, viewer_token)
    assert_code(viewer, "/api/products", "POST", 403, {"sku": "ITEM-1"}, viewer_token)
    assert_code(manager, "/api/categories", "POST", 415, token=token)
    assert_code(manager, "/api/categories", "POST", 422, {"code": "", "name": ""}, token)

    category = assert_code(manager, "/api/categories", "POST", 201,
                           {"code": "ENG", "name": "Engineering"}, token)
    cid = category["id"]
    assert category["code"] == "ENG"
    assert_code(manager, "/api/categories", "POST", 422,
                {"code": "ENG", "name": "Duplicate"}, token)
    assert_code(manager, "/api/products", "POST", 422,
                {"sku": "BAD", "name": "Bad", "category_id": 999999, "unit_price_minor": 5,
                 "reorder_level": 0}, token)
    assert_code(manager, "/api/products", "POST", 422,
                {"sku": "BAD", "name": "Bad", "category_id": cid, "unit_price_minor": -1,
                 "reorder_level": 0}, token)

    product = assert_code(manager, "/api/products", "POST", 201,
                          {"sku": "ITEM-0001", "name": "A Panel",
                           "category_id": cid, "unit_price_minor": 25050,
                           "reorder_level": 3, "stock_on_hand": 90000, "active": False}, token)
    pid = product["id"]
    assert product["stock_on_hand"] == 0 and product["active"] is True, product
    assert_code(manager, "/api/products", "POST", 422,
                {"sku": "ITEM-0001", "name": "Duplicate",
                 "category_id": cid, "unit_price_minor": 1, "reorder_level": 0}, token)
    assert_code(viewer, "/api/products/" + str(pid), "PUT", 403,
                {"sku": "HACK"}, viewer_token)
    assert_code(manager, "/api/categories/" + str(cid), "DELETE", 409, token=token)

    updated = assert_code(manager, "/api/products/" + str(pid), "PUT", 200,
                          {"sku": "ITEM-0002", "name": "Renamed Panel",
                           "category_id": cid, "unit_price_minor": 50000, "reorder_level": 2,
                           "stock_on_hand": 30000, "active": False}, token)
    assert updated["sku"] == "ITEM-0002" and updated["unit_price_minor"] == 50000, updated
    assert updated["stock_on_hand"] == 0 and updated["active"] is True, updated
    assert_code(admin, "/api/products/" + str(pid), "DELETE", 204, token=admin_token)
    catalog = assert_code(viewer, "/api/products", "GET", 200)
    assert not catalog["data"], catalog
    archived = assert_code(viewer, "/api/products/" + str(pid), "GET", 200)
    assert archived["active"] is False
    assert_code(manager, "/api/products/" + str(pid) + "/restore", "POST", 200, token=token)

    category = assert_code(manager, "/api/categories/" + str(cid), "PUT", 200,
                           {"code": "ENG-R", "name": "Engineering Revised"}, token)
    assert category["code"] == "ENG-R", category
    assert_code(manager, "/api/categories/999999", "GET", 404)

    # 21 categories exercise the bounded cursor path, without relying on OFFSET.
    for i in range(21):
        assert_code(manager, "/api/categories", "POST", 201,
                    {"code": "CAT-" + str(i).zfill(3), "name": "Category " + str(i)}, token)
    page1 = assert_code(viewer, "/api/categories", "GET", 200)
    assert len(page1["data"]) == 20 and page1["has_more"] is True
    cursor = page1["next_cursor"]
    assert cursor == "CAT-019", page1
    page2 = assert_code(viewer, "/api/categories?after=" + urllib.parse.quote(cursor), "GET", 200)
    assert len(page2["data"]) == 2 and page2["has_more"] is False
    assert [c["code"] for c in page2["data"]] == ["CAT-020", "ENG-R"]

    # Update with colliding code must not alter the stored row.
    duplicate = assert_code(manager, "/api/categories", "POST", 201,
                            {"code": "SPARE", "name": "Spare"}, token)
    assert_code(manager, "/api/categories/" + str(duplicate["id"]), "PUT", 409,
                {"code": "ENG-R", "name": "Collision"}, token)
    assert_code(manager, "/api/categories/" + str(duplicate["id"]), "DELETE", 204, token=token)
    assert_code(viewer, "/api/categories/" + str(duplicate["id"]), "GET", 404)

    with sqlite3.connect(ROOT / "storage" / "inventory.sqlite") as db:
        stored = db.execute("SELECT stock_on_hand, active, sku FROM products WHERE id = ?", (pid,)).fetchone()
        assert stored == (0, 1, "ITEM-0002"), stored
        assert db.execute("SELECT COUNT(*) FROM stock_movements").fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM products WHERE sku = 'BAD'").fetchone()[0] == 0

    print("PASS: catalog CRUD, role and CSRF boundaries, validation, stock isolation, archive, and cursors")


def main():
    logfile = ROOT / "storage" / "catalog-smoke.log"
    with logfile.open("w", encoding="utf-8") as log:
        process = subprocess.Popen(["gungnir", "run"], cwd=ROOT, stdout=log,
                                   stderr=subprocess.STDOUT, start_new_session=(os.name != "nt"))
        try:
            deadline = time.monotonic() + 90
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    raise RuntimeError("Application exited unexpectedly")
                try:
                    if call(client(), "/health")[0] == 200:
                        break
                except (OSError, ValueError):
                    time.sleep(0.5)
            else:
                raise RuntimeError("Application did not start")
            run()
        except Exception:
            print(logfile.read_text(encoding="utf-8"), file=sys.stderr)
            raise
        finally:
            stop(process)


if __name__ == "__main__":
    main()
