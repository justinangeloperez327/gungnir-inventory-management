#pragma once

// Native Gungnir extension: all stock mutations are deliberately implemented
// at the database boundary, not as detached model reads followed by saves.
#include <gungnir/core/application.hpp>
#include <gungnir/database/db.hpp>
#include <gungnir/database/error.hpp>
#include <gungnir/http/json.hpp>
#include <gungnir/http/request.hpp>
#include <gungnir/http/response.hpp>
#include <gungnir/model/value.hpp>

#include <algorithm>
#include <cctype>
#include <cstdint>
#include <limits>
#include <memory>
#include <mutex>
#include <optional>
#include <stdexcept>
#include <string>
#include <string_view>
#include <utility>
#include <vector>

namespace inventory::transactions {
using gungnir::http::Json;
using gungnir::http::Request;
using gungnir::http::Response;
using gungnir::database::DB;
using gungnir::model::AttributeMap;
using gungnir::model::AttributeValue;

constexpr std::int64_t max_stock = 2147483647;
constexpr std::int64_t max_movement = 1000000;

struct ApiFailure final : std::runtime_error {
    int status;
    ApiFailure(int code, std::string message)
        : std::runtime_error(std::move(message)), status(code) {}
};

inline Response failure(int status, const std::string& message) {
    return Response::json(Json::object({{"error", message}}), status);
}

inline std::int64_t integer_field(const Json& body, std::string_view key,
                                 std::int64_t low, std::int64_t high) {
    const auto* value = body.get(key);
    if (!value || !value->is_integer())
        throw ApiFailure{422, std::string{key} + " must be an integer"};
    std::int64_t number{};
    try {
        std::size_t consumed{};
        const auto text = value->dump();
        number = std::stoll(text, &consumed);
        if (consumed != text.size()) throw std::invalid_argument("integer");
    } catch (const std::exception&) {
        throw ApiFailure{422, std::string{key} + " is out of range"};
    }
    if (number < low || number > high)
        throw ApiFailure{422, std::string{key} + " is out of range"};
    return number;
}

inline std::string text_field(const Json& body, std::string_view key, std::size_t max_length) {
    const auto* value = body.get(key);
    if (!value || !value->is_string())
        throw ApiFailure{422, std::string{key} + " must be a string"};
    auto result = value->string();
    if (result.empty() || result.size() > max_length)
        throw ApiFailure{422, std::string{key} + " has invalid length"};
    for (const unsigned char c : result)
        if (c < 32 || c == 127)
            throw ApiFailure{422, std::string{key} + " contains a control character"};
    return result;
}

inline std::int64_t row_number(const AttributeMap& row, std::string_view key) {
    return gungnir::model::value_cast<gungnir::Int64>(row.at(std::string{key}));
}
inline std::string row_text(const AttributeMap& row, std::string_view key) {
    return gungnir::model::value_cast<gungnir::String>(row.at(std::string{key}));
}

struct Command {
    std::int64_t product_id{};
    std::int64_t actor_id{};
    std::int64_t quantity{};
    std::string kind;
    std::string reference;
    std::string reason;
};

inline Command parse(const Request& request) {
    const auto* actor = request.user();
    if (!actor) throw ApiFailure{401, "Unauthenticated"};
    if (!actor->role("admin") && !actor->role("manager"))
        throw ApiFailure{403, "Forbidden"};
    if (!request.is_json()) throw ApiFailure{415, "JSON required"};

    const auto& body = request.json();
    if (!body.is_object()) throw ApiFailure{422, "A JSON object is required"};
    for (const auto& [key, _] : body.as_object()) {
        if (key != "product_id" && key != "kind" && key != "quantity" &&
            key != "reference" && key != "reason")
            throw ApiFailure{422, "Unknown inventory field"};
    }

    Command command;
    command.actor_id = 0;
    try {
        std::size_t n{};
        command.actor_id = std::stoll(actor->id, &n);
        if (n != actor->id.size() || command.actor_id <= 0)
            throw std::invalid_argument("actor");
    } catch (const std::exception&) {
        throw ApiFailure{403, "Invalid authenticated identity"};
    }
    command.product_id = integer_field(body, "product_id", 1, max_stock);
    command.kind = text_field(body, "kind", 16);
    if (command.kind != "receipt" && command.kind != "issue" &&
        command.kind != "return" && command.kind != "correction")
        throw ApiFailure{422, "Unsupported inventory movement kind"};
    // Correction quantity represents the absolute target stock count.
    command.quantity = integer_field(body, "quantity",
        command.kind == "correction" ? 0 : 1,
        command.kind == "correction" ? max_stock : max_movement);
    command.reference = text_field(body, "reference", 64);
    const auto good_ref = [](unsigned char c) {
        return std::isalnum(c) || c == '-' || c == '_' || c == '.' || c == ':';
    };
    if (command.reference.size() < 8 ||
        !std::all_of(command.reference.begin(), command.reference.end(),
                     [&](char c) { return good_ref(static_cast<unsigned char>(c)); }))
        throw ApiFailure{422, "Invalid reference; use 8-64 alphanumeric/._:- characters"};
    command.reason = text_field(body, "reason", 32);
    return command;
}

inline Response summary(const Command& command, std::int64_t delta,
                        std::int64_t after, bool replayed) {
    return Response::json(Json::object({
        {"product_id", command.product_id},
        {"actor_id", command.actor_id},
        {"kind", command.kind},
        {"quantity", command.quantity},
        {"quantity_delta", delta},
        {"stock_after", after},
        {"reference", command.reference},
        {"reason", command.reason},
        {"replayed", replayed}
    }), replayed ? 200 : 201);
}

inline std::shared_ptr<std::mutex> shared_gate() {
    static const auto gate = std::make_shared<std::mutex>();
    return gate;
}

inline Response perform(Request& request, std::mutex& gate) {
    try {
        const auto command = parse(request);
        // The in-process mutex avoids snapshot-upgrade races for this SQLite
        // instance. SQLite's unique claim and conditional UPDATE remain the
        // authoritative cross-process safeguards.
        std::lock_guard lock{gate};
        return DB::transaction([&]() -> Response {
            auto connection = DB::connection();
            // Resolve product existence before claiming an operation reference.
            // This maps a missing SKU to 404 instead of a foreign-key 503.
            const auto products = connection->execute(
                "SELECT stock_on_hand, active FROM products WHERE id = ?",
                {command.product_id}
            );
            if (products.rows.empty()) throw ApiFailure{404, "Product not found"};

            const auto claimed = connection->execute(
                "INSERT INTO inventory_operations "
                "(reference, product_id, actor_id, kind, quantity, quantity_delta, stock_after, reason, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, 0, 0, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP) "
                "ON CONFLICT(reference) DO NOTHING",
                {command.reference, command.product_id, command.actor_id,
                 command.kind, command.quantity, command.reason}
            );
            if (claimed.affected_rows == 0) {
                const auto found = connection->execute(
                    "SELECT product_id, actor_id, kind, quantity, quantity_delta, stock_after, reason "
                    "FROM inventory_operations WHERE reference = ?",
                    {command.reference}
                );
                if (found.rows.empty())
                    throw ApiFailure{503, "Inventory operation temporarily unavailable"};
                const auto& previous = found.rows.front();
                if (row_number(previous, "product_id") != command.product_id ||
                    row_number(previous, "actor_id") != command.actor_id ||
                    row_number(previous, "quantity") != command.quantity ||
                    row_text(previous, "kind") != command.kind ||
                    row_text(previous, "reason") != command.reason)
                    throw ApiFailure{409, "Reference already used for another operation"};
                // A location-specific operation cannot be replayed by the
                // legacy unallocated-stock endpoint, even with a matching body.
                if (!connection->execute(
                    "SELECT reference FROM inventory_location_operations WHERE reference = ?",
                    {command.reference}
                ).rows.empty())
                    throw ApiFailure{409, "Reference belongs to a location-specific movement"};
                return summary(command, row_number(previous, "quantity_delta"),
                               row_number(previous, "stock_after"), true);
            }

            const auto& product = products.rows.front();
            if (row_number(product, "active") == 0)
                throw ApiFailure{409, "Archived products cannot change stock"};
            const auto current = row_number(product, "stock_on_hand");
            if (current < 0 || current > max_stock)
                throw ApiFailure{409, "Stored product balance is invalid"};

            const auto delta = command.kind == "correction"
                ? command.quantity - current
                : (command.kind == "issue" ? -command.quantity : command.quantity);
            const auto after = current + delta;
            if (after < 0) throw ApiFailure{409, "Insufficient stock"};
            if (after > max_stock) throw ApiFailure{409, "Stock capacity exceeded"};
            if (delta == 0) throw ApiFailure{409, "Correction does not change stock"};

            // Legacy Group 4 operations are unallocated inventory changes.
            // Never debit allocated stock via this endpoint or adjust the
            // central total below the sum of warehouse/location balances.
            const auto located = connection->execute(
                "SELECT COALESCE(SUM(quantity), 0) AS total FROM location_balances WHERE product_id = ?",
                {command.product_id}
            );
            if (located.rows.empty())
                throw ApiFailure{503, "Location balance could not be evaluated"};
            if (after < row_number(located.rows.front(), "total"))
                throw ApiFailure{409, "Insufficient unallocated stock"};

            // Optimistic CAS prevents a stale read overwriting a concurrent
            // inventory transaction or another writer's stock balance.
            const auto changed = connection->execute(
                "UPDATE products SET stock_on_hand = ?, updated_at = CURRENT_TIMESTAMP "
                "WHERE id = ? AND stock_on_hand = ? AND active = 1",
                {after, command.product_id, current}
            );
            if (changed.affected_rows != 1)
                throw ApiFailure{409, "Concurrent stock change; retry with a new reference"};

            connection->execute(
                "INSERT INTO stock_movements "
                "(product_id, quantity_delta, reason, reference, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)",
                {command.product_id, delta, command.reason, command.reference}
            );
            const auto operation = connection->execute(
                "UPDATE inventory_operations SET quantity_delta = ?, stock_after = ?, updated_at = CURRENT_TIMESTAMP "
                "WHERE reference = ?",
                {delta, after, command.reference}
            );
            if (operation.affected_rows != 1)
                throw ApiFailure{503, "Inventory operation could not be finalized"};
            return summary(command, delta, after, false);
        });
    } catch (const ApiFailure& error) {
        return failure(error.status, error.what());
    } catch (const gungnir::database::Error&) {
        // Never expose SQL, connection details or untrusted input in errors.
        return failure(503, "Inventory database temporarily unavailable");
    } catch (const std::exception&) {
        return failure(400, "Invalid inventory request");
    }
}

inline Response history(Request& request) {
    if (!request.authenticated()) return failure(401, "Unauthenticated");
    try {
        const auto product_string = std::string{request.parameter("product")};
        if (product_string.empty() || product_string.size() > 10 ||
            product_string.find_first_not_of("0123456789") != std::string::npos)
            return failure(404, "Product not found");
        const auto product = std::stoll(product_string);
        if (product <= 0 || product > max_stock)
            return failure(404, "Product not found");
        std::int64_t after = 0;
        const auto cursor = std::string{request.query("after")};
        if (!cursor.empty()) {
            if (cursor.size() > 19 ||
                cursor.find_first_not_of("0123456789") != std::string::npos)
                return failure(422, "Invalid history cursor");
            after = std::stoll(cursor);
        }
        const auto connection = DB::connection();
        if (connection->execute("SELECT id FROM products WHERE id = ?", {product}).rows.empty())
            return failure(404, "Product not found");
        const auto found = connection->execute(
            "SELECT m.id, m.product_id, m.quantity_delta, m.reason, m.reference, m.created_at, "
            "o.kind, o.actor_id, o.stock_after FROM stock_movements m "
            "LEFT JOIN inventory_operations o ON o.reference = m.reference "
            "WHERE m.product_id = ? AND m.id > ? ORDER BY m.id LIMIT 21",
            {product, after}
        );
        Json::Array items;
        const bool has_more = found.rows.size() > 20;
        std::int64_t next = 0;
        for (std::size_t i = 0; i < std::min<std::size_t>(20, found.rows.size()); ++i) {
            Json::Object record;
            for (const auto& [column, value] : found.rows[i])
                record.emplace(column, gungnir::http::make_json(value));
            next = row_number(found.rows[i], "id");
            items.push_back(Json::object(std::move(record)));
        }
        return Response::json(Json::object({
            {"data", Json::array(std::move(items))},
            {"has_more", has_more},
            {"next_cursor", has_more ? std::to_string(next) : std::string{}}
        }));
    } catch (const gungnir::database::Error&) {
        return failure(503, "Inventory database temporarily unavailable");
    } catch (const std::exception&) {
        return failure(422, "Invalid history cursor");
    }
}

inline void register_routes(gungnir::Application& app) {
    auto gate = shared_gate();
    // The global app middleware still enforces sessions, identity and CSRF.
    app.router().post("/api/inventory/movements",
        [gate](Request& request) { return perform(request, *gate); });
    app.router().get("/api/inventory/products/{product}/movements",
        [](Request& request) { return history(request); });
}
} // namespace inventory::transactions
