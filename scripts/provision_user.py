"""Provision a user through the native Gungnir password hasher and SQLite connection."""
import argparse
import getpass
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def provision(name: str, email: str, role: str, password: str):
    candidates = [
        ROOT / "build" / "provision" / "inventory-provision-user",
        ROOT / "build" / "provision" / "Debug" / "inventory-provision-user.exe",
        ROOT / "build" / "provision" / "Release" / "inventory-provision-user.exe",
    ]
    executable = next((path for path in candidates if path.is_file()), None)
    if executable is None:
        raise SystemExit("Build the tool first: cmake -S tools -B build/provision && cmake --build build/provision")
    if "\n" in name + email + role + password or "\r" in name + email + role + password:
        raise SystemExit("User input must not contain newlines")
    result = subprocess.run(
        [str(executable)], input=f"{name}\n{email}\n{role}\n{password}\n",
        text=True, cwd=ROOT, capture_output=True, check=False,
    )
    if result.stdout:
        print(result.stdout.rstrip())
    if result.returncode:
        print(result.stderr.rstrip(), file=sys.stderr)
        raise SystemExit(result.returncode)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--role", choices=("admin", "manager", "viewer"), default="admin")
    parser.add_argument("--ci-test-fixture", action="store_true",
                        help="Only inside GitHub Actions: seed non-production test accounts")
    args = parser.parse_args()
    if args.ci_test_fixture:
        if os.environ.get("GITHUB_ACTIONS") != "true":
            raise SystemExit("Test-fixture provisioning is restricted to GitHub Actions")
        name = "Inventory CI " + args.role.title()
        email = f"{args.role}@inventory.test"
        password = "CI-Only-Inventory-Passphrase-12345!"
    else:
        name = input("Full name: ").strip()
        email = input("Email: ").strip().lower()
        password = getpass.getpass("Password (12-128 characters): ")
        confirmation = getpass.getpass("Confirm password: ")
        if password != confirmation:
            raise SystemExit("Passwords do not match")
    provision(name, email, args.role, password)


if __name__ == "__main__":
    main()
