#include <gungnir/gungnir.hpp>
#include <gungnir/auth/password.hpp>
#include <gungnir/database/db.hpp>

#include <algorithm>
#include <cctype>
#include <iostream>
#include <regex>
#include <stdexcept>
#include <string>

int main() {
    // Four newline-separated values on stdin: name, email, role and password.
    // Never accept the password in process arguments or echo it to stdout.
    try {
        std::string name, email, role, password;
        if (!std::getline(std::cin, name) ||
            !std::getline(std::cin, email) ||
            !std::getline(std::cin, role) ||
            !std::getline(std::cin, password)) {
            throw std::invalid_argument("Missing user details on stdin");
        }
        std::transform(email.begin(), email.end(), email.begin(), [](unsigned char c) {
            return static_cast<char>(std::tolower(c));
        });
        if (name.empty() || name.size() > 100 ||
            !std::regex_match(email, std::regex{R"(^[A-Za-z0-9.!#$%&'*+/=?^`{|}~-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+$)"}) ||
            email.size() > 254 ||
            (role != "admin" && role != "manager" && role != "viewer") ||
            password.size() < 12 || password.size() > 128) {
            throw std::invalid_argument("Invalid name, email, role or password policy");
        }

        auto app = gungnir::Application::create();
        app.boot();
        auto scope = app.activate();
        auto connection = gungnir::database::DB::connection();
        if (!connection->execute("SELECT id FROM users WHERE email = ? LIMIT 1",
                                 {email}).rows.empty()) {
            throw std::invalid_argument("Account already exists");
        }
        const auto hash = gungnir::auth::Password::hash(password);
        std::fill(password.begin(), password.end(), '\0');
        connection->execute(
            "INSERT INTO users (name, email, password_hash, role, active) VALUES (?, ?, ?, ?, 1)",
            {name, email, hash, role}
        );
        app.shutdown();
        std::cout << "User provisioned successfully\n";
        return 0;
    } catch (const std::exception& error) {
        std::cerr << "Provisioning failed: " << error.what() << "\n";
        return 1;
    }
}
