"""Group 4: real SQLite-backed transactional inventory and concurrent HTTP tests."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import json
import os
import signal
import sqlite3
import subprocess
from smoke_process import stop
import sys
import time
from catalog_smoke import client, call, login, assert_code

ROOT = Path(__file__).resolve().parents[1]
BASE = "/api/inventory"


def movement(opener, token, kind, quantity, reference, product_id, reason="verified"):
    return call(opener, BASE + "/movements", "POST", {
        "kind": kind, "quantity": quantity, "reference": reference,
        "reason": reason, "product_id": product_id,
    }, token)


def stock(product_id):
    with sqlite3.connect(ROOT / "storage" / "inventory.sqlite") as db:
        row = db.execute("SELECT stock_on_hand FROM products WHERE id=?", (product_id,)).fetchone()
        return row[0] if row else None


def check():
    viewer, viewer_token = login("viewer")
    manager, token = login("manager")
    admin, admin_token = login("admin")
    anonymous = client()
    assert_code(anonymous, BASE + "/products/1/movements", "GET", 401)
    assert_code(anonymous, BASE + "/movements", "POST", 419)
    assert_code(viewer, BASE + "/movements", "POST", 403, {
        "product_id": 1, "kind": "receipt", "quantity": 1,
        "reason": "blocked", "reference": "VIEWER-DENY-001"
    }, viewer_token)
    assert_code(manager, BASE + "/movements", "POST", 419,
                {"kind": "receipt", "quantity": 1})
    assert_code(manager, BASE + "/movements", "POST", 415, token=token)

    category = assert_code(manager, "/api/categories", "POST", 201, {
        "code": "INV-G4", "name": "Inventory transaction tests"
    }, token)
    product = assert_code(manager, "/api/products", "POST", 201, {
        "sku": "INV-G4-PRODUCT", "name": "Stock-concurrency specimen",
        "category_id": category["id"], "unit_price_minor": 2250,
        "reorder_level": 2
    }, token)
    pid = product["id"]
    assert stock(pid) == 0

    invalid = {
        "kind": "issue", "quantity": 5, "reference": "MOVE-BAD-0001",
        "product_id": pid, "reason": "invalid", "stock_on_hand": 9999,
    }
    assert_code(manager, BASE + "/movements", "POST", 422, invalid, token)
    assert_code(manager, BASE + "/movements", "POST", 422, {
        "kind": "receipt", "quantity": 0, "reference": "MOVE-BAD-0002",
        "product_id": pid, "reason": "invalid",
    }, token)
    assert_code(manager, BASE + "/movements", "POST", 422, {
        "kind": "correction", "quantity": -1, "reference": "MOVE-BAD-0003",
        "product_id": pid, "reason": "invalid",
    }, token)
    assert_code(manager, BASE + "/movements", "POST", 422, {
        "kind": "receipt", "quantity": 1, "reference": "MOVE-BAD-0004",
        "product_id": pid, "reason": "bad\nreason",
    }, token)
    assert_code(manager, BASE + "/movements", "POST", 404, {
        "kind": "receipt", "quantity": 1, "reference": "MOVE-MISSING-0001",
        "product_id": 999999, "reason": "missing",
    }, token)

    def expect(kind, qty, ref, after, delta, auth=manager, csrf=token):
        code, payload = movement(auth, csrf, kind, qty, ref, pid)
        assert code == 201, (code, payload)
        result = json.loads(payload)
        assert result["stock_after"] == after and result["quantity_delta"] == delta, result
        assert result["replayed"] is False
        assert stock(pid) == after
        return result

    receipt = expect("receipt", 10, "MOVE-RCPT-0001", 10, 10)
    code, body = movement(manager, token, "receipt", 10, "MOVE-RCPT-0001", pid)
    assert code == 200 and json.loads(body)["replayed"] is True
    assert json.loads(body)["stock_after"] == 10
    assert_code(manager, BASE + "/movements", "POST", 409, {
        "kind": "issue", "quantity": 10, "reference": "MOVE-RCPT-0001",
        "product_id": pid, "reason": "verified",
    }, token)
    assert_code(manager, BASE + "/movements", "POST", 409, {
        "kind": "receipt", "quantity": 10, "reference": "MOVE-RCPT-0001",
        "product_id": pid, "reason": "DIFFERENT",
    }, token)

    expect("issue", 3, "MOVE-ISSUE-0001", 7, -3)
    expect("return", 2, "MOVE-RETURN-0001", 9, 2)
    expect("correction", 4, "MOVE-CORR-0001", 4, -5)
    code, body = movement(manager, token, "issue", 5, "MOVE-OVERSELL-0001", pid)
    assert code == 409, (code, body)
    assert stock(pid) == 4
    code, body = movement(manager, token, "correction", 4, "MOVE-NOOP-0001", pid)
    assert code == 409
    expect("correction", 0, "MOVE-CORR-0002", 0, -4)
    code, body = movement(manager, token, "issue", 1, "MOVE-ZERO-0001", pid)
    assert code == 409 and stock(pid) == 0

    # Race distinct references against exactly one remaining unit.
    expect("receipt", 1, "MOVE-RACE-SEED", 1, 1)
    with ThreadPoolExecutor(max_workers=4) as threads:
        futures = [
            threads.submit(movement, manager, token, "issue", 1,
                           "MOVE-RACE-" + str(i).zfill(4), pid)
            for i in range(4)
        ]
        results = [future.result(timeout=20) for future in futures]
    assert [code for code, _ in results].count(201) == 1, results
    assert [code for code, _ in results].count(409) == 3, results
    assert stock(pid) == 0

    # The reference is globally unique: concurrent equivalent retries
    # must commit exactly one stock increase and one ledger entry.
    with ThreadPoolExecutor(max_workers=4) as threads:
        futures = [
            threads.submit(movement, manager, token, "receipt", 2,
                           "MOVE-RACE-IDEMPOTENT", pid)
            for _ in range(4)
        ]
        results = [future.result(timeout=20) for future in futures]
    assert [code for code, _ in results].count(201) == 1, results
    assert [code for code, _ in results].count(200) == 3, results
    assert stock(pid) == 2

    other = assert_code(admin, "/api/products", "POST", 201, {
        "sku": "INV-G4-SECOND", "name": "Other stock",
        "category_id": category["id"], "unit_price_minor": 1, "reorder_level": 0
    }, admin_token)
    assert_code(manager, BASE + "/movements", "POST", 409, {
        "kind": "receipt", "quantity": 2, "reference": "MOVE-RACE-IDEMPOTENT",
        "product_id": other["id"], "reason": "verified",
    }, token)
    assert stock(other["id"]) == 0

    # More than 20 ledger rows verify stable ascending ID cursors.
    for i in range(25):
        expect("receipt", 1, "MOVE-PAGE-" + str(i).zfill(4), 3 + i, 1)
    page1 = assert_code(viewer, BASE + f"/products/{pid}/movements", "GET", 200)
    assert len(page1["data"]) == 20 and page1["has_more"], page1
    page2 = assert_code(viewer, BASE + f"/products/{pid}/movements?after={page1['next_cursor']}", "GET", 200)
    assert len(page2["data"]) >= 12 and not page2["has_more"], page2
    entries = page1["data"] + page2["data"]
    assert entries[0]["quantity_delta"] == 10 and entries[0]["kind"] == "receipt", entries[0]
    assert entries[0]["actor_id"] and entries[0]["reference"] == "MOVE-RCPT-0001"
    assert len({row["reference"] for row in entries}) == len(entries)
    assert_code(viewer, BASE + f"/products/{pid}/movements?after=garbage", "GET", 422)

    # The archived-product guard must preserve the entire ledger history.
    assert_code(admin, "/api/products/" + str(pid), "DELETE", 204, token=admin_token)
    assert_code(manager, BASE + "/movements", "POST", 409, {
        "kind": "receipt", "quantity": 1, "reference": "MOVE-ARCHIVE-0001",
        "product_id": pid, "reason": "verified",
    }, token)
    assert_code(viewer, BASE + f"/products/{pid}/movements", "GET", 200)

    with sqlite3.connect(ROOT / "storage" / "inventory.sqlite") as db:
        total = db.execute(
            "SELECT COALESCE(SUM(quantity_delta), 0) FROM stock_movements WHERE product_id=?",
            (pid,),
        ).fetchone()[0]
        assert total == stock(pid), (total, stock(pid))
        assert db.execute(
            "SELECT COUNT(*) FROM stock_movements WHERE reference='MOVE-RACE-IDEMPOTENT'"
        ).fetchone()[0] == 1
        assert db.execute(
            "SELECT COUNT(*) FROM inventory_operations WHERE reference='MOVE-RACE-IDEMPOTENT'"
        ).fetchone()[0] == 1
        assert db.execute(
            "SELECT COUNT(*) FROM inventory_operations WHERE reference='MOVE-OVERSELL-0001'"
        ).fetchone()[0] == 0
        assert db.execute(
            "SELECT COUNT(*) FROM stock_movements WHERE reference='MOVE-ARCHIVE-0001'"
        ).fetchone()[0] == 0
        assert db.execute(
            "SELECT COUNT(*) FROM stock_movements WHERE product_id=?",
            (pid,),
        ).fetchone()[0] == len(entries)
    print("PASS: stock transactions, idempotency, role/CSRF, archive, audit, concurrency, and pagination")


def main():
    logfile = ROOT / "storage" / "inventory-smoke.log"
    with logfile.open("w", encoding="utf-8") as log:
        proc = subprocess.Popen(["gungnir", "run"], cwd=ROOT, stdout=log,
                                stderr=subprocess.STDOUT, start_new_session=(os.name != "nt"))
        try:
            limit = time.monotonic() + 80
            while time.monotonic() < limit:
                if proc.poll() is not None:
                    raise RuntimeError("Server exited before it was ready")
                try:
                    if call(client(), "/health")[0] == 200:
                        break
                except (OSError, ValueError):
                    time.sleep(0.3)
            else:
                raise RuntimeError("Server did not become ready")
            check()
        except Exception:
            print(logfile.read_text(encoding="utf-8"), file=sys.stderr)
            raise
        finally:
            stop(proc)


if __name__ == "__main__":
    main()
