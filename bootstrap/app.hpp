#pragma once

#include <gungnir/core/services.hpp>
#include <gungnir/cache/memory_store.hpp>
#include <gungnir/queue/memory_driver.hpp>
#include <gungnir/mail/memory_transport.hpp>
#include <gungnir/storage/local_disk.hpp>
#include <gungnir/session/memory_store.hpp>
#include <gungnir/session/middleware.hpp>
#include <gungnir/auth/session.hpp>
#include <gungnir/auth/login.hpp>
#include <gungnir/http/security.hpp>
#include "identity.hpp"
#include "inventory_transactions.hpp"
#include "inventory_locations.hpp"
#include "procurement.hpp"

#include <chrono>
#include <memory>

namespace bootstrap {
inline void configure(gungnir::Application& app) {
    gungnir::ServiceOptions services;
    services.cache = std::make_shared<gungnir::cache::MemoryStore>();
    services.cache_locks = std::make_shared<gungnir::cache::MemoryLockStore>();
    services.queue = std::make_shared<gungnir::queue::MemoryDriver>();
    services.scheduler_locks = std::make_shared<gungnir::scheduler::MemoryLockStore>();
    services.mail = std::make_shared<gungnir::mail::MemoryTransport>();
    services.database_notifications = !app.config().string("database.default").empty();
    services.storage = std::make_shared<gungnir::storage::Manager>();
    services.storage->add("local", std::make_shared<gungnir::storage::LocalDisk>(app.base_path() / "storage"));

    // Both the provider and middleware MUST share the same guard instance.
    // Remember-me is disabled until a durable token store is configured.
    const auto identities = inventory::resolve_identity;
    auto guard = std::make_shared<gungnir::auth::SessionGuard>(
        inventory::resolve_credentials, identities);
    services.authentication = guard;
    app.provider<gungnir::ServicesProvider>(std::move(services));

    auto session_store = std::make_shared<gungnir::session::MemoryStore>();
    gungnir::session::Options session_options;
    // Local development is plain HTTP. Production must be served over HTTPS
    // with a durable session store before this app can be released.
    session_options.secure = app.is_production();

    app.router().use(gungnir::http::security_headers());
    app.router().use(gungnir::http::body_limit(64 * 1024));
    app.router().use(gungnir::session::middleware(session_store, session_options));
    app.router().use(gungnir::http::csrf());
    app.router().use(gungnir::auth::session(identities));
    app.router().use(gungnir::auth::guard(guard));

    inventory::transactions::register_routes(app);
    inventory::locations::register_routes(app);
    inventory::procurement::register_routes(app);

    // This limiter is process-local; a distributed store is required to
    // enforce an equivalent quota across multiple servers.
    app.middleware_registry().alias("login-throttle", gungnir::http::rate_limit(
        {.requests = 12, .window = std::chrono::seconds{60}}));
}

inline void boot(gungnir::Application&) {}
} // namespace bootstrap
