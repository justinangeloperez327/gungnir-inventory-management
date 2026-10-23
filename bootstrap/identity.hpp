#pragma once

#include <gungnir/auth/login.hpp>
#include <gungnir/database/db.hpp>
#include <gungnir/model/value.hpp>

#include <optional>
#include <string>
#include <string_view>
#include <utility>

namespace inventory {
inline std::optional<gungnir::auth::Identity> user_identity(
    const gungnir::model::AttributeMap& row
) {
    using gungnir::model::value_cast;
    if (!value_cast<bool>(row.at("active"))) return std::nullopt;

    gungnir::auth::Identity identity;
    identity.id = std::to_string(value_cast<gungnir::Int64>(row.at("id")));
    identity.roles.insert(value_cast<gungnir::String>(row.at("role")));
    identity.attributes.emplace("name", value_cast<gungnir::String>(row.at("name")));
    identity.attributes.emplace("email", value_cast<gungnir::String>(row.at("email")));
    return identity;
}

inline std::optional<gungnir::auth::Identity> resolve_identity(
    std::string_view id
) {
    if (id.empty() || id.size() > 19 ||
        id.find_first_not_of("0123456789") != std::string_view::npos) {
        return std::nullopt;
    }
    // Bind parameters, never interpolate user-controlled session IDs into SQL.
    const auto records = gungnir::database::DB::connection()->execute(
        "SELECT id, name, email, role, active FROM users WHERE id = ? LIMIT 1",
        {std::string{id}}
    );
    if (records.rows.empty()) return std::nullopt;
    return user_identity(records.rows.front());
}

inline std::optional<gungnir::auth::PasswordIdentity> resolve_credentials(
    std::string_view email
) {
    // Email is normalized to lowercase by the provisioning command.
    const auto records = gungnir::database::DB::connection()->execute(
        "SELECT id, name, email, role, active, password_hash FROM users WHERE email = ? LIMIT 1",
        {std::string{email}}
    );
    if (records.rows.empty()) return std::nullopt;
    const auto& row = records.rows.front();
    auto identity = user_identity(row);
    if (!identity) return std::nullopt;
    return gungnir::auth::PasswordIdentity{
        std::move(*identity),
        gungnir::model::value_cast<gungnir::String>(row.at("password_hash"))
    };
}
} // namespace inventory
