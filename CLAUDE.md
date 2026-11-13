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
- Privacy under the idle lock: a gift with any protected recipient is protected. While a request is locked
  (`app.unlocked()` is false) a protected gift does not exist as far as any response is concerned: `GET`, `PATCH`,
  `DELETE` and `/status` on it answer the same 404 as a missing gift; it is absent from every list, history, total,
  count and headline, including those of an unprotected co-recipient; a write that would give a gift a protected
  recipient, set Santa, unprotect a person or delete a person is 423 `{"error": "Locked", "locked": true}`. No locked
  response contains `santa`. Every query that can return gifts takes a required `unlocked` (or `include_protected`)
  and filters on `gifts.PROTECTED_SQL`; every route that takes one gift checks `gifts.visible()` first. A user
  with no PIN is always locked. `tests/test_privacy.py` walks `app.url_map` and enforces this for every `/api/` route.
- Read bodies with `json_body()` and strings with `text(data, key)`; bad shapes return 400. Never store non-finite numbers. Money is integer cents.
- Everything user-typed goes through `esc()` before `innerHTML`. CSP allows no inline scripts, no inline styles and images only from this origin. Per-person colors are set through the CSSOM (`data-color` plus `applyColors()`), never `style=""`. `tests/test_frontend.py` scans `static/js` for both.
- No gift title or person name in any `<title>`, the manifest, or a log line. Manifest name and page title are the `APP_NAME` setting, default `Ledger`.
- Schema changes go through idempotent `migrate()` and bump `SCHEMA_VERSION`. No manual DB step on the host.
- Family colors are data (`people.color`), not code.
- Commits: Conventional Commits, one logical change each, author `SDNick484 <SDNick484@users.noreply.github.com>`.
- Docs use OpenRC commands and say `vim`.
- Default port `8767`. Gunicorn binds `127.0.0.1` only.

## Changes together

- A new top-level Python module needs no installer edit: `installer/build.sh` ships every top-level `*.py` by glob, and `tests/test_installer_static.py` checks the built payload.
- A new setting goes in `settings.py`, `.env.example` and the installer's settings list.
- A schema change bumps `db.SCHEMA_VERSION` and adds a step to `db.MIGRATIONS` (which `migrate()` runs).
- A new `/api/` route must pass `tests/test_privacy.py`, which walks `app.url_map`: give a new path argument name
  a value in its `args()`. A route that shows or changes gifts filters on `gifts.PROTECTED_SQL` or checks
  `gifts.visible()`, and a board-like route takes `@unlock_required`.
- A new route of any kind stays behind the identity guard: `tests/test_identity.py` walks `app.url_map` and expects 403 from the LAN everywhere but `/healthz`.

## School mode (`MODE=school`)

A second instance of the same code (its own database, subdomain and Access policy): staff gifts, a `role` on each person, a `detail` and a `done` flag on each recipient of a gift, and the staff-by-gift matrix (`school.py`, `static/js/school.js`). The decisions are in `docs/superpowers/plans/2026-11-13-school-instance.md`.

- Ask `common.is_school()`, never a copy of `settings.MODE` taken at import: tests switch the mode with `monkeypatch.setattr(settings, "MODE", "school")`.
- Family mode must not change: a family response gains no key (`mode` on `/api/me` and `role` on a person exist in school mode only), and `role` in a request body is ignored there.
- A route that exists in one mode only is wrapped in `family_only` or `school_only` (under the route decorator): the other mode answers the generic 404 `{"error": "Not Found"}`. Lock, board, assign, the four location routes and `/lists/where` are family-only; the four matrix and cell routes are school-only. `tests/test_school.py` pins both lists (`FAMILY_ONLY`, `SCHOOL_ONLY`), so a new mode-specific route is added there.
- School mode has no lock: `app.unlocked()` is true, so the lock's filtering hides nothing. What must not appear is removed at the edges: `gift_json` drops `santa` and `refuse_locked_write` answers 400 when a body names it; `people._person` reports `protected: false` and setting it true is 400. A response built any other way must go through those.
- `gifts.update` changes the recipient set; it must never delete and re-insert `gift_recipients` rows, or every edit of a group gift erases its details.
- The installer's names and paths come from the block between `# instance-begin` and `# instance-end`, and the service script is the template between `# initd-begin` and `# initd-end`. Nothing else may hard-code `/opt/tracker` or the service name; `tests/test_installer_static.py` pins the default install's names, paths, port and rendered service script.

## After purchase (order fields, hiding places, lists)

Decisions in `docs/superpowers/plans/2026-11-13-after-purchase.md`.

- Order fields (`seller, order_number, ship_to_id, ship_elsewhere_ok, eta, arrived_on`) are written by `orders.update` (or, with a status, `gifts.set_status` / `gifts.update`); a route must not write them inline. The agent API calls the same functions.
- Marking a gift ordered needs seller, ship-to and eta (`orders.require_ordered`), through `POST /status` and `PATCH` alike. `POST /api/gifts` does not enforce it. `ship_warning` is computed on read, never stored.
- Exactly one address is home (a partial unique index plus the seeded `Home` row). A location is never its own ancestor (`locations.update` refuses it).
- Every list and count in `lists.py` filters on `gifts.PROTECTED_SQL`. The location tree and the address list name no gift. Deleting an address or a location answers 409 when a gift uses it, so both deletes require the unlock (a locked caller could otherwise learn that a hidden gift uses it).
- School mode has no hiding places: gift JSON drops `location_id` and `location_path`, a body naming `location_id` is 400, the lists drop the location keys. Order fields, addresses, `/lists/pending`, `/lists/to-wrap` and `/lists/counts` work in both modes.

## File structure

```
app.py            Flask object, before/after hooks, every route
settings.py       environment config, read once at import
db.py             SCHEMA, SCHEMA_VERSION, MIGRATIONS, connect(), transaction(), migrate()
common.py         json_body, text, flag, whole, parse_cents, today, now, ApiError, locked
identity.py       Cloudflare JWT verification, resolve_user()
lock.py           PIN hashing, unlock cookie, is_unlocked()
people.py         groups, people, households, seeded users (ensure_users runs at start-up)
occasions.py      occasions, per-person plan, carry-forward (copy_from), current()
gifts.py          gift CRUD, tracks (ours/theirs), status, Santa rule, history, visibility (PROTECTED_SQL)
budgets.py        spent/planned totals, occasion summary
boards.py         a protected person's board, assign, list text
school.py         school mode: the staff-by-gift matrix, per-recipient detail and done
orders.py         order fields, the ship-to check, the one writer (update), ship_warning
addresses.py      ship-to addresses, exactly one home
locations.py      the hiding-place tree, the ancestor rule, set_gift_location
lists.py          Not here yet, To wrap, Where is it?, and the home counts
static/           index.html (GET / fills in APP_NAME), style.css, manifest.json (GET /manifest.json adds the name), icon.svg
static/js/        ES modules: main (router), core, api, lock and one module per screen
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
- Raise `common.ApiError(status, message)` from routes and modules; it becomes `{"error": message}` (extra keyword
  arguments become extra keys). `common.locked()` is the 423 the UI turns into the PIN pad.
- The idle lock (`lock.py`): `app.unlocked()` reads the `gt_unlock` cookie once per request; `slide_unlock`
  re-issues it on every unlocked response and clears it otherwise. `lock.load_secret()` runs in `init_db()`.
- "Now" is `common.now()` (seconds) and "today" is `common.today()`; tests move them with `clock` and `freeze_today`.

## Tests

- `tests/conftest.py` sets the environment before importing `app`, and gives every test a fresh database (`client`, `con`).
- Cloudflare is simulated: `identity._jwk_client` is stubbed with a test RSA key. `auth(email)` returns Cloudflare headers with a signed JWT (keyword arguments `aud`, `iss`, `exp`, `key` forge bad ones); `mut(email)` adds `X-Tracker: 1`, a same-origin `Origin` and a JSON content type.
- Every test client starts locked. `unlock()` sets a PIN if needed and unlocks that client; `mk_protected_gift(ids, ...)`
  inserts through `gifts.create`, past the lock. `ids` gives protected `kid1` to `kid3`.
- The Flask test client's default remote address is `127.0.0.1`; `conftest.py` clears `DEV_EMAIL` so loopback is not admitted by accident. Use `environ_base={"REMOTE_ADDR": "192.0.2.50"}` for a LAN request.

## Verification gate

Run before every commit; all of it must pass:

```sh
python -m pytest -q && ruff check && for f in static/js/*.js; do node --check "$f"; done
node --test tests/frontend.test.mjs
shellcheck -s sh installer/setup-head.sh installer/build.sh ops/tracker-backup
```

CI (`.github/workflows/ci.yml`) runs pytest on Python 3.12 and the latest Python, pytest on Alpine's `py3-*` packages, `ruff check`, `node --check`, `node --test tests/frontend.test.mjs` and `shellcheck -s sh`. The backup tests need the `sqlite3` CLI and skip without it; the Alpine job installs it.
