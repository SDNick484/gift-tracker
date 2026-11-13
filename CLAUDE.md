# Gift Tracker: notes for Claude

A private gift planner for two owners: who each gift is for, who it is from, where it stands. Flask + SQLite,
plain ES-module frontend with no build step, gunicorn under OpenRC on an Alpine LXC, behind a Cloudflare Tunnel and
its own Cloudflare Access application. The design is `docs/superpowers/specs/2026-11-12-gift-tracker-design.md`;
the build order is `docs/superpowers/plans/2026-11-12-gift-tracker-core.md`. Do not edit either to make code fit.

## Rules that must keep holding

- Runtime dependencies must exist as Alpine `py3-*` packages: `py3-flask py3-gunicorn py3-jwt py3-cryptography`. Prefer the standard library.
- No frontend framework, bundler or npm step. Native ES modules in `static/js/`.
- No personal data in the repo: generic names (Alex, Sam, Jordan, Riley, Casey), `example.com` emails, RFC 5737 addresses. Household values live in git-ignored `deploy/deploy.env`. `tests/test_hygiene.py` enforces the emails, plus any strings in the git-ignored `tests/hygiene_denylist.txt`.
- Identity only from a verified `Cf-Access-Jwt-Assertion` whose email is in `ALLOWED_EMAILS`. No LAN role, ever. The one exception is development: `DEV_EMAIL` set, a loopback remote address, and none of `identity.CF_HEADERS` present. The installer never writes `DEV_EMAIL`.
- Every mutation needs header `X-Tracker: 1`, a JSON body, and a same-origin `Origin`.
- Read bodies with `json_body()` and strings with `text(data, key)`; bad shapes return 400. Never store non-finite numbers. Money is integer cents.
- Everything user-typed goes through `esc()` before `innerHTML`. CSP allows no inline scripts and images only from this origin.
- No gift title or person name in any `<title>`, the manifest, or a log line. Manifest name and page title are the `APP_NAME` setting, default `Ledger`.
- Schema changes go through idempotent `migrate()` and bump `SCHEMA_VERSION`. No manual DB step on the host.
- Family colors are data (`people.color`), not code.
- Commits: Conventional Commits, one logical change each, author `SDNick484 <SDNick484@users.noreply.github.com>`.
- Docs use OpenRC commands and say `vim`.
- Default port `8767`. Gunicorn binds `127.0.0.1` only.

## Changes together

- A new top-level Python module is added to `installer/build.sh`'s copy list (once that file exists).
- A new setting goes in `settings.py`, `.env.example` and the installer's settings list.
- A schema change bumps `db.SCHEMA_VERSION` and adds a step to `db.MIGRATIONS` (which `migrate()` runs).
- A new `/api/` GET route must pass `tests/test_privacy.py` (once that exists).
- A new route of any kind stays behind the identity guard: `tests/test_identity.py` walks `app.url_map` and expects 403 from the LAN everywhere but `/healthz`.

## File structure

```
app.py            Flask object, before/after hooks, every route
settings.py       environment config, read once at import
db.py             SCHEMA, SCHEMA_VERSION, MIGRATIONS, connect(), transaction(), migrate()
common.py         json_body, text, flag, whole, parse_cents, today, ApiError
identity.py       Cloudflare JWT verification, resolve_user()
lock.py           PIN hashing, unlock cookie, is_unlocked()
people.py         groups, people, households, seeded users (ensure_users runs at start-up)
occasions.py      occasions, per-person plan, carry-forward (copy_from), current()
gifts.py          gift CRUD, tracks (ours/theirs), status, Santa rule, history; visibility arrives with lock.py
budgets.py        spent/planned totals, occasion summary
boards.py         a protected person's board, assign, list text
static/           index.html, style.css, manifest.json, icon.svg
static/js/        ES modules
tests/            conftest.py + one test file per module
installer/        setup-head.sh, build.sh
deploy/           deploy.env.example
ops/              tracker-backup
docs/superpowers/ specs/, plans/
```

Not every file exists yet; the plan says which task creates each.

## How the code is put together

- `db.connect()` opens connections in autocommit mode (`isolation_level=None`) with foreign keys on and WAL. A single statement commits by itself; wrap anything that must be atomic in `with db.transaction(con):` (`BEGIN IMMEDIATE`).
- `app.db()` is the per-request connection in `flask.g`. `app.init_db()` runs `migrate()` at import, so every start migrates.
- `identity.resolve_user(headers, remote_addr)` returns `{"email", "name"}` or `None`. The `before_request` guard in `app.py` turns `None` into 403 `{"error": "Forbidden"}` on every path except `/healthz`, then applies the mutation guard to every `/api/` request that is not GET or HEAD.
- Raise `common.ApiError(status, message)` from routes and modules; it becomes `{"error": message}`.

## Tests

- `tests/conftest.py` sets the environment before importing `app`, and gives every test a fresh database (`client`, `con`).
- Cloudflare is simulated: `identity._jwk_client` is stubbed with a test RSA key. `auth(email)` returns Cloudflare headers with a signed JWT (keyword arguments `aud`, `iss`, `exp`, `key` forge bad ones); `mut(email)` adds `X-Tracker: 1`, a same-origin `Origin` and a JSON content type.
- The Flask test client's default remote address is `127.0.0.1`; `conftest.py` clears `DEV_EMAIL` so loopback is not admitted by accident. Use `environ_base={"REMOTE_ADDR": "192.0.2.50"}` for a LAN request.

## Verification gate

Run before every commit; all of it must pass:

```sh
python -m pytest -q && ruff check && for f in static/js/*.js; do node --check "$f"; done
```

(The `node --check` loop applies once `static/js` exists.) CI (`.github/workflows/ci.yml`) runs pytest on Python 3.12 and the latest Python, pytest on Alpine's `py3-*` packages, `ruff check`, and `node --check`.
