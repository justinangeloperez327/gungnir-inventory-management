#pragma once

// Group 5 warehouse/location service. SQL is SQLite-specific by design:
// preserve central stock, maintain location balances atomically, and use the
// existing inventory_operations reference as the global idempotency key.
#include "inventory_transactions.hpp"

#include <algorithm>
#include <cstdint>
#include <memory>
#include <mutex>
#include <string>
#include <string_view>
#include <vector>

namespace inventory::locations {
using namespace inventory::transactions;
using ConnectionPtr = std::shared_ptr<gungnir::database::Connection>;

template <class F>
Response handle(F&& fn) {
    try { return fn(); }
    catch (const ApiFailure& e) { return failure(e.status, e.what()); }
    catch (const gungnir::database::Error&) {
        return failure(503, "Warehouse database temporarily unavailable");
    }
    catch (const std::exception&) { return failure(400, "Invalid warehouse request"); }
}

void require_reader(const Request& request) {
    if (!request.authenticated()) throw ApiFailure{401, "Unauthenticated"};
}

std::int64_t require_manager(const Request& request) {
    const auto* actor = request.user();
    if (!actor) throw ApiFailure{401, "Unauthenticated"};
    if (!actor->role("admin") && !actor->role("manager"))
        throw ApiFailure{403, "Forbidden"};
    try {
        std::size_t used{};
        const auto id = std::stoll(actor->id, &used);
        if (used != actor->id.size() || id <= 0 || id > max_stock)
            throw std::invalid_argument("identity");
        return id;
    } catch (...) { throw ApiFailure{403, "Invalid authenticated identity"}; }
}

const Json& object_body(Request& request, std::initializer_list<std::string_view> allowed) {
    if (!request.is_json()) throw ApiFailure{415, "JSON required"};
    const Json& body = request.json();
    if (!body.is_object()) throw ApiFailure{422, "A JSON object is required"};
    for (const auto& [key, value] : body.as_object()) {
        if (std::find(allowed.begin(), allowed.end(), std::string_view{key}) == allowed.end())
            throw ApiFailure{422, "Unknown warehouse field"};
    }
    return body;
}

std::string code_field(const Json& body) {
    const auto code = text_field(body, "code", 32);
    for (unsigned char c : code)
        if (!std::isalnum(c) && c != '-' && c != '_')
            throw ApiFailure{422, "Code accepts only ASCII letters, numbers, dash and underscore"};
    return code;
}

std::int64_t route_id(const Request& request, std::string_view name) {
    const auto id = std::string{request.parameter(std::string{name})};
    if (id.empty() || id.size() > 10 ||
        id.find_first_not_of("0123456789") != std::string::npos)
        throw ApiFailure{404, "Resource not found"};
    const auto value = std::stoll(id);
    if (value < 1 || value > max_stock)
        throw ApiFailure{404, "Resource not found"};
    return value;
}

Json object_row(const AttributeMap& row) {
    Json::Object result;
    for (const auto& [key, value] : row)
        result.emplace(key, gungnir::http::make_json(value));
    return Json::object(std::move(result));
}

Json rows_json(const std::vector<AttributeMap>& rows) {
    Json::Array result;
    for (const auto& row : rows) result.push_back(object_row(row));
    return Json::array(std::move(result));
}

std::int64_t product_total(const ConnectionPtr& connection, std::int64_t product,
                           bool require_active = true) {
    const auto result = connection->execute(
        "SELECT stock_on_hand, active FROM products WHERE id = ?", {product});
    if (result.rows.empty()) throw ApiFailure{404, "Product not found"};
    if (require_active && row_number(result.rows.front(), "active") == 0)
        throw ApiFailure{409, "Archived product cannot change stock"};
    const auto total = row_number(result.rows.front(), "stock_on_hand");
    if (total < 0 || total > max_stock)
        throw ApiFailure{409, "Invalid central stock balance"};
    return total;
}

void active_location(const ConnectionPtr& connection, std::int64_t location) {
    const auto found = connection->execute(
        "SELECT l.active AS location_active, w.active AS warehouse_active "
        "FROM warehouse_locations l JOIN warehouses w ON w.id=l.warehouse_id "
        "WHERE l.id = ?", {location});
    if (found.rows.empty()) throw ApiFailure{404, "Location not found"};
    if (row_number(found.rows.front(), "location_active") == 0 ||
        row_number(found.rows.front(), "warehouse_active") == 0)
        throw ApiFailure{409, "Location or warehouse is inactive"};
}

std::int64_t location_total(const ConnectionPtr& connection, std::int64_t product) {
    const auto result = connection->execute(
        "SELECT COALESCE(SUM(quantity), 0) AS total "
        "FROM location_balances WHERE product_id = ?", {product});
    return row_number(result.rows.front(), "total");
}

std::int64_t at_location(const ConnectionPtr& connection, std::int64_t product,
                         std::int64_t location) {
    const auto rows = connection->execute(
        "SELECT quantity FROM location_balances WHERE product_id=? AND location_id=?",
        {product, location});
    return rows.rows.empty() ? 0 : row_number(rows.rows.front(), "quantity");
}

void write_location(const ConnectionPtr& connection, std::int64_t product,
                    std::int64_t location, std::int64_t before, std::int64_t after) {
    if (before < 0 || after < 0 || after > max_stock)
        throw ApiFailure{409, "Invalid warehouse balance"};
    const auto update = connection->execute(
        "UPDATE location_balances SET quantity=?, updated_at=CURRENT_TIMESTAMP "
        "WHERE product_id=? AND location_id=? AND quantity=?",
        {after, product, location, before});
    if (update.affected_rows == 1) return;
    if (before != 0) throw ApiFailure{409, "Concurrent location change"};
    const auto insert = connection->execute(
        "INSERT INTO location_balances "
        "(product_id,location_id,quantity,created_at,updated_at) "
        "VALUES (?,?,?,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP) "
        "ON CONFLICT(product_id,location_id) DO NOTHING",
        {product, location, after});
    if (insert.affected_rows != 1)
        throw ApiFailure{409, "Concurrent location change"};
}

void location_journal(const ConnectionPtr& connection, const Command& command,
                      std::int64_t location, std::int64_t delta) {
    connection->execute(
        "INSERT INTO location_movements "
        "(reference,product_id,location_id,actor_id,kind,quantity_delta,reason,created_at,updated_at) "
        "VALUES (?,?,?,?,?,?,?,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)",
        {command.reference, command.product_id, location, command.actor_id,
         command.kind, delta, command.reason});
}

bool claim(const ConnectionPtr& connection, const Command& command, std::int64_t central) {
    const auto inserted = connection->execute(
        "INSERT INTO inventory_operations "
        "(reference,product_id,actor_id,kind,quantity,quantity_delta,stock_after,reason,created_at,updated_at) "
        "VALUES (?,?,?,?,?,0,?,?,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP) "
        "ON CONFLICT(reference) DO NOTHING",
        {command.reference, command.product_id, command.actor_id,
         command.kind, command.quantity, central, command.reason});
    if (inserted.affected_rows == 1) return true;
    const auto old = connection->execute(
        "SELECT product_id,actor_id,kind,quantity,reason FROM inventory_operations "
        "WHERE reference=?", {command.reference});
    if (old.rows.empty()) throw ApiFailure{503, "Inventory operation unavailable"};
    const auto& row = old.rows.front();
    if (row_number(row,"product_id") != command.product_id ||
        row_number(row,"actor_id") != command.actor_id ||
        row_number(row,"quantity") != command.quantity ||
        row_text(row,"kind") != command.kind ||
        row_text(row,"reason") != command.reason)
        throw ApiFailure{409, "Reference already used for another operation"};
    return false;
}

void finalize(const ConnectionPtr& connection, const Command& command,
              std::int64_t delta, std::int64_t central) {
    const auto updated = connection->execute(
        "UPDATE inventory_operations SET quantity_delta=?,stock_after=?,updated_at=CURRENT_TIMESTAMP "
        "WHERE reference=?", {delta,central,command.reference});
    if (updated.affected_rows != 1)
        throw ApiFailure{503, "Inventory operation not finalized"};
}

Command command_from(Request& request, std::initializer_list<std::string_view> allowed,
                     std::string_view kind, bool allow_correction = false) {
    const auto id = require_manager(request);
    const auto& body = object_body(request, allowed);
    Command command;
    command.actor_id = id;
    command.product_id = integer_field(body, "product_id", 1, max_stock);
    command.kind = std::string{kind};
    command.quantity = integer_field(body, "quantity", allow_correction ? 0 : 1,
                                     allow_correction ? max_stock : max_movement);
    command.reference = text_field(body, "reference", 64);
    for (unsigned char c : command.reference)
        if (!std::isalnum(c) && c != '-' && c != '_' && c != '.' && c != ':')
            throw ApiFailure{422, "Invalid operation reference"};
    if (command.reference.size() < 8) throw ApiFailure{422, "Reference is too short"};
    command.reason = text_field(body, "reason", 32);
    return command;
}

Json operation_response(const Command& command, std::int64_t delta,
                        std::int64_t stock, bool replayed,
                        std::int64_t location, std::int64_t local_stock) {
    return Json::object({
        {"product_id",command.product_id},{"actor_id",command.actor_id},
        {"kind",command.kind},{"quantity",command.quantity},
        {"quantity_delta",delta},{"stock_after",stock},
        {"location_id",location},{"location_after",local_stock},
        {"reference",command.reference},{"reason",command.reason},
        {"replayed",replayed}
    });
}

Response create_warehouse(Request& request) {
    return handle([&]() -> Response {
        require_manager(request);
        const auto& data = object_body(request, {"code","name"});
        auto code = code_field(data), name = text_field(data,"name",128);
        auto conn = DB::connection();
        if (!conn->execute("SELECT id FROM warehouses WHERE code=?", {code}).rows.empty())
            throw ApiFailure{409,"Warehouse code already exists"};
        auto inserted = conn->execute(
            "INSERT INTO warehouses (code,name,active,created_at,updated_at) "
            "VALUES (?,?,1,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)", {code,name});
        const auto rows = conn->execute(
            "SELECT id,code,name,active FROM warehouses WHERE id=?", {*inserted.inserted_id});
        return Response::json(object_row(rows.rows.front()),201);
    });
}

Response list_warehouses(Request& request) {
    return handle([&]() -> Response {
        require_reader(request);
        const auto records = DB::connection()->execute(
            "SELECT id,code,name,active FROM warehouses ORDER BY id LIMIT 100");
        return Response::json(Json::object({{"data", rows_json(records.rows)}}));
    });
}

Response create_location(Request& request) {
    return handle([&]() -> Response {
        require_manager(request);
        const auto& data = object_body(request, {"warehouse_id","code","name"});
        const auto warehouse = integer_field(data,"warehouse_id",1,max_stock);
        auto code = code_field(data), name = text_field(data,"name",128);
        auto conn = DB::connection();
        const auto owner = conn->execute("SELECT active FROM warehouses WHERE id=?", {warehouse});
        if (owner.rows.empty()) throw ApiFailure{404, "Warehouse not found"};
        if (row_number(owner.rows.front(),"active") == 0)
            throw ApiFailure{409,"Warehouse inactive"};
        if (!conn->execute("SELECT id FROM warehouse_locations WHERE code=?", {code}).rows.empty())
            throw ApiFailure{409,"Location code already exists"};
        const auto added = conn->execute(
            "INSERT INTO warehouse_locations "
            "(warehouse_id,code,name,active,created_at,updated_at) "
            "VALUES (?,?,?,1,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)",
            {warehouse,code,name});
        const auto row = conn->execute(
            "SELECT id,warehouse_id,code,name,active FROM warehouse_locations WHERE id=?",
            {*added.inserted_id});
        return Response::json(object_row(row.rows.front()),201);
    });
}

Response list_locations(Request& request) {
    return handle([&]() -> Response {
        require_reader(request);
        auto conn = DB::connection();
        auto value = std::string{request.query("warehouse_id")};
        auto result = value.empty()
            ? conn->execute("SELECT id,warehouse_id,code,name,active FROM warehouse_locations ORDER BY id LIMIT 100")
            : conn->execute("SELECT id,warehouse_id,code,name,active FROM warehouse_locations "
                            "WHERE warehouse_id=? ORDER BY id LIMIT 100", [&]() {
                if (value.size() > 10 ||
                    value.find_first_not_of("0123456789") != std::string::npos)
                    throw ApiFailure{422,"Invalid warehouse ID"};
                const auto id = std::stoll(value);
                if (id < 1 || id > max_stock) throw ApiFailure{422,"Invalid warehouse ID"};
                return std::vector<AttributeValue>{id};
            }());
        return Response::json(Json::object({{"data",rows_json(result.rows)}}));
    });
}

Response balances(Request& request) {
    return handle([&]() -> Response {
        require_reader(request);
        const auto product = route_id(request,"product");
        auto conn = DB::connection();
        const auto central = product_total(conn,product,false);
        const auto located = location_total(conn,product);
        if (located < 0 || located > central)
            throw ApiFailure{409,"Warehouse balances do not reconcile with total stock"};
        const auto found = conn->execute(
            "SELECT b.location_id,l.warehouse_id,l.code AS location_code,"
            "b.quantity FROM location_balances b "
            "JOIN warehouse_locations l ON l.id=b.location_id "
            "WHERE b.product_id=? ORDER BY b.location_id LIMIT 100",
            {product});
        return Response::json(Json::object({
            {"product_id",product},{"stock_on_hand",central},
            {"located",located},{"unallocated",central-located},
            {"reconciled",true},{"locations",rows_json(found.rows)},
            {"locations_truncated",found.rows.size()==100}
        }));
    });
}

Response allocate(Request& request, std::mutex& gate) {
    return handle([&]() -> Response {
        auto command = command_from(request,
            {"product_id","location_id","quantity","reference","reason"}, "allocate");
        const auto location = integer_field(request.json(),"location_id",1,max_stock);
        std::lock_guard lock{gate};
        return DB::transaction([&]() -> Response {
            auto conn=DB::connection();
            const auto central=product_total(conn,command.product_id,false);
            if (!claim(conn,command,central)) {
                const auto found=conn->execute(
                    "SELECT location_id,location_after FROM inventory_location_operations "
                    "WHERE reference=?", {command.reference});
                if (found.rows.empty() || row_number(found.rows.front(),"location_id")!=location)
                    throw ApiFailure{409,"Reference belongs to another allocation"};
                return Response::json(operation_response(command,0,central,true,location,
                           row_number(found.rows.front(),"location_after")),200);
            }
            product_total(conn,command.product_id,true);
            active_location(conn,location);
            const auto located=location_total(conn,command.product_id);
            if (located<0 || located>central || command.quantity>central-located)
                throw ApiFailure{409,"Insufficient unallocated stock"};
            const auto before=at_location(conn,command.product_id,location);
            if (before>max_stock-command.quantity)
                throw ApiFailure{409,"Location stock capacity exceeded"};
            const auto after=before+command.quantity;
            write_location(conn,command.product_id,location,before,after);
            conn->execute(
                "INSERT INTO inventory_location_operations "
                "(reference,location_id,location_after,created_at,updated_at) "
                "VALUES (?,?,?,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)",
                {command.reference,location,after});
            location_journal(conn,command,location,command.quantity);
            finalize(conn,command,0,central);
            return Response::json(operation_response(command,0,central,false,location,after),201);
        });
    });
}

Response transfer(Request& request, std::mutex& gate) {
    return handle([&]() -> Response {
        const auto command=command_from(request,
            {"product_id","source_location_id","destination_location_id","quantity","reference","reason"},
            "transfer");
        const auto source=integer_field(request.json(),"source_location_id",1,max_stock);
        const auto destination=integer_field(request.json(),"destination_location_id",1,max_stock);
        if (source==destination) throw ApiFailure{422,"Source and destination must differ"};
        std::lock_guard lock{gate};
        return DB::transaction([&]() -> Response {
            auto conn=DB::connection();
            const auto central=product_total(conn,command.product_id,false);
            if (!claim(conn,command,central)) {
                const auto prior=conn->execute(
                    "SELECT source_location_id,destination_location_id FROM warehouse_transfers "
                    "WHERE reference=?", {command.reference});
                if (prior.rows.empty() || row_number(prior.rows.front(),"source_location_id")!=source ||
                    row_number(prior.rows.front(),"destination_location_id")!=destination)
                    throw ApiFailure{409,"Reference belongs to a different transfer"};
                return Response::json(Json::object({
                    {"product_id",command.product_id},{"source_location_id",source},
                    {"destination_location_id",destination},{"quantity",command.quantity},
                    {"stock_after",central},{"reference",command.reference},{"replayed",true}
                }),200);
            }
            product_total(conn,command.product_id,true);
            active_location(conn,source);
            active_location(conn,destination);
            const auto origin=at_location(conn,command.product_id,source);
            const auto target=at_location(conn,command.product_id,destination);
            if (command.quantity>origin) throw ApiFailure{409,"Insufficient stock at source location"};
            if (target>max_stock-command.quantity)
                throw ApiFailure{409,"Destination stock capacity exceeded"};
            write_location(conn,command.product_id,source,origin,origin-command.quantity);
            write_location(conn,command.product_id,destination,target,target+command.quantity);
            conn->execute(
                "INSERT INTO warehouse_transfers "
                "(reference,product_id,source_location_id,destination_location_id,actor_id,quantity,created_at,updated_at) "
                "VALUES (?,?,?,?,?,?,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)",
                {command.reference,command.product_id,source,destination,
                 command.actor_id,command.quantity});
            location_journal(conn,command,source,-command.quantity);
            location_journal(conn,command,destination,command.quantity);
            finalize(conn,command,0,central);
            return Response::json(Json::object({
                {"product_id",command.product_id},{"source_location_id",source},
                {"destination_location_id",destination},{"quantity",command.quantity},
                {"source_after",origin-command.quantity},
                {"destination_after",target+command.quantity},
                {"stock_after",central},{"reference",command.reference},{"replayed",false}
            }),201);
        });
    });
}

Response location_movement(Request& request, std::mutex& gate) {
    return handle([&]() -> Response {
        require_manager(request);
        const auto& body=object_body(request,
            {"product_id","location_id","kind","quantity","reference","reason"});
        const auto kind=text_field(body,"kind",16);
        if (kind!="receipt" && kind!="issue" && kind!="return" && kind!="correction")
            throw ApiFailure{422,"Unsupported location movement kind"};
        const auto command=command_from(request,
            {"product_id","location_id","kind","quantity","reference","reason"},
            kind,kind=="correction");
        const auto location=integer_field(body,"location_id",1,max_stock);
        std::lock_guard lock{gate};
        return DB::transaction([&]() -> Response {
            auto conn=DB::connection();
            const auto central=product_total(conn,command.product_id,false);
            if (!claim(conn,command,central)) {
                const auto prior=conn->execute(
                    "SELECT location_id,location_after FROM inventory_location_operations "
                    "WHERE reference=?", {command.reference});
                if (prior.rows.empty() || row_number(prior.rows.front(),"location_id")!=location)
                    throw ApiFailure{409,"Reference belongs to another location movement"};
                const auto op=conn->execute(
                    "SELECT quantity_delta,stock_after FROM inventory_operations WHERE reference=?",
                    {command.reference});
                return Response::json(operation_response(command,
                    row_number(op.rows.front(),"quantity_delta"),row_number(op.rows.front(),"stock_after"),
                    true,location,row_number(prior.rows.front(),"location_after")),200);
            }
            product_total(conn,command.product_id,true);
            active_location(conn,location);
            const auto before=at_location(conn,command.product_id,location);
            const auto delta=command.kind=="correction"
                ? command.quantity-before
                : (command.kind=="issue" ? -command.quantity : command.quantity);
            if (delta==0) throw ApiFailure{409,"Correction does not change stock"};
            const auto local_after=before+delta;
            const auto global_after=central+delta;
            if (local_after<0) throw ApiFailure{409,"Insufficient stock at location"};
            if (global_after<0 || global_after>max_stock)
                throw ApiFailure{409,"Central stock capacity exceeded"};
            const auto located=location_total(conn,command.product_id);
            if (global_after<located+delta)
                throw ApiFailure{409,"Warehouse stock exceeds central stock"};
            const auto update=conn->execute(
                "UPDATE products SET stock_on_hand=?,updated_at=CURRENT_TIMESTAMP "
                "WHERE id=? AND stock_on_hand=? AND active=1",
                {global_after,command.product_id,central});
            if (update.affected_rows!=1) throw ApiFailure{409,"Concurrent central stock change"};
            write_location(conn,command.product_id,location,before,local_after);
            conn->execute(
                "INSERT INTO stock_movements "
                "(product_id,quantity_delta,reason,reference,created_at,updated_at) "
                "VALUES (?,?,?,?,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)",
                {command.product_id,delta,command.reason,command.reference});
            conn->execute(
                "INSERT INTO inventory_location_operations "
                "(reference,location_id,location_after,created_at,updated_at) "
                "VALUES (?,?,?,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)",
                {command.reference,location,local_after});
            location_journal(conn,command,location,delta);
            finalize(conn,command,delta,global_after);
            return Response::json(operation_response(command,delta,global_after,false,
                                                        location,local_after),201);
        });
    });
}

Response location_history(Request& request) {
    return handle([&]() -> Response {
        require_reader(request);
        const auto location=route_id(request,"location");
        std::int64_t after=0;
        const auto cursor=std::string{request.query("after")};
        if (!cursor.empty()) {
            if (cursor.size()>18 || cursor.find_first_not_of("0123456789")!=std::string::npos)
                throw ApiFailure{422,"Invalid history cursor"};
            after=std::stoll(cursor);
            if (after<0) throw ApiFailure{422,"Invalid history cursor"};
        }
        auto conn=DB::connection();
        if (conn->execute("SELECT id FROM warehouse_locations WHERE id=?", {location}).rows.empty())
            throw ApiFailure{404,"Location not found"};
        const auto rows=conn->execute(
            "SELECT id,reference,product_id,location_id,actor_id,kind,quantity_delta,reason,created_at "
            "FROM location_movements WHERE location_id=? AND id>? ORDER BY id LIMIT 21",
            {location,after});
        Json::Array data;
        const bool more=rows.rows.size()>20;
        std::int64_t next{};
        for(std::size_t i=0;i<std::min<std::size_t>(20,rows.rows.size());++i) {
            data.push_back(object_row(rows.rows[i]));
            next=row_number(rows.rows[i],"id");
        }
        return Response::json(Json::object({
            {"data",Json::array(std::move(data))},
            {"has_more",more},
            {"next_cursor",more ? std::to_string(next) : std::string{}}
        }));
    });
}

void register_routes(gungnir::Application& app) {
    const auto gate=inventory::transactions::shared_gate();
    app.router().get("/api/warehouses",[](Request& r){return list_warehouses(r);});
    app.router().post("/api/warehouses",[](Request& r){return create_warehouse(r);});
    app.router().get("/api/locations",[](Request& r){return list_locations(r);});
    app.router().post("/api/locations",[](Request& r){return create_location(r);});
    app.router().get("/api/inventory/products/{product}/balances",
        [](Request& r){return balances(r);});
    app.router().post("/api/inventory/allocations",
        [gate](Request& r){return allocate(r,*gate);});
    app.router().post("/api/inventory/transfers",
        [gate](Request& r){return transfer(r,*gate);});
    app.router().post("/api/inventory/location-movements",
        [gate](Request& r){return location_movement(r,*gate);});
    app.router().get("/api/inventory/locations/{location}/movements",
        [](Request& r){return location_history(r);});
}
} // namespace inventory::locations
