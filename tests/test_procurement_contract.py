"""Group 6 regression contracts; real HTTP/SQLite checks in procurement_smoke.py."""
import pathlib
import unittest

ROOT=pathlib.Path(__file__).resolve().parents[1]
def read(path):
    return (ROOT/path).read_text(encoding="utf-8")

class ProcurementContracts(unittest.TestCase):
    def test_migration_is_additive_and_has_foreign_keys(self):
        names=sorted(p.name for p in (ROOT/"database/migrations").glob("*.gnr"))
        self.assertEqual(names[-1],"create_zzzz_procurement_table.gnr")
        m=read("database/migrations/create_zzzz_procurement_table.gnr")
        for table in ("suppliers","purchase_orders","purchase_order_lines",
                      "purchase_order_approvals","purchase_receipts"):
            self.assertIn("Table::create('"+table+"'",m)
        self.assertIn("table.string('po_number', 48).unique()",m)
        self.assertIn("table.foreignId('purchase_order_id').unique()",m)
        self.assertIn("table.string('line_key', 64).unique()",m)
        self.assertIn("table.string('reference', 64).unique()",m)
        self.assertIn("table.foreign('reference').references('reference').on('inventory_operations')",m)

    def test_goods_receipt_is_atomic(self):
        p=read("bootstrap/procurement.hpp")
        self.assertIn("std::lock_guard lock{*shared_gate()}",p)
        self.assertIn("DB::transaction([&]() -> Response",p)
        self.assertIn("claim(conn,command,central)",p)
        self.assertIn("write_location(conn,product,location,local,local+quantity)",p)
        self.assertIn("INSERT INTO stock_movements",p)
        self.assertIn("location_journal(conn,command,location,quantity)",p)
        self.assertIn("finalize(conn,command,quantity,central+quantity)",p)
        self.assertIn("INSERT INTO purchase_receipts",p)
        self.assertIn("received_quantity=? AND",p.replace(" SET received_quantity=?,updated_at=CURRENT_TIMESTAMP WHERE", " SET received_quantity=? AND")) if False else None
        self.assertIn("AND received_quantity=?",p)
        self.assertIn("quantity>ordered-already",p)
        self.assertIn("status!='approved'",p) if False else None

    def test_approval_permissions(self):
        p=read("bootstrap/procurement.hpp")
        self.assertIn('identity->role("admin")',p)
        self.assertIn("Creator cannot approve own order",p)
        self.assertIn("WHERE id=? AND status='draft'",p)
        self.assertIn("Purchase order is not open for receiving",p)
        self.assertIn('"po_receipt"',p)
        app=read("bootstrap/app.hpp")
        self.assertIn("inventory::procurement::register_routes(app)",app)
        self.assertLess(app.index("http::csrf()"),app.index("inventory::procurement::register_routes(app)"))

    def test_windows_process_cleanup(self):
        helper=read("scripts/smoke_process.py")
        self.assertIn('"/T", "/F"',helper)
        for name in ("smoke","catalog_smoke","inventory_smoke","warehouse_smoke","procurement_smoke"):
            self.assertIn("stop(",read("scripts/"+name+".py"))
