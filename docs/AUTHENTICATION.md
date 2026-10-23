# Authentication and authorization (Group 2)

## Architecture
- Users are stored in SQLite (`users` table); the password hash is never sent to clients.
- Passwords are hashed and verified using Gungnir's Application SDK (OpenSSL scrypt backend).
- `SessionGuard` calls SQLite-backed credential and identity resolvers.
- Middleware order: security headers / body bounds → session → CSRF → identity restoration → guard.
- Login rotates the session identifier; logout invalidates the session.
- Anonymous catalog/dashboard requests return 401, not private inventory data.
- Role rules: `viewer` reads inventory; `manager` can access management status; `admin` can access both management and admin status. Actual write endpoints remain disabled.
- Invalid/deactivated users are refused on the next request (identity re-resolved from SQLite).
- The password is never stored in an identity or logged.

## Local account creation
Install the **Application SDK v1.0.2**, create `.env`, and run `gungnir migrate` first.

```sh
cmake -S tools -B build/provision -DCMAKE_PREFIX_PATH=/path/to/gungnir-application
cmake --build build/provision --config Release
python3 scripts/provision_user.py --role admin
```

On Windows, use `python` instead of `python3` if required. The script securely prompts for a password and passes it to the native tool via stdin, not as a command-line argument. Repeat with `--role manager` or `--role viewer` to create additional accounts. Local OS/database access is trusted; this is **not** a public registration API. Keep accounts and their passwords private.

`GET /login` provides the sign-in form and CSRF token. `POST /login` uses Gungnir's credential guard, `POST /logout` logs out, and `GET /api/me` returns only public identity fields. There is no remember-me or self-service registration.

## Security limitations / production blockers
1. The initial session backend is in-memory; sessions disappear on restart and do not work across replicas. Replace it with persistent sessions before production.
2. Local HTTP sets a non-Secure session cookie only in development. Deploy behind correctly configured HTTPS, with Secure cookies, host/proxy validation and a managed session store.
3. The login throttle is process-local. Use shared rate limiting for multi-instance production deployments.
4. Access checks are demonstrated on route groups; Group 3+ must enforce authorization at the write operation boundary as well.
5. No reset password, invitations, account lockout, audit logging or MFA yet. Complete a threat-model/security review before public exposure.
6. `csrf` applies to every unsafe method; clients must fetch a session-bound token first. Tokens rotate after authentication transitions.
7. No untrusted account provisioning from the network. Run the tool only as a trusted host operator.

## Tests
CI creates two nonproduction fixture accounts, asserts session fixation protection, rejects missing/invalid CSRF tokens and wrong credentials, checks viewer/admin access separation and logout invalidation. Gungnir native compilation and SQLite migrations are required for acceptance.
