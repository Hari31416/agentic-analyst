# Local JWT authentication

The API uses local username/password accounts and HS256 JWT access tokens.
The initial admin is seeded from the environment on API startup. Admins create
other `admin` or `user` accounts through **Users** in the top bar. There is no
public registration, identity-provider service, refresh token or SSO dependency.

All signed-in users share the existing workspaces, sources, database connections,
threads, runs, artifacts and exports. Both roles can use and edit that shared
workspace data, including deletion. Only admins can list, create, change roles,
disable accounts or reset passwords. This is an application login boundary, not
per-user ownership or department permissions.

## Configuration and first start

After `make setup`, configure these values in the ignored `.env`:

```dotenv
JWT_SECRET_KEY=<random secret of at least 32 bytes>
JWT_ACCESS_TOKEN_EXPIRE_MINUTES=60
ADMIN_USERNAME=admin
ADMIN_PASSWORD=<your initial password>
AUTH_COOKIE_SECURE=false
AUTH_ALLOWED_ORIGINS='["http://localhost:5173","http://127.0.0.1:5173"]'
```

Generate the signing key with `uv run python -c 'import secrets;
print(secrets.token_urlsafe(48))'` from `backend/`, then store it in `.env`.
Usernames contain 3–50 ASCII letters, digits, dots, underscores or hyphens and
are normalized to lowercase. New passwords contain 8–128 characters and are
stored as salted Argon2id hashes. No password is committed or returned by account
inspection. Choose a unique initial password; there is no built-in default.

Run `make up`, `make migrate`, then `make dev` or `make start`. Migration
`d37e41b5a002` adds accounts and revoked JWT IDs; existing workspace data remains.
Open the frontend and sign in using the environment credentials. The initial
admin is created only when the users table is empty. Concurrent API starts use a
PostgreSQL transaction advisory lock to prevent duplicate seeding. Startup fails
if the signing key is missing, or bootstrap credentials are missing/invalid on an
empty users table. Restarting, changing bootstrap environment values, or disabling
an account never resets or recreates an existing admin.

Use **Users** to create accounts, select their role, reset passwords, or
activate/deactivate them. User lists are paginated. The last active admin cannot
be disabled or demoted. Accounts are disabled instead of deleted; existing
workspace data is shared and retained. Any account update invalidates its earlier
JWTs, including the current session if an admin edits their own account. Sign in
again after such an update. Passwords set by admins are ordinary passwords, not
one-time or automatically expiring credentials.

## Browser sessions and API clients

`POST /api/auth/login` accepts JSON `{ "username": "...", "password": "..." }`.
It returns `access_token`, `token_type`, `expires_in`, and a safe user view.
It also sets the JWT in an HttpOnly, SameSite=Strict cookie scoped to `/api`.
The frontend never persists tokens in browser storage or adds them to URLs.
Same-origin cookies authenticate EventSource streams and direct file downloads.
The Vite `/api` proxy preserves this same-origin arrangement in development.

`GET /api/auth/me` returns the current account. `POST /api/auth/logout` revokes
that JWT until expiry and clears its cookie; other sessions stay signed in.
Account status, token version and current role are checked against PostgreSQL on
each authenticated request. JWTs require signature, issuer, audience, expiry,
subject, issued/not-before times, token type and ID. HS256 is fixed by the server.
The signing key is deployment-wide and must be the same across API processes.
Rotating it invalidates all existing JWTs. There is no automatic refresh.

Non-browser clients send `Authorization: Bearer <access_token>` on each request.
An explicit malformed Authorization header never falls back to cookie auth.
Cookie-authenticated mutations require an allowed `Origin`; login rejects
unapproved origins too. Bearer clients do not need an Origin header. Configure
exact frontend origins with no paths, trailing slash or wildcards. Add your
actual development origin if you change the frontend port.

For HTTPS deployments set `AUTH_COOKIE_SECURE=true`, set your HTTPS frontend
origin in `AUTH_ALLOWED_ORIGINS`, and route frontend and `/api` through the same
origin. Keep PostgreSQL, RustFS and sandbox endpoints on private interfaces;
the application's JWT does not authenticate those separate infrastructure APIs.

All business API routes require authentication, including readiness, uploads,
connections, voice, run events, audit, exports, downloads and deletion. Liveness
`GET /api/health` and `POST /api/auth/login` are public. OpenAPI/docs remain public
metadata; they do not grant access to application resources. Open event streams
close at expiry and recheck account changes/logout revocation within five seconds.
Already authorized short requests can finish, and logout does not cancel a queued
or running analysis. Expired stream reconnects return 401; the frontend returns
to the login screen. Failed logout stays visible for retry instead of claiming
that a still-valid session was revoked.

Login admission allows ten attempts per minute per direct client IP per API
process, including successful logins. The map is bounded to 1,024 active IPs and
expired entries are removed. This is a local baseline, not a distributed
rate-limit service. Requests through the Vite proxy share its backend address;
multiple API processes have separate limits.

## Evaluation and validation

Create a regular evaluation account, then set `EVAL_USERNAME` and `EVAL_PASSWORD`
in `.env` before `make eval-live`. The runner logs in through the public API and
uses its bearer token without writing it to checkpoints or reports. Its password
remains a secret setting. Long experiments exceeding the token lifetime need an
appropriate bounded lifetime or a fresh authenticated process; the runner does
not automatically replay requests or refresh credentials.

Auth tests exercise the actual gate without the feature-test dependency override.
They cover anonymous access to every business route, admin/user roles, duplicate
usernames, safe validation errors, CSRF origins, malformed/expired/foreign JWTs,
logout, immediate account invalidation, stream rechecks, protected downloads,
seeding and the last-admin rule. PostgreSQL tests use isolated schemas to verify
parallel seeding, real account APIs and upgrade/downgrade/reapply behavior while
preserving workspace records. Existing feature tests override auth explicitly so
they continue testing their own contracts. Full browser QA remains deferred.
