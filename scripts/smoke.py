"""Run the built Gungnir development app and verify the public read-only endpoints."""
import json
import os
import pathlib
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parents[1]
BASE = "http://127.0.0.1:8000"


def fetch(path):
    with urllib.request.urlopen(BASE + path, timeout=2) as response:
        return response.status, response.headers.get("Content-Type", ""), response.read().decode()


def main():
    log_path = ROOT / "storage" / "smoke-server.log"
    with open(log_path, "w", encoding="utf-8") as logs:
        process = subprocess.Popen(["gungnir", "run"], cwd=ROOT, stdout=logs, stderr=subprocess.STDOUT,
                                   start_new_session=(os.name != "nt"))
        try:
            deadline = time.monotonic() + 70
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    raise RuntimeError("Gungnir exited before becoming ready")
                try:
                    code, content_type, body = fetch("/health")
                    if code == 200 and json.loads(body).get("status") == "ok":
                        break
                except (OSError, ValueError):
                    time.sleep(0.5)
            else:
                raise RuntimeError("Timed out waiting for Gungnir server")
            code, content_type, body = fetch("/")
            assert code == 200 and "Inventory management" in body, "Dashboard response invalid"
            code, content_type, body = fetch("/products")
            assert code == 200 and "No products yet" in body, "Product list response invalid"
            code, content_type, body = fetch("/api/products")
            assert code == 200 and isinstance(json.loads(body), list), "JSON product API response invalid"
            print("PASS: health, dashboard, product catalog, JSON product API")
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
