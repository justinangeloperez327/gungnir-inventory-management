"""Group 5: warehouse allocation, location movements, transfers, and reconciliation."""
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
from inventory_smoke import movement

ROOT=Path(__file__).resolve().parents[1]
BASE="/api/inventory"


def endpoint(user, token, path, payload, status=201):
    return assert_code(user, path, "POST", status, payload, token)


def balance(user, product):
    return assert_code(user, BASE+f"/products/{product}/balances", "GET", 200)


def location_movement(user, token, product, location, kind, qty, ref, status=201):
    return endpoint(user,token,BASE+"/location-movements",{
        "product_id":product,"location_id":location,"kind":kind,
        "quantity":qty,"reference":ref,"reason":"group5-test"
    },status)


def test_group5():
    anonymous=client()
    viewer, viewer_token=login("viewer")
    manager, token=login("manager")
    admin, admin_token=login("admin")

    assert_code(anonymous,"/api/warehouses","GET",401)
    assert_code(anonymous,"/api/locations","GET",401)
    assert_code(anonymous,BASE+"/products/1/balances","GET",401)
    assert_code(viewer,"/api/warehouses","POST",403,{"code":"BAD","name":"Unauthorized"},viewer_token)
    assert_code(manager,"/api/warehouses","POST",419,{"code":"MAIN","name":"Main"})
    assert_code(manager,"/api/locations","POST",422,{"warehouse_id":1,"code":"??","name":"Bad"},token)
    assert_code(manager,BASE+"/transfers","POST",415,token=token)

    wh1=endpoint(manager,token,"/api/warehouses",{"code":"MAIN-5","name":"Main Warehouse"})
    wh2=endpoint(admin,admin_token,"/api/warehouses",{"code":"REMOTE-5","name":"Remote Warehouse"})
    assert wh1["id"]!=wh2["id"]
    assert_code(manager,"/api/warehouses","POST",409,{"code":"MAIN-5","name":"Duplicate"},token)
    warehouse_id=wh1["id"]
    locations=[]
    for id_,code in [(wh1["id"],"G5-A"),(wh1["id"],"G5-B"),(wh2["id"],"G5-C")]:
        loc=endpoint(manager,token,"/api/locations",
            {"warehouse_id":id_,"code":code,"name":"Location "+code})
        locations.append(loc["id"])
    a,b,c=locations
    assert_code(viewer,"/api/locations?warehouse_id="+str(warehouse_id),"GET",200)["data"][0]["code"]=="G5-A"
    assert len(assert_code(viewer,"/api/locations?warehouse_id="+str(warehouse_id),"GET",200)["data"])==2
    assert_code(manager,"/api/locations","POST",409,
                {"warehouse_id":wh2["id"],"code":"G5-A","name":"Duplicate"},token)

    category=endpoint(manager,token,"/api/categories",{"code":"G5-CAT","name":"Warehouse G5"})
    prod=endpoint(manager,token,"/api/products",{
        "sku":"G5-COMPONENT","name":"Warehouse stock component",
        "category_id":category["id"],"unit_price_minor":100,"reorder_level":1})
    p=prod["id"]
    assert balance(viewer,p)["unallocated"]==0
    assert_code(viewer,BASE+"/allocations","POST",403,{
        "product_id":p,"location_id":a,"quantity":1,
        "reference":"G5-ALLOC-VIEWER","reason":"group5-test"},viewer_token)
    assert_code(manager,BASE+"/allocations","POST",409,{
        "product_id":p,"location_id":a,"quantity":1,
        "reference":"G5-ALLOC-EMPTY","reason":"group5-test"},token)
    assert_code(manager,BASE+"/allocations","POST",422,{
        "product_id":p,"location_id":a,"quantity":1,
        "reference":"bad","reason":"group5-test","stock_on_hand":10000},token)

    code,body=movement(manager,token,"receipt",30,"G5-RECEIPT-LEGACY",p)
    assert code==201,(code,body)
    expected={"stock_on_hand":30,"located":0,"unallocated":30}
    for k,v in expected.items(): assert balance(viewer,p)[k]==v

    allocate={"product_id":p,"location_id":a,"quantity":15,
              "reference":"G5-ALLOC-0001","reason":"group5-test"}
    first=endpoint(manager,token,BASE+"/allocations",allocate)
    assert first["location_after"]==15 and first["stock_after"]==30
    retry=endpoint(manager,token,BASE+"/allocations",allocate,200)
    assert retry["replayed"] is True
    endpoint(manager,token,BASE+"/allocations",{
        **allocate,"reference":"G5-ALLOC-0002","location_id":b,"quantity":5})
    assert_code(manager,BASE+"/allocations","POST",409,{
        **allocate,"reference":"G5-ALLOC-0001","location_id":c},token)
    assert_code(manager,BASE+"/allocations","POST",409,{
        **allocate,"reference":"G5-OVERALLOC-0001","quantity":11},token)
    res=balance(viewer,p)
    assert (res["stock_on_hand"],res["located"],res["unallocated"])==(30,20,10),res

    transfer={"product_id":p,"source_location_id":a,"destination_location_id":c,
              "quantity":6,"reference":"G5-TRANSFER-0001","reason":"group5-test"}
    first=endpoint(manager,token,BASE+"/transfers",transfer)
    assert first["stock_after"]==30
    assert first["source_after"]==9 and first["destination_after"]==6
    assert endpoint(manager,token,BASE+"/transfers",transfer,200)["replayed"] is True
    assert_code(manager,BASE+"/transfers","POST",409,{
        **transfer,"destination_location_id":b},token)
    assert_code(manager,BASE+"/transfers","POST",422,{
        **transfer,"source_location_id":a,"destination_location_id":a,
        "reference":"G5-TRANSFER-SAME"},token)
    assert_code(manager,BASE+"/transfers","POST",409,{
        **transfer,"quantity":10,"reference":"G5-TRANSFER-OVER"},token)
    assert_code(manager,BASE+"/transfers","POST",409,{
        **transfer,"quantity":6,"reference":"G5-ALLOC-0001"},token)
    assert (balance(viewer,p)["stock_on_hand"],balance(viewer,p)["located"])==(30,20)

    # The original Group 4 unallocated endpoint cannot spend a located unit.
    code,body=movement(manager,token,"issue",11,"G5-LEGACY-ISSUE-BAD",p)
    assert code==409,(code,body)
    code,body=movement(manager,token,"issue",8,"G5-LEGACY-ISSUE-OK",p)
    assert code==201,(code,body)
    assert balance(viewer,p)["unallocated"]==2

    issue=location_movement(manager,token,p,a,"issue",4,"G5-LOC-ISSUE-0001")
    assert issue["location_after"]==5 and issue["stock_after"]==18
    receipt=location_movement(manager,token,p,b,"receipt",3,"G5-LOC-RCPT-0001")
    assert receipt["location_after"]==8 and receipt["stock_after"]==21
    corr=location_movement(manager,token,p,c,"correction",1,"G5-LOC-CORR-0001")
    assert corr["location_after"]==1 and corr["stock_after"]==16
    ret=location_movement(manager,token,p,c,"return",2,"G5-LOC-RETURN-01")
    assert ret["location_after"]==3 and ret["stock_after"]==18
    assert location_movement(manager,token,p,c,"return",2,"G5-LOC-RETURN-01",200)["replayed"] is True
    assert_code(manager,BASE+"/location-movements","POST",409,{
        "product_id":p,"location_id":a,"kind":"issue","quantity":6,
        "reason":"group5-test","reference":"G5-LOC-OVERISSUE"},token)
    assert_code(manager,BASE+"/location-movements","POST",409,{
        "product_id":p,"location_id":a,"kind":"correction","quantity":5,
        "reason":"group5-test","reference":"G5-LOC-CORR-NOOP"},token)
    assert_code(manager,BASE+"/location-movements","POST",409,{
        "product_id":p,"location_id":a,"kind":"receipt","quantity":2,
        "reason":"group5-test","reference":"G5-ALLOC-0001"},token)
    res=balance(viewer,p)
    assert (res["stock_on_hand"],res["located"],res["unallocated"])==(18,16,2),res

    # Two attempts to spend the same five located units: exactly one commits.
    # Concurrent issues race for fewer units than their combined demand.
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures=[pool.submit(call,manager,BASE+"/location-movements","POST",{
            "product_id":p,"location_id":a,"kind":"issue","quantity":4,
            "reference":f"G5-LOC-RACE-{i:04}","reason":"group5-test"},token) for i in range(2)]
        raced=[f.result(timeout=20) for f in futures]
    assert sorted(status for status,_ in raced)==[201,409],raced
    assert balance(viewer,p)["unallocated"]==2

    with ThreadPoolExecutor(max_workers=3) as pool:
        futures=[pool.submit(call,manager,BASE+"/transfers","POST",{
            "product_id":p,"source_location_id":b,"destination_location_id":c,
            "quantity":2,"reference":"G5-TRANSFER-RACEIDEMP","reason":"group5-test"},token) for _ in range(3)]
        raced=[f.result(timeout=20) for f in futures]
    assert sorted(status for status,_ in raced)==[200,200,201],raced

    # More than 20 location journal entries trigger ordered pagination.
    for i in range(23):
        location_movement(manager,token,p,c,"receipt",1,f"G5-LOC-PAGE-{i:04}")
    page1=assert_code(viewer,BASE+f"/locations/{c}/movements","GET",200)
    assert len(page1["data"])==20 and page1["has_more"] is True
    page2=assert_code(viewer,BASE+f"/locations/{c}/movements?after="+page1["next_cursor"],"GET",200)
    assert len(page2["data"])>=5 and page2["has_more"] is False
    assert_code(viewer,BASE+f"/locations/{c}/movements?after=not-id","GET",422)
    assert_code(viewer,BASE+f"/locations/999999/movements","GET",404)

    with sqlite3.connect(ROOT/"storage"/"inventory.sqlite") as db:
        central=db.execute("SELECT stock_on_hand FROM products WHERE id=?",(p,)).fetchone()[0]
        located=db.execute("SELECT COALESCE(SUM(quantity),0) FROM location_balances WHERE product_id=?",(p,)).fetchone()[0]
        total_journal=db.execute("SELECT COALESCE(SUM(quantity_delta),0) FROM stock_movements WHERE product_id=?",(p,)).fetchone()[0]
        assert central==total_journal and central==located+2,(central,total_journal,located)
        local_balance=dict(db.execute("SELECT location_id,quantity FROM location_balances WHERE product_id=?",(p,)))
        local_journal=dict(db.execute(
            "SELECT location_id,SUM(quantity_delta) FROM location_movements "
            "WHERE product_id=? GROUP BY location_id",(p,)))
        assert local_balance==local_journal,(local_balance,local_journal)
        assert db.execute("SELECT COUNT(*) FROM warehouse_transfers WHERE reference='G5-TRANSFER-0001'").fetchone()[0]==1
        assert db.execute("SELECT COUNT(*) FROM warehouse_transfers WHERE reference='G5-TRANSFER-RACEIDEMP'").fetchone()[0]==1
        assert db.execute("SELECT COUNT(*) FROM inventory_operations WHERE reference='G5-LOC-OVERISSUE'").fetchone()[0]==0
        assert db.execute("SELECT COUNT(*) FROM inventory_operations WHERE reference='G5-OVERALLOC-0001'").fetchone()[0]==0

    assert_code(admin,"/api/products/"+str(p),"DELETE",204,token=admin_token)
    assert_code(manager,BASE+"/transfers","POST",409,{
        **transfer,"quantity":1,"reference":"G5-TRANSFER-ARCHIVED"},token)
    assert_code(viewer,BASE+f"/products/{p}/balances","GET",200)
    print("PASS Group5 warehouse/location balances, allocation, transfers, located receipts/issues, reconciliation, idempotency, concurrency")


def main():
    logpath=ROOT/"storage"/"warehouse-smoke.log"
    with logpath.open("w",encoding="utf-8") as log:
        proc=subprocess.Popen(["gungnir","run"],cwd=ROOT,stdout=log,
            stderr=subprocess.STDOUT,start_new_session=(os.name!="nt"))
        try:
            end=time.monotonic()+80
            while time.monotonic()<end:
                if proc.poll() is not None: raise RuntimeError("Server exited")
                try:
                    if call(client(),"/health")[0]==200: break
                except (OSError,ValueError): time.sleep(0.3)
            else: raise RuntimeError("Server not ready")
            test_group5()
        except Exception:
            print(logpath.read_text(encoding="utf-8"),file=sys.stderr)
            raise
        finally:
            if proc.poll() is None:
                if os.name!="nt": os.killpg(proc.pid,signal.SIGTERM)
                else: proc.terminate()
                try: proc.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    proc.kill();proc.wait(timeout=5)


if __name__=="__main__":
    main()
