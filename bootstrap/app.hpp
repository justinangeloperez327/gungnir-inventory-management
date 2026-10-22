#pragma once
#include <gungnir/core/services.hpp>
#include <gungnir/cache/memory_store.hpp>
#include <gungnir/queue/memory_driver.hpp>
#include <gungnir/mail/memory_transport.hpp>
#include <gungnir/storage/local_disk.hpp>

namespace bootstrap {
// Development-only service providers. Replace process-local drivers before
// running multiple instances or moving queue processing out of process.
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
    app.provider<gungnir::ServicesProvider>(std::move(services));
}
inline void boot(gungnir::Application&) {}
}
