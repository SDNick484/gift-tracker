# Gift Tracker Core (Plan 1 of 6) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A deployable, private gift tracker the two owners can use by hand: people, occasions, gifts with several recipients and givers, status, Santa, budgets, the idle lock, and the girls' assignment boards.

**Architecture:** A new standalone Flask + SQLite app in its own repo (`gift-tracker`). `app.py` holds the Flask object, hooks and routes; route-free logic lives in sibling modules that take a `sqlite3.Connection`. The frontend is native ES modules with no build step. Identity comes only from a verified Cloudflare Access JWT checked against an allowlist; there is no LAN role.

**Tech Stack:** Python 3.12, Flask 3, gunicorn, PyJWT (crypto), SQLite (WAL), pytest, plain HTML/CSS/JS (ES modules), Alpine + OpenRC installer.

**Spec:** `docs/superpowers/specs/2026-11-12-gift-tracker-design.md`.

## The six plans

| Plan | Covers (spec build stage) | Depends on |
| --- | --- | --- |
| **1. Core (this plan)** | Core, privacy, girls' boards (1 to 3) | nothing |
| 2. School mode | School instance (4) | 1 |
| 3. After purchase | Order fields, ship-to check, locations, three lists (5) | 1 |
| 4. Wishlist link | Feed endpoint in `family-wishlist`, write-back (6) | 1 |
| 5. Agent API and MCP | Scoped tokens, REST `/api/v1`, MCP server (7) | 1, 3 |
| 6. Exchanges and import | Exchanges, 2024/2025 sheet import (8, 9) | 1 |

Each plan ships working software. Plans 2, 3, 4 and 6 are independent of each other.

## Global Constraints

- Runtime dependencies must exist as Alpine `py3-*` packages: `py3-flask py3-gunicorn py3-jwt py3-cryptography`. Prefer the standard library.
- No frontend framework, bundler or npm step. Native ES modules in `static/js/`.
- No personal data in the repo: generic names (Alex, Sam, Jordan, Riley), `example.com` emails, RFC 5737 addresses. Household values live in git-ignored `deploy/deploy.env`.
- Identity only from a verified `Cf-Access-Jwt-Assertion` whose email is in `ALLOWED_EMAILS`. No LAN role, ever.
- Every mutation needs header `X-Tracker: 1`, a JSON body, and a same-origin `Origin`.
- Read bodies with `json_body()` and strings with `text(data, key)`; bad shapes return 400. Never store non-finite numbers. Money is integer cents.
- Everything user-typed goes through `esc()` before `innerHTML`. CSP allows no inline scripts and images only from this origin.
- No gift title or person name in any `<title>`, the manifest, or a log line. Manifest name and page title are the `APP_NAME` setting, default `Ledger`.
- Schema changes go through idempotent `migrate()` and bump `SCHEMA_VERSION`. No manual DB step on the host.
- Family colors are data (`people.color`), not code.
- Commits: Conventional Commits, one logical change each, author `SDNick484 <SDNick484@users.noreply.github.com>`.
- Docs use OpenRC commands and say `vim`.
- Default port `8767`. Gunicorn binds `127.0.0.1` only.

## Review Focus

1. A request with no Cloudflare headers from a LAN address gets 403 on every route except `/healthz`; it is never given a default user. (Task 2)
2. Both owners assign the same unassigned gift to different givers at the same moment: the second gets 409 and the first assignment stands. (Task 8)
3. A gift shared between a protected and an unprotected recipient is hidden entirely while locked, from every endpoint including the unprotected person's list and budget totals. (Task 7)
4. Price typed as `$29.99`, `29`, `1,299.00`, empty, `abc`, `-5` or `NaN`: stored as cents or `null`, or rejected with 400; never a float or non-finite value. (Task 5)
5. Deleting a person who has gifts is refused with 409; deactivating keeps their gifts and history and drops them from new occasions. (Task 3)

## File structure

```
app.py            Flask object, before/after hooks, every route
settings.py       environment config, read once at import
db.py             SCHEMA, SCHEMA_VERSION, connect(), migrate()
common.py         json_body, text, parse_cents, ApiError
identity.py       Cloudflare JWT verification, resolve_user()
lock.py           PIN hashing, unlock cookie, is_unlocked()
people.py         groups, people, households, users
occasions.py      occasions, per-person plan, carry-forward
gifts.py          gift CRUD, tracks, status, Santa rule, visibility
budgets.py        spent/planned totals, occasion summary
boards.py         a protected person's board, assign, list text
static/           index.html, style.css, manifest.json, icon.svg
static/js/        main.js core.js api.js lock.js home.js idea.js person.js gift.js board.js settings.js package.json
tests/            conftest.py + one test file per module
installer/        setup-head.sh, build.sh
deploy/           deploy.env.example
ops/              tracker-backup
docs/superpowers/ specs/, plans/
CLAUDE.md README.md requirements.txt requirements-dev.txt ruff.toml .github/workflows/ci.yml
```

---

### Task 1: Repository scaffold, settings and schema

**Files:**
- Create: `settings.py`, `db.py`, `app.py`, `requirements.txt`, `requirements-dev.txt`, `ruff.toml`, `.gitignore`, `CLAUDE.md`, `README.md`, `.github/workflows/ci.yml`
- Test: `tests/conftest.py`, `tests/test_db.py`, `tests/test_hygiene.py`

**Interfaces:**
- Produces: `settings.DATA_DIR, DB_PATH, PORT, ALLOWED_EMAILS: set[str], CF_TEAM_DOMAIN, CF_AUD, DEV_EMAIL, APP_NAME, LOCK_IDLE_SECONDS: int, USERS: dict[str, str]` (email → display name, from `USERS="a@example.com=Alex,b@example.com=Sam"`)
- Produces: `db.SCHEMA_VERSION = 1`, `db.connect(path: str) -> sqlite3.Connection` (Row factory, `foreign_keys=ON`, WAL), `db.migrate(con) -> None`
- Produces: `app.app` (Flask), `app.db() -> sqlite3.Connection` (per-request, in `flask.g`), `GET /healthz -> {"ok": true}`
- Produces: test fixtures `client` (Flask test client, fresh database per test) and `con`

Schema v1:

```sql
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE groups (id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE, sort_order INTEGER NOT NULL DEFAULT 0);
CREATE TABLE people (
  id INTEGER PRIMARY KEY, name TEXT NOT NULL, group_id INTEGER REFERENCES groups(id) ON DELETE SET NULL,
  household TEXT, color TEXT, protected INTEGER NOT NULL DEFAULT 0, active INTEGER NOT NULL DEFAULT 1,
  wishlist_person_id INTEGER, notes TEXT, sort_order INTEGER NOT NULL DEFAULT 0);
CREATE TABLE users (
  email TEXT PRIMARY KEY, person_id INTEGER NOT NULL REFERENCES people(id),
  pin_hash BLOB, pin_salt BLOB, pin_fails INTEGER NOT NULL DEFAULT 0, pin_locked_until INTEGER NOT NULL DEFAULT 0);
CREATE TABLE occasions (
  id INTEGER PRIMARY KEY, name TEXT NOT NULL, kind TEXT NOT NULL CHECK (kind IN ('christmas','birthday','graduation','other')),
  date TEXT NOT NULL, archived INTEGER NOT NULL DEFAULT 0);
CREATE TABLE occasion_people (
  occasion_id INTEGER NOT NULL REFERENCES occasions(id) ON DELETE CASCADE,
  person_id INTEGER NOT NULL REFERENCES people(id) ON DELETE CASCADE,
  plan TEXT NOT NULL DEFAULT 'individual' CHECK (plan IN ('individual','joint','exchange','skip')),
  budget_cents INTEGER, PRIMARY KEY (occasion_id, person_id));
CREATE TABLE gifts (
  id INTEGER PRIMARY KEY, occasion_id INTEGER NOT NULL REFERENCES occasions(id) ON DELETE CASCADE,
  title TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('idea','decided','ordered','arrived','wrapped','given','told','confirmed')),
  santa INTEGER NOT NULL DEFAULT 0, url TEXT, store TEXT, price_cents INTEGER, notes TEXT,
  wishlist_item_id INTEGER, told_at TEXT, created_by TEXT NOT NULL, created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL);
CREATE TABLE gift_recipients (gift_id INTEGER NOT NULL REFERENCES gifts(id) ON DELETE CASCADE,
  person_id INTEGER NOT NULL REFERENCES people(id), PRIMARY KEY (gift_id, person_id));
CREATE TABLE gift_givers (gift_id INTEGER NOT NULL REFERENCES gifts(id) ON DELETE CASCADE,
  person_id INTEGER NOT NULL REFERENCES people(id), PRIMARY KEY (gift_id, person_id));
CREATE INDEX gifts_occasion ON gifts(occasion_id);
CREATE INDEX gift_recipients_person ON gift_recipients(person_id);
```

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_db.py
def test_migrate_creates_every_table(con):
    names = {r["name"] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"meta","groups","people","users","occasions","occasion_people","gifts","gift_recipients","gift_givers"} <= names
    assert con.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()[0] == "1"

def test_migrate_is_idempotent(con):
    db.migrate(con); db.migrate(con)
    assert con.execute("SELECT COUNT(*) FROM meta WHERE key='schema_version'").fetchone()[0] == 1

def test_foreign_keys_enforced(con):
    with pytest.raises(sqlite3.IntegrityError):
        con.execute("INSERT INTO gifts(occasion_id,title,status,created_by,created_at,updated_at) VALUES (999,'x','idea','a',0,0)")

def test_healthz_is_open_and_bare(client):
    r = client.get("/healthz")
    assert r.status_code == 200 and r.get_json() == {"ok": True}

# tests/test_hygiene.py: walk tracked files; fail on any email not @example.com
# (noreply.github.com exempt), and on the strings in tests/hygiene_denylist.txt (git-ignored, optional).
```

- [ ] **Step 2: Run `python -m pytest -q`; expect import errors.**
- [ ] **Step 3: Implement `settings.py`, `db.py` and the `app.py` skeleton.** `tests/conftest.py` sets `DATA_DIR` to a temp dir, `ALLOWED_EMAILS="alex@example.com,sam@example.com"`, `USERS="alex@example.com=Alex,sam@example.com=Sam"`, `CF_TEAM_DOMAIN="family.cloudflareaccess.com"`, `CF_AUD="aud-test"` before importing `app`, and recreates the database per test.
- [ ] **Step 4: Write `CLAUDE.md`** with the Global Constraints above as "Rules that must keep holding", the file structure, and the commands `python -m pytest -q`, `for f in static/js/*.js; do node --check "$f"; done`, `ruff check`. The spec is already in the repo; do not edit it.
- [ ] **Step 5: Add CI** (`ci.yml`): pytest on Python 3.12 and latest, an Alpine container job installing only `py3-*` packages, `ruff check`, `node --check` on `static/js/*.js`.
- [ ] **Step 6: Run `python -m pytest -q`; expect 5 passed.**
- [ ] **Step 7: Commit** `chore: scaffold app, settings and schema v1`

### Task 2: Identity and request guards

**Files:**
- Create: `identity.py`, `common.py`
- Modify: `app.py`
- Test: `tests/test_identity.py`

**Interfaces:**
- Produces: `identity.resolve_user(headers, remote_addr: str) -> dict | None` returning `{"email": str, "name": str}` or `None`
- Produces: `common.ApiError(status: int, message: str)`, `common.json_body() -> dict`, `common.text(data: dict, key: str) -> str`
- Produces: `flask.g.user` on every request except `/healthz` and `/static/`; `app.token(email, ...)` is not exported, tests build JWTs in `conftest.py` as the wishlist's tests do (stub `identity._jwk_client`)
- Produces: test helper `auth(email="alex@example.com") -> dict` returning Cloudflare headers with a signed JWT, and `mut(email=...)` adding `X-Tracker: 1`, `Origin` and JSON content type

Rules: a request is authenticated only if it carries a valid JWT (signature via the team JWKS, audience `CF_AUD`, issuer `https://<CF_TEAM_DOMAIN>`, unexpired) and its email, lowercased, is in `ALLOWED_EMAILS`. One development exception: `DEV_EMAIL` is set, the remote address is loopback, and no header in `CF_HEADERS = ("Cf-Ray","Cf-Connecting-Ip","Cf-Access-Jwt-Assertion","Cf-Access-Authenticated-User-Email")` is present. Everything else is 403 with body `{"error": "Forbidden"}`.

- [ ] **Step 1: Write the failing tests**

```python
def test_lan_request_without_cloudflare_headers_is_forbidden(client):
    for path in ("/", "/api/me", "/api/people"):
        r = client.get(path, environ_base={"REMOTE_ADDR": "192.0.2.50"})
        assert r.status_code == 403, path

def test_forged_email_header_alone_is_forbidden(client):
    r = client.get("/api/me", headers={"Cf-Access-Authenticated-User-Email": "alex@example.com"})
    assert r.status_code == 403

def test_valid_jwt_for_allowed_email(client):
    r = client.get("/api/me", headers=auth("alex@example.com"))
    assert r.status_code == 200 and r.get_json()["email"] == "alex@example.com"

def test_valid_jwt_for_other_email_is_forbidden(client):
    assert client.get("/api/me", headers=auth("jordan@example.com")).status_code == 403

@pytest.mark.parametrize("kw", [{"aud": "other"}, {"iss": "https://evil.example.com"}, {"exp": -10}, {"key": OTHER_KEY}])
def test_bad_tokens_are_forbidden(client, kw):
    assert client.get("/api/me", headers=auth("alex@example.com", **kw)).status_code == 403

def test_dev_email_only_from_loopback_without_cf_headers(client, monkeypatch):
    monkeypatch.setattr(identity, "DEV_EMAIL", "alex@example.com")
    assert client.get("/api/me", environ_base={"REMOTE_ADDR": "127.0.0.1"}).status_code == 200
    assert client.get("/api/me", environ_base={"REMOTE_ADDR": "192.0.2.50"}).status_code == 403
    assert client.get("/api/me", environ_base={"REMOTE_ADDR": "127.0.0.1"}, headers={"Cf-Ray": "x"}).status_code == 403

def test_mutation_needs_header_json_and_origin(client):
    h = auth("alex@example.com")
    assert client.post("/api/groups", json={"name": "A"}, headers=h).status_code == 400            # no X-Tracker
    assert client.post("/api/groups", data="name=A", headers={**h, "X-Tracker": "1"}).status_code == 400
    assert client.post("/api/groups", json={"name": "A"},
                       headers={**h, "X-Tracker": "1", "Origin": "https://evil.example.com"}).status_code == 403

def test_security_headers(client):
    r = client.get("/api/me", headers=auth())
    assert "script-src 'self'" in r.headers["Content-Security-Policy"]
    assert r.headers["Cache-Control"] == "no-store"
    assert r.headers["Referrer-Policy"] == "no-referrer"
```

- [ ] **Step 2: Run `python -m pytest -q tests/test_identity.py`; expect failures.**
- [ ] **Step 3: Implement.** Port `_verify_cf_token` and the JWKS client from `family-wishlist/app.py`. `GET /api/me` returns `{"email","name","app_name"}` for now. Add the `before_request` guard, the mutation guard for every non-GET `/api/` request, and `after_request` security headers. `POST /api/groups` may be a stub returning 201 until Task 3.
- [ ] **Step 4: Run the tests; expect all passed.**
- [ ] **Step 5: Commit** `feat(auth): Cloudflare identity, allowlist, no LAN role, CSRF guard`

### Task 3: Groups, people, households and users

**Files:**
- Create: `people.py`
- Modify: `app.py`
- Test: `tests/test_people.py`

**Interfaces:**
- Produces: `people.ensure_users(con, users: dict[str, str]) -> None` (start-up: for each email creates a person, in group "Us", household "Us", and a `users` row, if missing)
- Produces: `people.user_person_ids(con) -> set[int]`
- Produces: `people.list_people(con, include_inactive=False) -> list[dict]` with keys `id, name, group_id, household, color, protected, active, sort_order, is_user`
- Produces: `people.households(con) -> list[dict]` → `[{"label": str, "person_ids": [int]}]`; a person with no household is their own entry labelled by name
- Produces routes: `GET/POST /api/groups`, `PATCH/DELETE /api/groups/<id>`, `GET/POST /api/people`, `PATCH/DELETE /api/people/<id>`, `GET /api/households`

Rules: `color` must match `^#[0-9a-fA-F]{6}$` or be null. `DELETE /api/people/<id>` returns 409 `{"error": "This person has gifts. Deactivate them instead."}` when any `gift_recipients` or `gift_givers` row references them, and 409 for a user's own person. `protected` people are the ones behind the lock (Task 7).

- [ ] **Step 1: Write the failing tests**

```python
def test_users_are_seeded_as_people_in_household_us(client):
    ppl = client.get("/api/people", headers=auth()).get_json()
    assert [p["name"] for p in ppl if p["is_user"]] == ["Alex", "Sam"]
    assert {p["household"] for p in ppl if p["is_user"]} == {"Us"}

def test_households_group_by_label(client):
    mk_person(client, "Jordan", household="Jordan & Riley"); mk_person(client, "Riley", household="Jordan & Riley")
    mk_person(client, "Casey")
    labels = [h["label"] for h in client.get("/api/households", headers=auth()).get_json()]
    assert labels == ["Us", "Jordan & Riley", "Casey"]

def test_bad_color_is_400(client):
    assert client.post("/api/people", json={"name": "X", "color": "red"}, headers=mut()).status_code == 400

def test_delete_person_with_gifts_is_409_and_deactivate_keeps_history(client):
    pid = mk_person(client, "Jordan"); oid = mk_occasion(client); mk_gift(client, oid, "Book", to=[pid])
    assert client.delete(f"/api/people/{pid}", headers=mut()).status_code == 409
    assert client.patch(f"/api/people/{pid}", json={"active": False}, headers=mut()).status_code == 200
    assert pid not in [p["id"] for p in client.get("/api/people", headers=auth()).get_json()]
    assert client.get(f"/api/people/{pid}/history", headers=auth()).get_json()["gifts"][0]["title"] == "Book"
```

(`mk_person`, `mk_occasion`, `mk_gift` are `conftest.py` helpers that call the API; the last assertion passes once Tasks 4 and 5 land, so mark that test `xfail(strict=True)` here and remove the mark in Task 5.)

- [ ] **Step 2: Run; expect failures.**
- [ ] **Step 3: Implement `people.py` and the routes; call `ensure_users` after `migrate` at start-up.**
- [ ] **Step 4: Run; expect passed plus one xfailed.**
- [ ] **Step 5: Commit** `feat(people): groups, people, households and seeded users`

### Task 4: Occasions and the per-person plan

**Files:**
- Create: `occasions.py`
- Modify: `app.py`
- Test: `tests/test_occasions.py`

**Interfaces:**
- Produces: `occasions.create(con, name: str, kind: str, date: str, copy_from: int | None) -> int`
- Produces: `occasions.set_plan(con, occasion_id: int, person_id: int, plan: str, budget_cents: int | None) -> None`
- Produces: `occasions.current(con) -> sqlite3.Row | None` (the unarchived occasion with the nearest date on or after today, else the latest)
- Produces routes: `GET /api/occasions`, `POST /api/occasions` `{name, kind, date, copy_from?}`, `PATCH /api/occasions/<id>` `{name?, date?, archived?}`, `PUT /api/occasions/<oid>/people/<pid>` `{plan, budget_cents}`, `DELETE /api/occasions/<oid>/people/<pid>`

Rules: `date` is `YYYY-MM-DD`. `copy_from` copies `occasion_people` rows (plan and budget) for people who are still active; it copies no gifts. A new occasion with no `copy_from` has no people.

- [ ] **Step 1: Write the failing tests**

```python
def test_copy_from_carries_plans_for_active_people_only(client):
    a, b = mk_person(client, "Jordan"), mk_person(client, "Riley")
    o1 = mk_occasion(client, "Christmas 2025", "christmas", "2025-12-25")
    put_plan(client, o1, a, "joint", 7500); put_plan(client, o1, b, "exchange", None)
    client.patch(f"/api/people/{b}", json={"active": False}, headers=mut())
    o2 = client.post("/api/occasions", json={"name": "Christmas 2026", "kind": "christmas", "date": "2026-12-25",
                                             "copy_from": o1}, headers=mut()).get_json()["id"]
    rows = con_rows("SELECT person_id, plan, budget_cents FROM occasion_people WHERE occasion_id=?", o2)
    assert rows == [(a, "joint", 7500)]

@pytest.mark.parametrize("body", [{"name": "", "kind": "christmas", "date": "2026-12-25"},
                                  {"name": "X", "kind": "party", "date": "2026-12-25"},
                                  {"name": "X", "kind": "other", "date": "12/25/2026"}])
def test_bad_occasion_is_400(client, body):
    assert client.post("/api/occasions", json=body, headers=mut()).status_code == 400

def test_current_prefers_next_upcoming(con, freeze_today):
    freeze_today("2026-11-12")
    occasions.create(con, "Christmas 2025", "christmas", "2025-12-25", None)
    nxt = occasions.create(con, "Christmas 2026", "christmas", "2026-12-25", None)
    occasions.create(con, "Birthday 2027", "birthday", "2027-03-01", None)
    assert occasions.current(con)["id"] == nxt
```

- [ ] **Step 2: Run; expect failures.**
- [ ] **Step 3: Implement.** `freeze_today` monkeypatches `common.today() -> str`, which every module uses for "today".
- [ ] **Step 4: Run; expect passed.**
- [ ] **Step 5: Commit** `feat(occasions): occasions, per-person plan and carry-forward`

### Task 5: Gifts, tracks, status and the Santa rule

**Files:**
- Create: `gifts.py`
- Modify: `app.py`, `common.py`, `tests/test_people.py` (remove the xfail)
- Test: `tests/test_gifts.py`

**Interfaces:**
- Consumes: `people.user_person_ids(con)`
- Produces: `common.parse_cents(value) -> int | None` (raises `ApiError(400)`)
- Produces: `gifts.OURS = ("idea","decided","ordered","arrived","wrapped","given")`, `gifts.THEIRS = ("told","confirmed","given")`
- Produces: `gifts.track(giver_ids: set[int], user_ids: set[int]) -> str` → `"ours"` when `giver_ids` is empty or intersects `user_ids`, else `"theirs"`
- Produces: `gifts.create(con, user: dict, data: dict) -> int`, `gifts.update(con, gift_id, data) -> None`, `gifts.set_status(con, gift_id, status) -> None`, `gifts.set_givers(con, gift_id, giver_ids: list[int]) -> None`
- Produces: `gifts.to_json(con, row, *, unlocked: bool) -> dict` with keys `id, occasion_id, title, status, track, url, store, price_cents, notes, recipient_ids, giver_ids, told_at, created_by, updated_at` and, only when `unlocked`, `santa`
- Produces routes: `POST /api/gifts`, `GET/PATCH/DELETE /api/gifts/<id>`, `POST /api/gifts/<id>/status` `{status}`, `GET /api/occasions/<oid>/people/<pid>/gifts`, `GET /api/people/<pid>/history` → `{"person": {...}, "gifts": [...]}` newest occasion first

Rules:
- A gift needs a non-empty title (max 200), one occasion and at least one recipient. Givers may be empty.
- Default status on create: `idea` on the ours track, `told` on the theirs track (and `told_at` = today).
- Any status within the gift's track may be set directly. A status from the other track is 400.
- `set_givers` that changes the track maps status: to theirs → `told` (keeps `given`); to ours → `decided` (keeps `given`). Moving to theirs clears `santa`.
- `santa: true` is 400 unless the track is ours and at least one giver is a user.
- `url` must start with `http://` or `https://` (max 2000) or be empty.
- `parse_cents` accepts an int or a string such as `29`, `29.9`, `$29.99`, `1,299.00`; empty string and `None` give `None`; negative, non-numeric, more than two decimals, or above 10,000,000 cents is 400. It never goes through `float`.

- [ ] **Step 1: Write the failing tests**

```python
@pytest.mark.parametrize("raw,cents", [("29", 2900), ("29.9", 2990), ("$29.99", 2999), ("1,299.00", 129900),
                                        (1500, 1500), ("", None), (None, None)])
def test_parse_cents_accepts(raw, cents):
    assert common.parse_cents(raw) == cents

@pytest.mark.parametrize("raw", ["abc", "-5", "NaN", "inf", "1.999", "1e5", 10_000_001, 12.5, True])
def test_parse_cents_rejects(raw):
    with pytest.raises(common.ApiError):
        common.parse_cents(raw)

def test_joint_gift_is_one_row_with_two_recipients_and_two_givers(client, ids):
    g = mk_gift(client, ids.o, "Analogue 3D", to=[ids.jordan, ids.riley], frm=[ids.alex, ids.sam])
    body = client.get(f"/api/gifts/{g}", headers=auth()).get_json()
    assert body["recipient_ids"] == [ids.jordan, ids.riley] and body["giver_ids"] == [ids.alex, ids.sam]
    assert body["track"] == "ours" and body["status"] == "idea"

def test_gift_from_someone_else_starts_told(client, ids, freeze_today):
    freeze_today("2026-11-20")
    g = mk_gift(client, ids.o, "eReader", to=[ids.kid1], frm=[ids.casey])
    body = client.get(f"/api/gifts/{g}", headers=auth()).get_json()
    assert (body["track"], body["status"], body["told_at"]) == ("theirs", "told", "2026-11-20")

def test_status_must_belong_to_the_track(client, ids):
    g = mk_gift(client, ids.o, "Book", to=[ids.jordan], frm=[ids.alex])
    assert client.post(f"/api/gifts/{g}/status", json={"status": "arrived"}, headers=mut()).status_code == 200
    assert client.post(f"/api/gifts/{g}/status", json={"status": "confirmed"}, headers=mut()).status_code == 400

def test_santa_only_on_gifts_from_a_user(client, ids, unlock):
    unlock()
    ok = mk_gift(client, ids.o, "Bike", to=[ids.kid1], frm=[ids.alex, ids.sam])
    assert client.patch(f"/api/gifts/{ok}", json={"santa": True}, headers=mut()).status_code == 200
    no = mk_gift(client, ids.o, "Scarf", to=[ids.kid1], frm=[ids.casey])
    assert client.patch(f"/api/gifts/{no}", json={"santa": True}, headers=mut()).status_code == 400

def test_moving_to_another_giver_clears_santa_and_maps_status(con, ids):
    g = gifts.create(con, USER, {"occasion_id": ids.o, "title": "Bike", "recipient_ids": [ids.kid1],
                                 "giver_ids": [ids.alex], "santa": True, "status": "arrived"})
    gifts.set_givers(con, g, [ids.casey])
    row = con.execute("SELECT status, santa FROM gifts WHERE id=?", (g,)).fetchone()
    assert (row["status"], row["santa"]) == ("told", 0)

@pytest.mark.parametrize("patch", [{"title": ""}, {"recipient_ids": []}, {"url": "javascript:alert(1)"},
                                   {"price": "abc"}, {"recipient_ids": [99999]}])
def test_bad_gift_fields_are_400(client, ids, patch):
    g = mk_gift(client, ids.o, "Book", to=[ids.jordan])
    assert client.patch(f"/api/gifts/{g}", json=patch, headers=mut()).status_code == 400
```

(`ids` is a `conftest.py` fixture creating one occasion and people Jordan, Riley, Casey plus protected kids `kid1`, `kid2`, `kid3`, exposing ids as attributes with `alex` and `sam` as the user people. `unlock` arrives in Task 7; until then make it a no-op fixture and have `to_json` treat every request as unlocked.)

- [ ] **Step 2: Run; expect failures.**
- [ ] **Step 3: Implement `parse_cents`, `gifts.py` and the routes.** The API accepts `price` and returns `price_cents`.
- [ ] **Step 4: Run the whole suite; expect all passed, no xfail.**
- [ ] **Step 5: Commit** `feat(gifts): gifts with many recipients and givers, tracks, status, Santa rule`

### Task 6: Budgets and the occasion summary

**Files:**
- Create: `budgets.py`
- Modify: `app.py`
- Test: `tests/test_budgets.py`

**Interfaces:**
- Consumes: `gifts.track`, `people.list_people`, `people.user_person_ids`
- Produces: `budgets.shares(price_cents: int, recipient_ids: list[int]) -> dict[int, int]` (even split; the remainder cents go to the lowest ids, one each)
- Produces: `budgets.person_totals(con, occasion_id, *, include_protected: bool) -> dict[int, dict]` → per person `{"spent_cents", "planned_cents", "gift_count"}`
- Produces: `budgets.summary(con, occasion_id, *, unlocked: bool) -> dict`
- Produces route: `GET /api/occasions/<oid>/summary`

Rules: only ours-track gifts count toward money. `spent` = statuses `ordered, arrived, wrapped, given`; `planned` = `idea, decided`. A gift with no price counts in `gift_count` only. `summary` returns `{"occasion": {...}, "days_until": int, "groups": [{"id","name","people":[{"id","name","color","plan","budget_cents","spent_cents","planned_cents","gift_count","headline"}]}], "protected": {"locked": bool, "people": [...] }}`. `headline` is the title of the person's most recently updated gift, or `null`. When locked, `protected.people` is `[]` and protected people appear in no group.

- [ ] **Step 1: Write the failing tests**

```python
def test_shares_split_evenly_with_remainder_to_lowest_ids():
    assert budgets.shares(10000, [7, 3, 5]) == {3: 3334, 5: 3333, 7: 3333}

def test_spent_and_planned_follow_status(con, ids):
    mk(con, ids, "Shoes", to=[ids.jordan], frm=[ids.alex], price=4000, status="idea")
    mk(con, ids, "Book", to=[ids.jordan], frm=[ids.alex], price=2500, status="arrived")
    mk(con, ids, "Scarf", to=[ids.jordan], frm=[ids.casey], price=9900, status="confirmed")   # theirs: not counted
    t = budgets.person_totals(con, ids.o, include_protected=True)[ids.jordan]
    assert t == {"spent_cents": 2500, "planned_cents": 4000, "gift_count": 3}

def test_summary_days_until_and_headline(client, ids, freeze_today):
    freeze_today("2026-11-12")            # the fixture occasion is 2026-12-25
    mk_gift(client, ids.o, "Shoes?", to=[ids.jordan], frm=[ids.alex])
    s = client.get(f"/api/occasions/{ids.o}/summary", headers=auth()).get_json()
    assert s["days_until"] == 43
    jordan = next(p for g in s["groups"] for p in g["people"] if p["id"] == ids.jordan)
    assert jordan["headline"] == "Shoes?" and jordan["gift_count"] == 1
```

- [ ] **Step 2: Run; expect failures.**
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run; expect passed.**
- [ ] **Step 5: Commit** `feat(budgets): per-person totals and occasion summary`

### Task 7: PIN, idle lock and protected visibility

**Files:**
- Create: `lock.py`
- Modify: `app.py`, `gifts.py`, `budgets.py`, `people.py`
- Test: `tests/test_lock.py`, `tests/test_privacy.py`

**Interfaces:**
- Produces: `lock.set_pin(con, email, pin: str) -> None` (4 to 8 digits; `hashlib.scrypt`, n=2**14, r=8, p=1, 16-byte salt), `lock.check_pin(con, email, pin) -> bool`
- Produces: `lock.is_unlocked(request, email) -> bool`, `lock.issue(email) -> str`, cookie name `gt_unlock`
- Produces: decorator `app.unlock_required` → 423 `{"error": "Locked", "locked": true}`
- Produces: `gifts.is_protected(con, gift_id) -> bool` (any recipient has `protected=1`), `gifts.visible(con, gift_id, *, unlocked) -> bool`
- Produces routes: `POST /api/lock/pin` `{pin, current?}`, `POST /api/lock/unlock` `{pin}`, `POST /api/lock/lock`; `GET /api/me` gains `has_pin`, `unlocked`, `lock_idle_seconds`

Rules:
- The unlock cookie is `itsdangerous.URLSafeTimedSerializer(secret).dumps({"e": email})`, `HttpOnly`, `Secure`, `SameSite=Strict`, path `/`. The secret is 32 random bytes in `DATA_DIR/secret.key`, created with mode 0600 on first start.
- `is_unlocked` is true when the cookie verifies, belongs to this email, and is younger than `LOCK_IDLE_SECONDS` (default 300). Every response to an unlocked request re-issues it (sliding window).
- Five wrong PINs set `pin_locked_until = now + 60`; unlock answers 429 until then. A correct PIN resets the count. Changing an existing PIN needs `current`.
- While locked: a protected gift answers 404 on `GET/PATCH/DELETE /api/gifts/<id>` and `/status`, is absent from every list, history and total, and creating a gift with a protected recipient is 423. No response contains `santa`. `GET /api/people` still lists protected people (names are not secret).
- A user with no PIN is always locked; the UI sends them to set one.

- [ ] **Step 1: Write the failing tests**

```python
def test_unlock_flow_and_expiry(client, ids, clock):
    set_pin(client, "4821")
    g = mk_protected_gift(ids)                                   # inserted directly through gifts.create
    assert client.get(f"/api/gifts/{g}", headers=auth()).status_code == 404
    assert client.post("/api/lock/unlock", json={"pin": "4821"}, headers=mut()).status_code == 200
    assert client.get(f"/api/gifts/{g}", headers=auth()).status_code == 200
    clock.advance(299); assert client.get(f"/api/gifts/{g}", headers=auth()).status_code == 200   # slides
    clock.advance(301); assert client.get(f"/api/gifts/{g}", headers=auth()).status_code == 404

def test_five_wrong_pins_lock_out_for_60_seconds(client, clock):
    set_pin(client, "4821")
    for _ in range(5):
        assert client.post("/api/lock/unlock", json={"pin": "0000"}, headers=mut()).status_code == 403
    assert client.post("/api/lock/unlock", json={"pin": "4821"}, headers=mut()).status_code == 429
    clock.advance(61)
    assert client.post("/api/lock/unlock", json={"pin": "4821"}, headers=mut()).status_code == 200

@pytest.mark.parametrize("pin", ["123", "123456789", "12a4", ""])
def test_bad_pin_shape_is_400(client, pin):
    assert client.post("/api/lock/pin", json={"pin": pin}, headers=mut()).status_code == 400

def test_unlock_cookie_is_bound_to_its_user(client):
    set_pin(client, "4821", email="alex@example.com")
    client.post("/api/lock/unlock", json={"pin": "4821"}, headers=mut("alex@example.com"))
    assert client.get("/api/me", headers=auth("sam@example.com")).get_json()["unlocked"] is False

def test_gift_shared_with_a_protected_person_is_hidden_everywhere_when_locked(client, ids):
    g = mk_protected_gift(ids, title="Family trip", to=[ids.kid1, ids.jordan], frm=[ids.alex], price=90000, status="arrived")
    lst = client.get(f"/api/occasions/{ids.o}/people/{ids.jordan}/gifts", headers=auth()).get_json()
    assert g not in [x["id"] for x in lst]
    s = client.get(f"/api/occasions/{ids.o}/summary", headers=auth()).get_json()
    jordan = next(p for grp in s["groups"] for p in grp["people"] if p["id"] == ids.jordan)
    assert jordan["spent_cents"] == 0 and jordan["gift_count"] == 0 and jordan["headline"] is None
    assert client.get(f"/api/people/{ids.jordan}/history", headers=auth()).get_json()["gifts"] == []

def test_writes_to_protected_gifts_are_refused_when_locked(client, ids):
    g = mk_protected_gift(ids)
    assert client.patch(f"/api/gifts/{g}", json={"title": "x"}, headers=mut()).status_code == 404
    assert client.post("/api/gifts", json={"occasion_id": ids.o, "title": "x", "recipient_ids": [ids.kid1]},
                       headers=mut()).status_code == 423

# tests/test_privacy.py
def test_no_api_route_leaks_protected_data_when_locked(client, ids):
    """Walk every GET rule under /api/ in app.url_map, fill path arguments from `ids`, and assert the
    response text contains neither the protected gift's title ("ZZ-SECRET-GIFT") nor the key "santa"."""
```

- [ ] **Step 2: Run; expect failures.**
- [ ] **Step 3: Implement `lock.py`; thread `unlocked=lock.is_unlocked(...)` into every `gifts`, `budgets` and history query; make the `unlock` test fixture real.** `clock` monkeypatches `common.now() -> int`.
- [ ] **Step 4: Run the whole suite; expect all passed.**
- [ ] **Step 5: Commit** `feat(lock): PIN, idle lock and protected-gift visibility`

### Task 8: A protected person's board and assignment

**Files:**
- Create: `boards.py`
- Modify: `app.py`
- Test: `tests/test_boards.py`

**Interfaces:**
- Consumes: `people.households`, `gifts.to_json`, `gifts.set_givers`, `budgets.shares`
- Produces: `boards.board(con, occasion_id, person_id) -> dict`
- Produces: `boards.list_text(label: str, gifts: list[dict]) -> str`
- Produces: `boards.assign(con, gift_id, giver_ids: list[int], expected_giver_ids: list[int]) -> None` (raises `ApiError(409, "Someone just assigned this. Reload.")`)
- Produces routes, all `@unlock_required`: `GET /api/occasions/<oid>/board/<pid>`, `POST /api/gifts/<id>/assign` `{giver_ids, expected_giver_ids}`

`board` returns:

```json
{"person": {"id": 1, "name": "Riley", "color": "#b91c1c"},
 "unassigned": [gift, ...],
 "givers": [{"label": "Jordan & Casey", "giver_ids": [4, 5], "is_us": false, "gifts": [gift, ...], "list_text": "..."}],
 "split": {"santa": 1, "us": 1, "others": 3},
 "evenness": [{"person_id": 1, "name": "Riley", "cents": 12900}, ...]}
```

Rules: the board is 404 for a person who is not protected. `unassigned` = gifts for this person with no givers. Giver groups are ordered: other households by label, then "Us" last; a group appears only if it has gifts. A giver set that matches a household exactly uses its label; otherwise the givers' names joined with " & ". `evenness` covers every protected person in the occasion and sums `budgets.shares` of ours-track priced gifts in any status. `assign` runs in one `BEGIN IMMEDIATE` transaction and compares the current giver set with `expected_giver_ids`. `list_text` is the label, a blank line, then one line per gift: the title, and ` - <url>` when there is one.

- [ ] **Step 1: Write the failing tests**

```python
def test_board_groups_by_giver_with_us_last(client, ids, unlock):
    unlock()
    a = mk_gift(client, ids.o, "Murdle", to=[ids.kid1])
    b = mk_gift(client, ids.o, "eReader", to=[ids.kid1, ids.kid2, ids.kid3], frm=[ids.jordan, ids.riley])
    c = mk_gift(client, ids.o, "Belt bag", to=[ids.kid1], frm=[ids.alex, ids.sam])
    brd = client.get(f"/api/occasions/{ids.o}/board/{ids.kid1}", headers=auth()).get_json()
    assert [g["id"] for g in brd["unassigned"]] == [a]
    assert [(x["label"], [g["id"] for g in x["gifts"]]) for x in brd["givers"]] == [("Jordan & Riley", [b]), ("Us", [c])]

def test_split_counts_santa_us_and_others(client, ids, unlock):
    unlock()
    mk_gift(client, ids.o, "Bike", to=[ids.kid1], frm=[ids.alex, ids.sam], santa=True)
    mk_gift(client, ids.o, "Belt bag", to=[ids.kid1], frm=[ids.alex, ids.sam])
    mk_gift(client, ids.o, "eReader", to=[ids.kid1], frm=[ids.jordan])
    brd = client.get(f"/api/occasions/{ids.o}/board/{ids.kid1}", headers=auth()).get_json()
    assert brd["split"] == {"santa": 1, "us": 1, "others": 1}

def test_evenness_splits_shared_gifts(client, ids, unlock):
    unlock()
    mk_gift(client, ids.o, "Camera", to=[ids.kid1, ids.kid2, ids.kid3], frm=[ids.alex], price="90")
    mk_gift(client, ids.o, "Belt bag", to=[ids.kid1], frm=[ids.alex], price="38")
    brd = client.get(f"/api/occasions/{ids.o}/board/{ids.kid1}", headers=auth()).get_json()
    assert {e["person_id"]: e["cents"] for e in brd["evenness"]} == {ids.kid1: 6800, ids.kid2: 3000, ids.kid3: 3000}

def test_assign_conflict_is_409_and_first_assignment_stands(client, ids, unlock):
    unlock()
    g = mk_gift(client, ids.o, "Murdle", to=[ids.kid1])
    first = client.post(f"/api/gifts/{g}/assign", json={"giver_ids": [ids.jordan, ids.riley], "expected_giver_ids": []}, headers=mut())
    second = client.post(f"/api/gifts/{g}/assign", json={"giver_ids": [ids.casey], "expected_giver_ids": []}, headers=mut("sam@example.com"))
    assert (first.status_code, second.status_code) == (200, 409)
    assert client.get(f"/api/gifts/{g}", headers=auth()).get_json()["giver_ids"] == [ids.jordan, ids.riley]

def test_assign_to_others_sets_told_and_unassign_returns_to_idea(client, ids, unlock, freeze_today):
    unlock(); freeze_today("2026-11-20")
    g = mk_gift(client, ids.o, "Murdle", to=[ids.kid1])
    client.post(f"/api/gifts/{g}/assign", json={"giver_ids": [ids.casey], "expected_giver_ids": []}, headers=mut())
    assert client.get(f"/api/gifts/{g}", headers=auth()).get_json()["status"] == "told"
    client.post(f"/api/gifts/{g}/assign", json={"giver_ids": [], "expected_giver_ids": [ids.casey]}, headers=mut())
    assert client.get(f"/api/gifts/{g}", headers=auth()).get_json()["status"] == "idea"

def test_list_text():
    assert boards.list_text("Jordan & Riley", [{"title": "Murdle", "url": "https://example.com/m"}, {"title": "Chess set", "url": None}]) \
        == "Jordan & Riley\n\nMurdle - https://example.com/m\nChess set"

def test_board_locked_is_423_and_unprotected_person_is_404(client, ids, unlock):
    assert client.get(f"/api/occasions/{ids.o}/board/{ids.kid1}", headers=auth()).status_code == 423
    unlock()
    assert client.get(f"/api/occasions/{ids.o}/board/{ids.jordan}", headers=auth()).status_code == 404
```

- [ ] **Step 2: Run; expect failures.**
- [ ] **Step 3: Implement.** Unassigning (empty `giver_ids`) sets status `idea` and clears `told_at`; this overrides the ours-track mapping in Task 5 for the empty set.
- [ ] **Step 4: Run the whole suite; expect all passed.** Confirm `tests/test_privacy.py` still passes with the new routes in `url_map`.
- [ ] **Step 5: Commit** `feat(boards): assignment board with conflict check and copyable lists`

### Task 9: Frontend

**Files:**
- Create: `static/index.html`, `static/style.css`, `static/manifest.json`, `static/icon.svg`, `static/js/package.json` (`{"type": "module"}`), `static/js/{main,core,api,lock,home,idea,person,gift,board,settings}.js`
- Modify: `app.py` (`GET /` renders `index.html` with `APP_NAME`; `GET /manifest.json` is generated from `APP_NAME`)
- Test: `tests/test_frontend.py`, `tests/frontend.test.mjs`

**Interfaces:**
- Consumes: every route above
- Produces: `core.js` exports `esc(s)`, `money(cents)`, `daysLabel(n)`, `statusSteps(track)`; `api.js` exports `get(path)`, `send(method, path, body)` (adds `X-Tracker: 1`; on 423 dispatches a `locked` event)

Screens, as hash routes (the sketches are in the spec, "Screens"):

| Route | Module | Shows |
| --- | --- | --- |
| `#/` | `home.js` | Occasion picker, days until, `+ Idea`, the protected section as one locked row or one row per protected person, then groups with each person's plan, headline, spent of budget |
| `#/idea` | `idea.js` | For (people, most recently used first), What, From (me, other user, both); Save returns to where the user came from |
| `#/person/<id>` | `person.js` | This occasion's gifts, plan and budget editor, then history by occasion |
| `#/gift/<id>` | `gift.js` | Title, recipients, givers, status steps as buttons, Santa checkbox (only when unlocked and allowed), link, store, price, notes |
| `#/board/<pid>` | `board.js` | The board: Assign menu from `/api/households`, told and confirmed buttons, Copy list (uses `navigator.clipboard.writeText(list_text)`), split line, evenness line |
| `#/settings` | `settings.js` | People, groups, households, colors, protected flag, occasions (new, copy from), PIN |
| overlay | `lock.js` | PIN pad on a `locked` event or when a board is opened locked; also a visible Lock button in the header |

Rules: phone-first single column, a two-column layout from 900px. Each person's row carries a left border in `people.color`. `document.title` is always `APP_NAME`. An idle timer in `main.js` calls `POST /api/lock/lock` and re-renders after `lock_idle_seconds` without pointer or key input, and on `visibilitychange` to hidden. The Assign menu sends the gift's current giver ids as `expected_giver_ids`; on 409 it shows the server message and reloads the board.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_frontend.py
def test_index_title_and_manifest_use_app_name_only(client):
    html = client.get("/", headers=auth()).get_data(as_text=True)
    assert "<title>Ledger</title>" in html
    m = client.get("/manifest.json", headers=auth()).get_json()
    assert (m["name"], m["short_name"]) == ("Ledger", "Ledger")

def test_no_inline_script_or_handlers():
    html = open("static/index.html").read()
    assert "<script>" not in html and "onclick=" not in html

def test_every_innerhtml_interpolation_is_escaped():
    """For each static/js/*.js, every `${...}` inside a template literal assigned to innerHTML is
    esc(...), money(...), daysLabel(...) or a call to a local function whose name starts with `render`."""

def test_document_title_is_never_assigned_a_variable():
    for f in glob("static/js/*.js"):
        assert "document.title" not in open(f).read() or f.endswith("main.js")
```

```js
// tests/frontend.test.mjs (node --test)
test("esc escapes the five HTML characters", () => assert.equal(esc(`<a href="x">'&`), "&lt;a href=&quot;x&quot;&gt;&#39;&amp;"));
test("money", () => { assert.equal(money(2999), "$29.99"); assert.equal(money(129900), "$1,299.00"); assert.equal(money(null), ""); });
test("daysLabel", () => { assert.equal(daysLabel(43), "43 days"); assert.equal(daysLabel(1), "1 day"); assert.equal(daysLabel(0), "today"); assert.equal(daysLabel(-2), "2 days ago"); });
test("statusSteps", () => { assert.deepEqual(statusSteps("theirs"), ["told", "confirmed", "given"]); assert.equal(statusSteps("ours").length, 6); });
```

- [ ] **Step 2: Run `python -m pytest -q tests/test_frontend.py` and `node --test tests/frontend.test.mjs`; expect failures.**
- [ ] **Step 3: Build `core.js`, `api.js`, `index.html`, `style.css`, then one screen per commit in the table's order.**
- [ ] **Step 4: Run `python -m pytest -q`, `node --test tests/frontend.test.mjs` and `for f in static/js/*.js; do node --check "$f"; done`; expect all passed.**
- [ ] **Step 5: Manual check with `DEV_EMAIL=alex@example.com USERS="alex@example.com=Alex,sam@example.com=Sam" python3 app.py`:** create a group and four people (two protected), an occasion, set a PIN, add an idea from `#/idea` in two fields, open a board, assign, copy a list, wait out the idle lock and confirm the board closes.
- [ ] **Step 6: Commit** per screen, `feat(ui): <screen>`

### Task 10: Installer, backup and deployment notes

**Files:**
- Create: `installer/setup-head.sh`, `installer/build.sh`, `deploy/deploy.env.example`, `ops/tracker-backup`, `.env.example`
- Modify: `README.md`
- Test: `tests/test_installer_static.py`

**Interfaces:**
- Produces: `sh installer/build.sh` → `dist/setup-tracker.sh`, a single idempotent installer for Alpine + OpenRC

Rules: the installer installs `py3-flask py3-gunicorn py3-jwt py3-cryptography` with `apk`, replaces `/opt/tracker`, writes `/etc/init.d/tracker` running gunicorn as an unprivileged `tracker` user bound to `127.0.0.1:8767`, keeps `/var/lib/tracker` and `/etc/conf.d/tracker`, and adds missing settings without overwriting existing ones. Settings: `TRACKER_PORT`, `ALLOWED_EMAILS`, `USERS`, `CF_TEAM_DOMAIN`, `CF_AUD`, `APP_NAME`, `LOCK_IDLE_SECONDS`. `DEV_EMAIL` is never written by the installer. `ops/tracker-backup` uses `sqlite3 ... ".backup"` into `/var/backups/tracker` with mode 0600 and keeps 14 days.

`README.md` documents, with `tracker.example.com`: creating a separate Cloudflare Access application whose policy includes only the two emails, setting its session duration, pointing the tunnel at `http://127.0.0.1:8767`, running `cloudflared` in the same container so nothing listens on the LAN, and that the backups contain every secret.

- [ ] **Step 1: Write the failing tests**

```python
def test_build_produces_a_syntactically_valid_installer(tmp_path):
    subprocess.run(["sh", "installer/build.sh"], check=True)
    subprocess.run(["sh", "-n", "dist/setup-tracker.sh"], check=True)

def test_installer_binds_loopback_and_never_sets_dev_email():
    head = open("installer/setup-head.sh").read()
    assert "127.0.0.1:" in head and "0.0.0.0" not in head and "DEV_EMAIL" not in head

def test_every_python_module_and_setting_is_shipped():
    """Each top-level *.py is in build.sh's copy list; each name read from os.environ in settings.py
    except DEV_EMAIL and DATA_DIR appears in .env.example and in the installer's settings list."""
```

- [ ] **Step 2: Run; expect failures.**
- [ ] **Step 3: Implement, following `family-wishlist/deploy/setup-wishlist.sh.in` and Matinee's `installer/`.**
- [ ] **Step 4: Run `python -m pytest -q` and `shellcheck -s sh installer/setup-head.sh installer/build.sh ops/tracker-backup`; expect clean.**
- [ ] **Step 5: Commit** `chore(installer): Alpine installer, backup script and deployment notes`

---

## Done when

- `python -m pytest -q`, `node --test tests/frontend.test.mjs`, `node --check` on every module, `ruff check` and `shellcheck` are clean, and CI is green.
- On the container: a request from the LAN to port 8767 is refused at the socket, a third email is refused by Access, and each owner can add an idea, assign a wish on a board and see the board lock itself after five idle minutes.
