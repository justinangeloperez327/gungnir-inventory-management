#pragma once
// Group 6 procurement integration. All goods receipts use the same database
// transaction and idempotency namespace as Group 4/5 inventory mutations.
#include "inventory_locations.hpp"
#include <set>
#include <string>
#include <vector>

namespace inventory::procurement {
using namespace inventory::locations;

inline std::string reference_field(const Json& data, std::string_view field, std::size_t limit) {
    const auto value = text_field(data, field, limit);
    if (value.size() < 8) throw ApiFailure{422, "Reference must contain at least 8 characters"};
    for (unsigned char c : value)
        if (c > 127 || (!std::isalnum(c) && c!='-' && c!='_' && c!='.' && c!=':'))
            throw ApiFailure{422, "Reference has invalid characters"};
    return value;
}

inline std::int64_t admin_actor(const Request& request) {
    const auto actor = require_manager(request);
    const auto* identity = request.user();
    if (!identity || !identity->role("admin"))
        throw ApiFailure{403, "Administrator approval required"};
    return actor;
}

inline std::int64_t max_price() { return 2147483647; }

inline void active_supplier(const ConnectionPtr& conn, std::int64_t supplier) {
    const auto found=conn->execute("SELECT active FROM suppliers WHERE id=?", {supplier});
    if (found.rows.empty()) throw ApiFailure{404, "Supplier not found"};
    if (row_number(found.rows.front(),"active")==0)
        throw ApiFailure{409, "Supplier is inactive"};
}

inline void check_product(const ConnectionPtr& conn, std::int64_t product) {
    (void)product_total(conn,product,true);
}

inline Json po_detail(const ConnectionPtr& conn, std::int64_t order) {
    const auto found=conn->execute(
        "SELECT id,po_number,supplier_id,created_by,status,created_at,updated_at "
        "FROM purchase_orders WHERE id=?", {order});
    if (found.rows.empty()) throw ApiFailure{404,"Purchase order not found"};
    auto object=object_row(found.rows.front()).as_object();
    const auto lines=conn->execute(
        "SELECT id,product_id,ordered_quantity,received_quantity,unit_price_minor "
        "FROM purchase_order_lines WHERE purchase_order_id=? ORDER BY id LIMIT 51",{order});
    if (lines.rows.size()>50) throw ApiFailure{409,"Purchase order exceeds line limit"};
    object["lines"]=rows_json(lines.rows);
    const auto approval=conn->execute(
        "SELECT actor_id,created_at FROM purchase_order_approvals WHERE purchase_order_id=?",
        {order});
    object["approval"]=approval.rows.empty() ? Json{nullptr} : object_row(approval.rows.front());
    return Json::object(std::move(object));
}

inline Response list_suppliers(Request& request) {
    return handle([&]() -> Response {
        require_reader(request);
        const auto results=DB::connection()->execute(
            "SELECT id,code,name,active FROM suppliers ORDER BY id LIMIT 101");
        auto rows=results.rows;
        const auto more=rows.size()>100;
        if (more) rows.resize(100);
        return Response::json(Json::object({{"data",rows_json(rows)},{"has_more",more}}));
    });
}

inline Response create_supplier(Request& request) {
    return handle([&]() -> Response {
        require_manager(request);
        const auto& data=object_body(request,{"code","name"});
        const auto code=code_field(data);
        const auto name=text_field(data,"name",128);
        std::lock_guard lock{*shared_gate()};
        return DB::transaction([&]() -> Response {
            auto conn=DB::connection();
            if (!conn->execute("SELECT id FROM suppliers WHERE code=?", {code}).rows.empty())
                throw ApiFailure{409,"Supplier code already exists"};
            const auto inserted=conn->execute(
                "INSERT INTO suppliers (code,name,active,created_at,updated_at) "
                "VALUES (?,?,1,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)", {code,name});
            const auto row=conn->execute(
                "SELECT id,code,name,active FROM suppliers WHERE id=?", {*inserted.inserted_id});
            return Response::json(object_row(row.rows.front()),201);
        });
    });
}

inline Response list_orders(Request& request) {
    return handle([&]() -> Response {
        require_reader(request);
        std::int64_t after=0;
        const auto cursor=std::string{request.query("after")};
        if (!cursor.empty()) {
            if (cursor.size()>10 || cursor.find_first_not_of("0123456789")!=std::string::npos)
                throw ApiFailure{422,"Invalid purchase-order cursor"};
            after=std::stoll(cursor);
            if (after<0 || after>max_stock) throw ApiFailure{422,"Invalid purchase-order cursor"};
        }
        const auto rows=DB::connection()->execute(
            "SELECT id,po_number,supplier_id,created_by,status,created_at "
            "FROM purchase_orders WHERE id>? ORDER BY id LIMIT 21", {after});
        Json::Array results;
        const bool more=rows.rows.size()>20;
        std::int64_t next{};
        for (std::size_t i=0;i<std::min<std::size_t>(20,rows.rows.size());++i) {
            results.push_back(object_row(rows.rows[i]));
            next=row_number(rows.rows[i],"id");
        }
        return Response::json(Json::object({
            {"data",Json::array(std::move(results))},{"has_more",more},
            {"next_cursor",more ? std::to_string(next) : std::string{}}
        }));
    });
}

inline Response show_order(Request& request) {
    return handle([&]() -> Response {
        require_reader(request);
        return Response::json(po_detail(DB::connection(),route_id(request,"order")));
    });
}

struct NewLine { std::int64_t product{},quantity{},unit_price{}; };

inline Response create_order(Request& request) {
    return handle([&]() -> Response {
        const auto actor=require_manager(request);
        const auto& data=object_body(request,{"po_number","supplier_id","lines"});
        const auto number=reference_field(data,"po_number",48);
        const auto supplier=integer_field(data,"supplier_id",1,max_stock);
        const Json* list=data.get("lines");
        if (!list || !list->is_array() || list->as_array().empty() || list->as_array().size()>50)
            throw ApiFailure{422,"Purchase order requires 1 to 50 lines"};
        std::vector<NewLine> lines;
        std::set<std::int64_t> seen;
        for (const auto& item:list->as_array()) {
            if (!item.is_object()) throw ApiFailure{422,"Each order line must be an object"};
            for (const auto& [key,value]:item.as_object())
                if (key!="product_id" && key!="quantity" && key!="unit_price_minor")
                    throw ApiFailure{422,"Unexpected purchase-order line field"};
            NewLine line;
            line.product=integer_field(item,"product_id",1,max_stock);
            line.quantity=integer_field(item,"quantity",1,max_movement);
            line.unit_price=integer_field(item,"unit_price_minor",0,max_price());
            if (!seen.insert(line.product).second)
                throw ApiFailure{422,"Duplicate product in purchase order"};
            lines.push_back(line);
        }

        std::lock_guard lock{*shared_gate()};
        return DB::transaction([&]() -> Response {
            auto conn=DB::connection();
            active_supplier(conn,supplier);
            if (!conn->execute("SELECT id FROM purchase_orders WHERE po_number=?", {number}).rows.empty())
                throw ApiFailure{409,"Purchase order number already exists"};
            for (const auto& line:lines) check_product(conn,line.product);
            const auto created=conn->execute(
                "INSERT INTO purchase_orders "
                "(po_number,supplier_id,created_by,status,created_at,updated_at) "
                "VALUES (?,?,?,'draft',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)",
                {number,supplier,actor});
            const std::int64_t po=*created.inserted_id;
            for (const auto& line:lines) {
                const auto key=std::to_string(po)+":"+std::to_string(line.product);
                conn->execute(
                    "INSERT INTO purchase_order_lines "
                    "(purchase_order_id,product_id,line_key,ordered_quantity,received_quantity,"
                    "unit_price_minor,created_at,updated_at) "
                    "VALUES (?,?,?,?,0,?,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)",
                    {po,line.product,key,line.quantity,line.unit_price});
            }
            return Response::json(po_detail(conn,po),201);
        });
    });
}

inline Response approve_order(Request& request) {
    return handle([&]() -> Response {
        const auto actor=admin_actor(request);
        (void)object_body(request,{});
        const auto order=route_id(request,"order");
        std::lock_guard lock{*shared_gate()};
        return DB::transaction([&]() -> Response {
            auto conn=DB::connection();
            const auto found=conn->execute(
                "SELECT status,created_by FROM purchase_orders WHERE id=?", {order});
            if (found.rows.empty()) throw ApiFailure{404,"Purchase order not found"};
            if (row_number(found.rows.front(),"created_by")==actor)
                throw ApiFailure{403,"Creator cannot approve own order"};
            if (row_text(found.rows.front(),"status")!="draft")
                throw ApiFailure{409,"Only draft purchase orders can be approved"};
            const auto changes=conn->execute(
                "UPDATE purchase_orders SET status='approved',updated_at=CURRENT_TIMESTAMP "
                "WHERE id=? AND status='draft'", {order});
            if (changes.affected_rows!=1)
                throw ApiFailure{409,"Purchase order approval conflict"};
            conn->execute(
                "INSERT INTO purchase_order_approvals "
                "(purchase_order_id,actor_id,created_at,updated_at) "
                "VALUES (?,?,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)", {order,actor});
            return Response::json(po_detail(conn,order));
        });
    });
}

inline Response list_receipts(Request& request) {
    return handle([&]() -> Response {
        require_reader(request);
        const auto order=route_id(request,"order");
        auto conn=DB::connection();
        (void)po_detail(conn,order);
        const auto result=conn->execute(
            "SELECT id,reference,line_id,product_id,location_id,actor_id,quantity,"
            "line_received_after,stock_after,location_after,created_at "
            "FROM purchase_receipts WHERE purchase_order_id=? ORDER BY id LIMIT 101",
            {order});
        auto rows=result.rows;
        const bool more=rows.size()>100;
        if (more) rows.resize(100);
        return Response::json(Json::object({{"data",rows_json(rows)},{"has_more",more}}));
    });
}

inline Response receive_order(Request& request) {
    return handle([&]() -> Response {
        const auto actor=require_manager(request);
        const auto& data=object_body(request,{"line_id","location_id","quantity","reference"});
        const auto order=route_id(request,"order");
        const auto line_id=integer_field(data,"line_id",1,max_stock);
        const auto location=integer_field(data,"location_id",1,max_stock);
        const auto quantity=integer_field(data,"quantity",1,max_movement);
        const auto reference=reference_field(data,"reference",64);
        std::lock_guard lock{*shared_gate()};
        return DB::transaction([&]() -> Response {
            auto conn=DB::connection();
            const auto po=conn->execute(
                "SELECT status,supplier_id FROM purchase_orders WHERE id=?", {order});
            if (po.rows.empty()) throw ApiFailure{404,"Purchase order not found"};
            const auto line=conn->execute(
                "SELECT product_id,ordered_quantity,received_quantity "
                "FROM purchase_order_lines WHERE id=? AND purchase_order_id=?",
                {line_id,order});
            if (line.rows.empty()) throw ApiFailure{404,"Purchase order line not found"};
            const auto product=row_number(line.rows.front(),"product_id");

            // The global reference prevents duplicating a stock entry, even
            // if an HTTP retry races with another inventory operation.
            Command command;
            command.product_id=product;
            command.actor_id=actor;
            command.quantity=quantity;
            command.kind="po_receipt";
            command.reason="purchase_order_receipt";
            command.reference=reference;
            const auto central=product_total(conn,product,false);
            if (!claim(conn,command,central)) {
                const auto old=conn->execute(
                    "SELECT purchase_order_id,line_id,location_id,actor_id,quantity,"
                    "line_received_after,stock_after,location_after "
                    "FROM purchase_receipts WHERE reference=?", {reference});
                if (old.rows.empty()) throw ApiFailure{409,"Reference belongs to another operation"};
                const auto& prev=old.rows.front();
                if (row_number(prev,"purchase_order_id")!=order ||
                    row_number(prev,"line_id")!=line_id ||
                    row_number(prev,"location_id")!=location ||
                    row_number(prev,"actor_id")!=actor ||
                    row_number(prev,"quantity")!=quantity)
                    throw ApiFailure{409,"Reference belongs to a different receipt"};
                return Response::json(Json::object({
                    {"reference",reference},{"purchase_order_id",order},{"line_id",line_id},
                    {"product_id",product},{"location_id",location},{"quantity",quantity},
                    {"line_received_after",row_number(prev,"line_received_after")},
                    {"stock_after",row_number(prev,"stock_after")},
                    {"location_after",row_number(prev,"location_after")},
                    {"replayed",true}
                }),200);
            }

            const auto status=row_text(po.rows.front(),"status");
            if (status!="approved" && status!="partially_received")
                throw ApiFailure{409,"Purchase order is not open for receiving"};
            active_supplier(conn,row_number(po.rows.front(),"supplier_id"));
            product_total(conn,product,true);
            active_location(conn,location);
            const auto ordered=row_number(line.rows.front(),"ordered_quantity");
            const auto already=row_number(line.rows.front(),"received_quantity");
            if (ordered<=0 || already<0 || already>ordered || quantity>ordered-already)
                throw ApiFailure{409,"Receipt exceeds outstanding order quantity"};
            const auto local=at_location(conn,product,location);
            if (central>max_stock-quantity || local>max_stock-quantity)
                throw ApiFailure{409,"Inventory capacity exceeded"};
            const auto line_updated=conn->execute(
                "UPDATE purchase_order_lines SET received_quantity=?,updated_at=CURRENT_TIMESTAMP "
                "WHERE id=? AND received_quantity=?",
                {already+quantity,line_id,already});
            if (line_updated.affected_rows!=1)
                throw ApiFailure{409,"Concurrent purchase-order receipt"};
            const auto product_updated=conn->execute(
                "UPDATE products SET stock_on_hand=?,updated_at=CURRENT_TIMESTAMP "
                "WHERE id=? AND stock_on_hand=? AND active=1",
                {central+quantity,product,central});
            if (product_updated.affected_rows!=1)
                throw ApiFailure{409,"Concurrent inventory movement"};
            write_location(conn,product,location,local,local+quantity);
            conn->execute(
                "INSERT INTO stock_movements "
                "(product_id,quantity_delta,reason,reference,created_at,updated_at) "
                "VALUES (?,?,?,?,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)",
                {product,quantity,command.reason,reference});
            location_journal(conn,command,location,quantity);
            finalize(conn,command,quantity,central+quantity);
            conn->execute(
                "INSERT INTO purchase_receipts "
                "(reference,purchase_order_id,line_id,product_id,location_id,actor_id,quantity,"
                "line_received_after,stock_after,location_after,created_at,updated_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)",
                {reference,order,line_id,product,location,actor,quantity,
                 already+quantity,central+quantity,local+quantity});
            const auto remaining=conn->execute(
                "SELECT COALESCE(SUM(ordered_quantity-received_quantity),0) AS outstanding "
                "FROM purchase_order_lines WHERE purchase_order_id=?", {order});
            const auto next=row_number(remaining.rows.front(),"outstanding")==0
                ? std::string{"received"} : std::string{"partially_received"};
            const auto set=conn->execute(
                "UPDATE purchase_orders SET status=?,updated_at=CURRENT_TIMESTAMP "
                "WHERE id=? AND status=?", {next,order,status});
            if (set.affected_rows!=1) throw ApiFailure{409,"Purchase order status changed"};
            return Response::json(Json::object({
                {"reference",reference},{"purchase_order_id",order},{"line_id",line_id},
                {"product_id",product},{"location_id",location},{"quantity",quantity},
                {"line_received_after",already+quantity},{"stock_after",central+quantity},
                {"location_after",local+quantity},{"status",next},{"replayed",false}
            }),201);
        });
    });
}

inline void register_routes(gungnir::Application& app) {
    // Session, CSRF and identity middleware are installed by bootstrap::configure.
    app.router().get("/api/suppliers",[](Request& r){return list_suppliers(r);});
    app.router().post("/api/suppliers",[](Request& r){return create_supplier(r);});
    app.router().get("/api/purchase-orders",[](Request& r){return list_orders(r);});
    app.router().post("/api/purchase-orders",[](Request& r){return create_order(r);});
    app.router().get("/api/purchase-orders/{order}",[](Request& r){return show_order(r);});
    app.router().post("/api/purchase-orders/{order}/approve",
        [](Request& r){return approve_order(r);});
    app.router().get("/api/purchase-orders/{order}/receipts",
        [](Request& r){return list_receipts(r);});
    app.router().post("/api/purchase-orders/{order}/receipts",
        [](Request& r){return receive_order(r);});
}
} // namespace inventory::procurement
