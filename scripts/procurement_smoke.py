"""Group 6 real HTTP + SQLite procurement acceptance test, including concurrent receipts."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import json
import os
import signal
import sqlite3
import subprocess
import sys
import time

from catalog_smoke import client, call, login, assert_code
from smoke_process import stop

ROOT=Path(__file__).resolve().parents[1]

def post(session, token, path, payload, status=201):
    return assert_code(session,path,"POST",status,payload,token)

def balances(session, product):
    return assert_code(session,f"/api/inventory/products/{product}/balances","GET",200)

def group6():
    anonymous=client()
    viewer,vt=login("viewer")
    manager,mt=login("manager")
    admin,at=login("admin")
    assert_code(anonymous,"/api/suppliers","GET",401)
    assert_code(anonymous,"/api/purchase-orders","GET",401)
    assert_code(viewer,"/api/suppliers","POST",403,{"code":"S1","name":"X"},vt)
    assert_code(manager,"/api/suppliers","POST",419,{"code":"S1","name":"X"})
    assert_code(manager,"/api/suppliers","POST",415,token=mt)
    assert_code(manager,"/api/suppliers","POST",422,{"code":"??","name":"X"},mt)
    supplier=post(manager,mt,"/api/suppliers",{"code":"GROUP6-SUP","name":"G6 Supplier"})
    sid=supplier["id"]
    assert supplier["active"]==1
    assert_code(manager,"/api/suppliers","POST",409,{"code":"GROUP6-SUP","name":"Duplicate"},mt)
    assert any(s["code"]=="GROUP6-SUP" for s in assert_code(viewer,"/api/suppliers","GET",200)["data"])

    warehouse=post(manager,mt,"/api/warehouses",{"code":"G6-WHS","name":"Purchase receipt warehouse"})
    location=post(manager,mt,"/api/locations",{
        "warehouse_id":warehouse["id"],"code":"G6-BIN","name":"Receiving bin"})
    dest=post(manager,mt,"/api/locations",{
        "warehouse_id":warehouse["id"],"code":"G6-BIN-B","name":"Secondary bin"})
    loc=location["id"]
    category=post(manager,mt,"/api/categories",{"code":"G6-CATEGORY","name":"G6 Products"})
    def product(sku):
        return post(manager,mt,"/api/products",{
            "sku":sku,"name":sku,"category_id":category["id"],
            "unit_price_minor":1500,"reorder_level":2})
    p1=product("G6-PRODUCT-A")["id"]
    p2=product("G6-PRODUCT-B")["id"]

    payload={"po_number":"PO-2026-G6-0001","supplier_id":sid,
      "lines":[{"product_id":p1,"quantity":5,"unit_price_minor":2000},
               {"product_id":p2,"quantity":2,"unit_price_minor":0}]}
    assert_code(manager,"/api/purchase-orders","POST",422,{
        **payload,"lines":[payload["lines"][0],payload["lines"][0]]},mt)
    assert_code(manager,"/api/purchase-orders","POST",422,{
        **payload,"lines":[{"product_id":p1,"quantity":0,"unit_price_minor":1}]},mt)
    assert_code(manager,"/api/purchase-orders","POST",404,{**payload,"supplier_id":999999},mt)
    assert_code(manager,"/api/purchase-orders","POST",422,{
        **payload,"lines":[{"product_id":p1,"quantity":1,"unit_price_minor":-5}]},mt)
    po=post(manager,mt,"/api/purchase-orders",payload)
    order=po["id"]
    assert po["status"]=="draft" and len(po["lines"])==2,po
    lid=po["lines"][0]["id"]
    other_line=po["lines"][1]["id"]
    assert po["approval"] is None
    assert_code(manager,"/api/purchase-orders","POST",409,payload,mt)
    assert_code(viewer,f"/api/purchase-orders/{order}","GET",200)["status"]=="draft"
    assert_code(viewer,"/api/purchase-orders","GET",200)["data"]
    assert_code(viewer,"/api/purchase-orders?after=invalid","GET",422)
    assert_code(viewer,f"/api/purchase-orders/{order}/receipts","GET",200)["data"]==[]
    assert_code(viewer,f"/api/purchase-orders/{order}/approve","POST",403,{},vt)
    assert_code(manager,f"/api/purchase-orders/{order}/approve","POST",403,{},mt)
    draft_receipt={"line_id":lid,"location_id":loc,"quantity":1,"reference":"G6-DRAFT-REJECT"}
    assert_code(manager,f"/api/purchase-orders/{order}/receipts","POST",409,draft_receipt,mt)
    assert_code(manager,f"/api/purchase-orders/{order}/receipts","POST",422,{
        **draft_receipt,"quantity":0},mt)
    assert_code(manager,f"/api/purchase-orders/{order}/receipts","POST",419,draft_receipt)

    # Separation of duties: an admin who created an order cannot approve it.
    own=post(admin,at,"/api/purchase-orders",{
        "po_number":"PO-2026-G6-OWN","supplier_id":sid,
        "lines":[{"product_id":p1,"quantity":1,"unit_price_minor":1}]})
    assert_code(admin,f"/api/purchase-orders/{own['id']}/approve","POST",403,{},at)

    approved=post(admin,at,f"/api/purchase-orders/{order}/approve",{},200)
    assert approved["status"]=="approved"
    assert approved["approval"]["actor_id"] is not None
    assert_code(admin,f"/api/purchase-orders/{order}/approve","POST",409,{},at)
    assert_code(manager,f"/api/purchase-orders/{order}/receipts","POST",404,{
        "line_id":999999,"location_id":loc,"quantity":1,
        "reference":"G6-MISSING-LINE"},mt)
    assert_code(manager,f"/api/purchase-orders/{order}/receipts","POST",409,{
        "line_id":lid,"location_id":loc,"quantity":6,
        "reference":"G6-OVERRECEIPT"},mt)
    assert_code(manager,f"/api/purchase-orders/{order}/receipts","POST",404,{
        "line_id":lid,"location_id":999999,"quantity":1,
        "reference":"G6-MISSING-LOC"},mt)
    assert_code(viewer,f"/api/purchase-orders/{order}/receipts","POST",403,{
        "line_id":lid,"location_id":loc,"quantity":1,
        "reference":"G6-VIEWER-RECV"},vt)

    rec={"line_id":lid,"location_id":loc,"quantity":2,"reference":"G6-RECEIPT-0001"}
    partial=post(manager,mt,f"/api/purchase-orders/{order}/receipts",rec)
    assert partial["status"]=="partially_received" and partial["stock_after"]==2,partial
    assert partial["line_received_after"]==2
    assert balances(viewer,p1)["located"]==2
    assert balances(viewer,p1)["unallocated"]==0
    assert_code(manager,f"/api/purchase-orders/{order}/receipts","POST",409,{
        **rec,"location_id":dest["id"]},mt)
    assert_code(manager,"/api/inventory/location-movements","POST",409,{
        "product_id":p1,"location_id":loc,"quantity":2,
        "kind":"receipt","reason":"purchase_order_receipt","reference":"G6-RECEIPT-0001"},mt)

    # Concurrent over-receive: only three outstanding units remain.
    def race(i):
        return call(manager,f"/api/purchase-orders/{order}/receipts","POST",{
            "line_id":lid,"location_id":loc,"quantity":3,
            "reference":f"G6-RACE-{i:04}"},mt)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=[f.result(timeout=20) for f in [pool.submit(race,i) for i in range(2)]]
    assert sorted(x[0] for x in results)==[201,409],results
    assert balances(viewer,p1)["stock_on_hand"]==5

    po=assert_code(viewer,f"/api/purchase-orders/{order}","GET",200)
    assert po["status"]=="partially_received"
    assert po["lines"][0]["received_quantity"]==5
    other={"line_id":other_line,"location_id":dest["id"],
           "quantity":2,"reference":"G6-RECEIPT-OTHER"}
    with ThreadPoolExecutor(max_workers=3) as pool:
        futures=[pool.submit(call,manager,f"/api/purchase-orders/{order}/receipts","POST",
                             other,mt) for _ in range(3)]
        results=[f.result(timeout=20) for f in futures]
    assert sorted(x[0] for x in results)==[200,200,201],results
    assert balances(viewer,p2)["stock_on_hand"]==2
    full=assert_code(viewer,f"/api/purchase-orders/{order}","GET",200)
    assert full["status"]=="received" and all(
        x["ordered_quantity"]==x["received_quantity"] for x in full["lines"])
    retry=post(manager,mt,f"/api/purchase-orders/{order}/receipts",rec,200)
    assert retry["replayed"] is True and retry["stock_after"]==2
    assert_code(manager,f"/api/purchase-orders/{order}/receipts","POST",409,{
        **other,"reference":"G6-RECEIPT-TOO-LATE"},mt)

    receipts=assert_code(viewer,f"/api/purchase-orders/{order}/receipts","GET",200)["data"]
    assert len(receipts)==3,receipts
    assert len({r["reference"] for r in receipts})==3
    with sqlite3.connect(ROOT/"storage"/"inventory.sqlite") as db:
        r=db.execute("SELECT COUNT(*) FROM purchase_receipts WHERE purchase_order_id=?",(order,)).fetchone()[0]
        assert r==3,r
        approved_by=db.execute("SELECT actor_id FROM purchase_order_approvals WHERE purchase_order_id=?",(order,)).fetchone()[0]
        creator=db.execute("SELECT created_by FROM purchase_orders WHERE id=?",(order,)).fetchone()[0]
        assert approved_by!=creator
        assert db.execute("SELECT COUNT(*) FROM purchase_receipts WHERE reference='G6-DRAFT-REJECT'").fetchone()[0]==0
        assert db.execute("SELECT COUNT(*) FROM inventory_operations WHERE reference='G6-OVERRECEIPT'").fetchone()[0]==0
        for product,expected in ((p1,5),(p2,2)):
            central=db.execute("SELECT stock_on_hand FROM products WHERE id=?",(product,)).fetchone()[0]
            sums=db.execute("SELECT COALESCE(SUM(quantity_delta),0) FROM stock_movements WHERE product_id=?",(product,)).fetchone()[0]
            located=db.execute("SELECT COALESCE(SUM(quantity),0) FROM location_balances WHERE product_id=?",(product,)).fetchone()[0]
            assert central==sums==located==expected,(product,central,sums,located)
            for record in db.execute("SELECT reference FROM purchase_receipts WHERE product_id=?",(product,)):
                ref=record[0]
                assert db.execute("SELECT COUNT(*) FROM stock_movements WHERE reference=?",(ref,)).fetchone()[0]==1
                assert db.execute("SELECT COUNT(*) FROM location_movements WHERE reference=?",(ref,)).fetchone()[0]==1
                assert db.execute("SELECT COUNT(*) FROM inventory_operations WHERE reference=?",(ref,)).fetchone()[0]==1
    print("PASS Group6 suppliers, PO approval separation, partial receipt, exact-once stock, concurrent overreceipt, immutable history")


def main():
    path=ROOT/"storage"/"procurement-smoke.log"
    with path.open("w",encoding="utf-8") as log:
        process=subprocess.Popen(["gungnir","run"],cwd=ROOT,stdout=log,
                                 stderr=subprocess.STDOUT,start_new_session=(os.name!="nt"))
        try:
            deadline=time.monotonic()+90
            while time.monotonic()<deadline:
                if process.poll() is not None: raise RuntimeError("Procurement server exited")
                try:
                    if call(client(),"/health")[0]==200: break
                except (OSError,ValueError): time.sleep(0.3)
            else: raise RuntimeError("Procurement server never became healthy")
            group6()
        except Exception:
            print(path.read_text(encoding="utf-8"),file=sys.stderr)
            raise
        finally:
            stop(process)

if __name__=="__main__":
    main()
