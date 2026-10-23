"""Exercise real SQLite-backed authentication, authorization and CSRF over HTTP."""
import http.cookiejar
import json
import os
import pathlib
import re
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parents[1]
BASE = "http://127.0.0.1:8000"
CI_PASSWORD = "CI-Only-Inventory-Passphrase-12345!"


def new_client():
    jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    return opener, jar


def fetch(opener, path, data=None):
    headers = {}
    if data is not None:
        headers["Content-Type"] = "application/x-www-form-urlencoded"
        data = urllib.parse.urlencode(data).encode()
    request = urllib.request.Request(BASE + path, data=data, headers=headers)
    try:
        with opener.open(request, timeout=3) as response:
            return response.status, response.read().decode()
    except urllib.error.HTTPError as error:
        return error.code, error.read().decode()


def session_id(jar):
    for cookie in jar:
        if cookie.name == "gungnir_session":
            return cookie.value
    return None


def csrf(html):
    match = re.search(r'name="_token" value="([a-zA-Z0-9]+)"', html)
    assert match, "Missing session-bound CSRF token"
    return match.group(1)


def check_login(role, expected_manager, expected_admin):
    opener, cookies = new_client()
    status, html = fetch(opener, "/login")
    assert status == 200 and "Sign in" in html
    token = csrf(html)
    old_session = session_id(cookies)
    assert old_session, "No session cookie created on GET /login"

    assert fetch(opener, "/login", {"email": role + "@inventory.test", "password": CI_PASSWORD})[0] == 419
    assert fetch(opener, "/login", {"_token": "invalid", "email": role + "@inventory.test", "password": CI_PASSWORD})[0] == 419
    assert fetch(opener, "/login", {"_token": token, "email": role + "@inventory.test", "password": "wrong-password"})[0] == 401
    status, body = fetch(opener, "/login", {
        "_token": token, "email": role + "@inventory.test", "password": CI_PASSWORD,
    })
    assert status == 200 and "Signed in:" in body, (role, status, body[:300])
    authenticated_session = session_id(cookies)
    assert authenticated_session and authenticated_session != old_session, "Session fixation vulnerability"

    status, body = fetch(opener, "/api/me")
    user = json.loads(body)["user"]
    assert status == 200 and user["roles"] == [role], (role, body)
    assert "password_hash" not in body and "password" not in body

    status, body = fetch(opener, "/products")
    assert status == 200 and "No products yet" in body
    status, body = fetch(opener, "/api/products")
    assert status == 200 and json.loads(body) == []

    assert fetch(opener, "/api/management/status")[0] == expected_manager
    assert fetch(opener, "/api/admin/status")[0] == expected_admin
    assert fetch(opener, "/logout", {})[0] == 419

    _, dashboard = fetch(opener, "/")
    logout_token = csrf(dashboard)
    status, _ = fetch(opener, "/logout", {"_token": logout_token})
    assert status == 200  # Follows redirect back to the sign-in form
    assert fetch(opener, "/api/me")[0] == 401
    assert fetch(opener, "/products")[0] == 401
    assert session_id(cookies) != authenticated_session, "Logout did not invalidate session"


def main():
    log_path = ROOT / "storage" / "smoke-server.log"
    with open(log_path, "w", encoding="utf-8") as logs:
        process = subprocess.Popen(["gungnir", "run"], cwd=ROOT, stdout=logs, stderr=subprocess.STDOUT,
                                   start_new_session=(os.name != "nt"))
        try:
            deadline = time.monotonic() + 70
            anonymous, _ = new_client()
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    raise RuntimeError("Gungnir exited before becoming ready")
                try:
                    status, body = fetch(anonymous, "/health")
                    if status == 200 and json.loads(body).get("status") == "ok":
                        break
                except (OSError, ValueError):
                    time.sleep(0.5)
            else:
                raise RuntimeError("Timed out waiting for Gungnir server")

            for path in ("/", "/products", "/api/products", "/api/me",
                         "/api/management/status", "/api/admin/status"):
                assert fetch(anonymous, path)[0] == 401, "Public read bypass: " + path

            check_login("viewer", 403, 403)
            check_login("manager", 200, 403)
            check_login("admin", 200, 200)
            print("PASS: auth, CSRF, credential validation, role-based routes, session rotation and logout")
        except Exception:
            print(log_path.read_text(encoding="utf-8"), file=sys.stderr)
            raise
        finally:
            if process.poll() is None:
                if os.name == "nt":
                    process.terminate()
                else:
                    os.killpg(process.pid, signal.SIGTERM)
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)


if __name__ == "__main__":
    main()
