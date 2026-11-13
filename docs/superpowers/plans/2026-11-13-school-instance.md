# School Instance (Plan 2 of 6) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `MODE=school` turns a second deployment of the same code into the staff-gift tracker: a role on each staff member, a detail and a done flag on each recipient of a group gift, and a staff-by-gift matrix, with its own database, port, service and access list.

**Architecture:** No fork. One setting, `settings.MODE` (`family`, the default, or `school`), read at request time through `common.is_school()`. Family-only routes are wrapped in `family_only` and answer the generic 404 in school mode; school-only routes are wrapped in `school_only` and answer the same 404 in family mode, so a family instance has exactly the API it has today. Schema version 2 adds `people.role` and `gift_recipients.detail`/`done`. The matrix logic is route-free in `school.py`; its routes sit in their own block of `app.py`. The frontend swaps the home screen for the matrix and hides the board in school mode. The installer takes an instance name so a second service, user, data directory and port can sit beside the first.

**Tech Stack:** as the core plan. No new dependency.

**Spec:** `docs/superpowers/specs/2026-11-12-gift-tracker-design.md`, section "School instance". Builds on `docs/superpowers/plans/2026-11-12-gift-tracker-core.md` (Plan 1). Neither is edited here.

## Decisions fixed before the build

- Family mode is untouched: no existing test changes except the one that reads the schema version (Task 3), no key is added to any family-mode response (`/api/me` gains `mode` and a person gains `role` only in school mode), and `role` in a request body is ignored in family mode.
- School mode has no lock at all. `app.unlocked()` is true for every request, so nothing in the code that filters on the lock hides anything; what must not appear is removed at the edges instead: `santa` is dropped from every gift response and a request that names it is 400, and `people._person` reports `protected: false` for everyone and a request that sets it true is 400. A copied family database with `protected = 1` rows therefore still shows no protected person.
- School routes: `GET /api/occasions/<oid>/matrix`, `PUT` and `DELETE /api/gifts/<id>/recipients/<pid>`, `POST /api/gifts/<id>/recipients {role}`.
- A gift always keeps at least one recipient (the core rule): removing the last cell is 409 "delete the gift instead", and the "add gift" form creates a gift with its first recipients in one call.
- Matrix rows: every recipient of one of the occasion's gifts; anyone in the occasion with a plan other than `skip` (inactive staff included); and every other active person who is not an owner and not skipped, so a teacher added in Settings is in the grid at once (a new school instance would otherwise need a plan set on 22 person pages before the grid showed anyone). Columns are the occasion's gifts by id. Counts are per column: `recipient_count` and `done_count`.
- `gifts.update` must stop deleting and re-inserting recipients, or every edit of a group gift would erase its details.
- Instance name: `--instance school` or `TRACKER_INSTANCE=school` (not `INSTANCE`, a name other tooling sets). Without one, every path, the service name, the user and the port are what they are today. With one: service, user and folders `tracker-<name>`, default port 8768.

## Review Focus

1. Every family-only route (`/api/lock/*`, the board, assign) answers 404 in school mode, identical to a missing path; every school route answers 404 in family mode. (Task 2)
2. In school mode no response contains `santa`, no person is protected, and a request that sets either is refused: even for data created by a family instance in the same database. (Task 2)
3. A v1 database migrates to v2 keeping every row, with `role` null, `detail` null and `done` 0, and migrating twice changes nothing. (Task 3)
4. Editing a group gift's recipients (adding one, removing one) keeps the detail and done flag of everyone who stays. (Task 3)
5. The default install is byte-for-byte what it was: the init script rendered with no instance name equals the old one (plus the new `MODE` export), and an instance cannot share a path, service, user or port default with it. (Task 6)

## File map

```
settings.py     + MODE
common.py       + is_school()
people.py       + role (school only), protected never reported or accepted in school mode
gifts.py        update(): diff recipients instead of replacing them
school.py       NEW: matrix(), set_cell(), remove_cell(), add_role()
db.py           + _to_v2, MIGRATIONS[2], SCHEMA_VERSION = 2
app.py          + family_only, school_only, mode handling in unlocked/slide_unlock/gift_json/me, the school routes block
static/js/      + mode.js (pure), school.js (the matrix screen); main.js, settings.js take mode into account
static/style.css + .matrix
installer/      + instance name, MODE setting; build.sh bakes MODE
.env.example, deploy/deploy.env.example, .gitignore (deploy/*.env)
tests/          + test_school.py; test_db.py, test_installer_static.py, frontend.test.mjs extended
README.md, CLAUDE.md  second-instance section, school-mode rules
```

---

### Task 1: This plan

- [ ] Commit `docs: plan for the school instance`

### Task 2: The MODE setting and what it hides

**Files:**
- Modify: `settings.py`, `common.py`, `people.py`, `app.py`, `.gitignore`, `.env.example`, `deploy/deploy.env.example`, `installer/setup-head.sh` (settings list and the exported variables only), `installer/build.sh` (bake `MODE`)
- Test: `tests/test_school.py` (mode part)

**Interfaces:**
- Produces: `settings.MODE: str`, one of `settings.MODES = ("family", "school")`; any other value raises `ValueError` at import. Read from the environment variable `MODE`, default `family`.
- Produces: `common.is_school() -> bool` (`settings.MODE == "school"`, read at call time so tests can set it).
- Produces: `app.family_only(view)` and `app.school_only(view)` decorators. Each raises werkzeug `NotFound` when the mode is wrong, so the body is exactly `{"error": "Not Found"}` as for a path that does not exist. The wrapper carries `mode_only = "family"` or `"school"` so a test can find every such view through `app.url_map`.
- `family_only`: `lock_set_pin`, `lock_unlock`, `lock_lock`, `person_board`, `gifts_assign`.
- In school mode: `app.unlocked()` is `True`; `slide_unlock` neither issues nor clears the cookie; `gift_json` drops `santa`; `refuse_locked_write` answers 400 `{"error": "Santa is not used in school mode."}` when the body has a `santa` key; `people._person` reports `protected: false`; creating or patching a person with `protected: true` is 400; `GET /api/me` adds `"mode": "school"` (the key is absent in family mode).

- [ ] **Step 1: Write the failing tests** (`tests/test_school.py`, fixture `school` sets `settings.MODE = "school"` with `monkeypatch`)

```python
FAMILY_ONLY = {"lock_set_pin", "lock_unlock", "lock_lock", "person_board", "gifts_assign"}

def test_the_family_only_views_are_exactly_these(client):
    found = {r.endpoint for r in tracker.app.url_map.iter_rules()
             if getattr(tracker.app.view_functions[r.endpoint], "mode_only", None) == "family"}
    assert found == FAMILY_ONLY

def test_every_family_only_route_is_404_in_school_mode(client, ids, school):
    missing = client.get("/api/nothing", headers=auth())
    for rule in family_only_rules():
        for url, values in urls(rule, world(ids)):      # path arguments from tests/test_privacy.py's args()
            for method in sorted(rule.methods & {"GET", "POST", "PUT", "PATCH", "DELETE"}):
                r = client.open(url, method=method, json={}, headers=mut())
                assert (r.status_code, r.get_json()) == (404, missing.get_json()), (method, url)

def test_family_mode_routes_are_unchanged(client, ids, unlock):   # the same urls answer as before
    assert client.post("/api/lock/pin", json={"pin": "4821"}, headers=mut()).status_code == 200
    unlock()
    assert client.get(f"/api/occasions/{ids.o}/board/{ids.kid1}", headers=auth()).status_code == 200

def test_every_school_route_is_404_in_family_mode(client, ids): ...   # matrix, PUT/DELETE cell, POST role

def test_school_mode_never_returns_or_accepts_santa(client, ids, school):
    g = mk_gift(client, ids.o, "Card", to=[ids.jordan])
    assert "santa" not in client.get(f"/api/gifts/{g}", headers=auth()).get_json()
    for body in ({"santa": True}, {"santa": False}, {"santa": None}):
        r = client.patch(f"/api/gifts/{g}", json=body, headers=mut())
        assert (r.status_code, r.get_json()) == (400, {"error": "Santa is not used in school mode."})
        r = client.post("/api/gifts", json={"occasion_id": ids.o, "title": "x", "recipient_ids": [ids.jordan], **body}, headers=mut())
        assert r.status_code == 400
    assert con_rows("SELECT santa FROM gifts") == [(0,)]

def test_a_family_database_opened_in_school_mode_shows_no_santa_and_no_protected_person(client, ids, monkeypatch):
    # data made by a family instance: a protected child, a Santa gift for her
    gid = mk_protected_gift(ids, santa=True)
    monkeypatch.setattr(settings, "MODE", "school")
    seen = walk_all_gets(client, ids)                  # every GET under /api/ from test_privacy's walker
    assert all('"santa"' not in text and '"protected": true' not in text for text in seen.values())
    assert client.get(f"/api/gifts/{gid}", headers=auth()).status_code == 200      # nothing is locked

def test_protected_true_is_400_in_school_mode(client, ids, school):
    assert client.post("/api/people", json={"name": "Ms. Avery", "protected": True}, headers=mut()).status_code == 400
    assert client.patch(f"/api/people/{ids.jordan}", json={"protected": True}, headers=mut()).status_code == 400
    assert client.post("/api/people", json={"name": "Ms. Blake", "protected": False}, headers=mut()).status_code == 201

def test_me_has_mode_only_in_school_mode(client, school): assert client.get("/api/me", headers=auth()).get_json()["mode"] == "school"
# family mode: tests/test_identity.py's exact-equality check of /api/me still passes untouched

def test_school_mode_sets_no_unlock_cookie(client, school): assert "gt_unlock" not in (client.get("/api/me", headers=auth()).headers.get("Set-Cookie") or "")

@pytest.mark.parametrize("raw", ["School", " SCHOOL "]) ...   # accepted, lowercased
def test_bad_mode_is_refused_at_import(): run `python -c "import settings"` with MODE=staff: ValueError mentioning MODE
def test_mode_is_in_every_settings_list(): covered by tests/test_installer_static.py::test_every_setting_is_in_env_example_and_the_installer
```

- [ ] **Step 2: Run; expect failures.**
- [ ] **Step 3: Implement.** `.env.example`: `MODE=family`. `deploy.env.example`: a commented `MODE=` block explaining that a school build is made with `DEPLOY_ENV=deploy/school.env`. Installer: `add_setting "$CONF" MODE "${MODE:-family}" ...` and `MODE` added to the exported names. `build.sh`: `MODE` joins the baked keys.
- [ ] **Step 4: Run the whole suite.**
- [ ] **Step 5: Commit** `feat(school): MODE setting; family-only routes are 404 in school mode`

### Task 3: Schema v2, role, detail and done

**Files:**
- Modify: `db.py`, `people.py`, `gifts.py`, `tests/test_db.py` (the one assertion that reads the schema version: `"1"` becomes `"2"`, which cannot stay true once the version is bumped)
- Test: `tests/test_db.py`, `tests/test_school.py`, `tests/test_gifts.py` untouched

**Interfaces:**
- Produces: `db.SCHEMA_VERSION = 2`; `db._to_v2(con)`: `ALTER TABLE people ADD COLUMN role TEXT`, `ALTER TABLE gift_recipients ADD COLUMN detail TEXT`, `ALTER TABLE gift_recipients ADD COLUMN done INTEGER NOT NULL DEFAULT 0`; registered as `MIGRATIONS[2]` on its own line under the v1 entry. `SCHEMA` (version 1) is not edited.
- Produces: in school mode only, `role` on every person JSON (`str | None`) and accepted by `POST`/`PATCH /api/people` (text, at most 50 characters, empty stored as null); in family mode `role` in a body is ignored and never returned.
- Changes: `gifts.update(recipient_ids=...)` deletes only the recipients that were removed and inserts only the new ones.

- [ ] **Step 1: Write the failing tests**

```python
def test_a_v1_database_migrates_to_v2_keeping_its_rows(tmp_path):
    con = db.connect(str(tmp_path / "t.db"))
    for stmt in db._statements(db.SCHEMA): con.execute(stmt)
    con.execute("INSERT INTO meta VALUES ('schema_version', '1')")
    con.execute("INSERT INTO people (id, name) VALUES (1, 'Ms. Avery'), (2, 'Mr. Diaz')")
    con.execute("INSERT INTO occasions (id, name, kind, date) VALUES (1, 'Christmas 2025', 'christmas', '2025-12-19')")
    con.execute("INSERT INTO gifts (id, occasion_id, title, status, created_by, created_at, updated_at) VALUES (1, 1, 'Card', 'given', 'a', 0, 0)")
    con.execute("INSERT INTO gift_recipients VALUES (1, 1), (1, 2)")
    db.migrate(con)
    assert con.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()[0] == "2"
    assert [tuple(r) for r in con.execute("SELECT id, name, role FROM people ORDER BY id")] == [(1, "Ms. Avery", None), (2, "Mr. Diaz", None)]
    assert [tuple(r) for r in con.execute("SELECT gift_id, person_id, detail, done FROM gift_recipients ORDER BY person_id")] == [(1, 1, None, 0), (1, 2, None, 0)]
    db.migrate(con)   # idempotent
    assert con.execute("SELECT COUNT(*) FROM gift_recipients").fetchone()[0] == 2

def test_a_fresh_database_has_the_v2_columns(con): PRAGMA table_info lists role, detail, done (done NOT NULL, default 0)
def test_a_database_newer_than_the_code_is_refused(con): meta version 3 -> RuntimeError (existing behavior, pinned for the stacked versions)

def test_role_is_school_only(client, school): create with role " homeroom " -> "homeroom"; PATCH role "" -> None; 51 characters -> 400; role: 5 -> 400
def test_role_is_ignored_and_absent_in_family_mode(client): POST /api/people {"name": "X", "role": "homeroom"} -> 201, "role" not in the response, con_rows("SELECT role FROM people WHERE name='X'") == [(None,)]

def test_editing_the_recipients_of_a_group_gift_keeps_detail_and_done(client, school, staff):
    g = mk_gift(client, occ, "Card", to=[avery, blake])
    PUT cell avery {"detail": "$35 coffee shop", "done": True}   # Task 4's route; this test is added there
    PATCH /api/gifts/<g> {"recipient_ids": [avery, chen]}
    assert con_rows("SELECT person_id, detail, done FROM gift_recipients WHERE gift_id=? ORDER BY person_id", g) == [(avery, "$35 coffee shop", 1), (chen, None, 0)]
```
(The last one is written against `gifts.update` directly in this task, setting `detail` and `done` with SQL, and repeated through the routes in Task 4.)

- [ ] **Step 2: Run; expect failures.**
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run the whole suite.**
- [ ] **Step 5: Commit** `feat(school): schema v2 with staff role and per-recipient detail and done`

### Task 4: The matrix API

**Files:**
- Create: `school.py`
- Modify: `app.py` (a separate block of routes), `tests/test_school.py`

**Interfaces:**
- Consumes: `gifts.get`, `gifts.PROTECTED_SQL`, `occasions.get`, `people.get_person`, `common.text/flag`
- Produces:
  - `school.matrix(con, occasion_id, *, unlocked) -> dict`:
    `{"occasion": {id, name, kind, date, archived}, "gifts": [{id, title, status, recipient_count, done_count}], "staff": [{id, name, role, color, active, plan}], "cells": [{gift_id, person_id, detail, done}]}`. `gifts` by id, `staff` by `sort_order, id`, `cells` by `gift_id, person_id`; `done` is a bool, `plan` is `null` for a person with no plan in this occasion. Which people are rows: see Decisions.
  - `school.set_cell(con, gift_id, person_id, data) -> tuple[dict, bool]`: adds the recipient when missing (the bool is `True`) or updates it; only the keys present change (`detail`: text up to 200 characters, empty or null clears it; `done`: true or false). Returns `{gift_id, person_id, detail, done, recipient_count, done_count}`. Touches `gifts.updated_at`.
  - `school.remove_cell(con, gift_id, person_id) -> dict`: the column counts after. 404 when that person is not a recipient; 409 `{"error": "A gift needs at least one recipient. Delete the gift instead."}` when it is the only one.
  - `school.add_role(con, gift_id, data) -> dict`: `{"added": [person ids], "recipient_count", "done_count"}`; adds every active person whose role equals `data["role"]` (trimmed, case-insensitive) and is not yet a recipient. `role` missing, not text or empty is 400. A role nobody has adds nobody (200).
- Routes (all `school_only`, all after the gift is checked with `visible_gift`): `GET /api/occasions/<oid>/matrix`; `PUT /api/gifts/<gid>/recipients/<pid>` (201 when it added, else 200); `DELETE` the same path; `POST /api/gifts/<gid>/recipients`.
- New path arguments need no new `args()` value: `gift_id`, `person_id`, `occasion_id` exist in `tests/test_privacy.py`.

- [ ] **Step 1: Write the failing tests** (fixture `staff`: occasion "Christmas 2025", Ms. Avery and Ms. Blake homeroom, Ms. Chen assistant, Mr. Diaz special subject, Ms. Ellis administrator, all in school mode; gifts "Card $35", "Card $25", "Apprec.", "Granola")

```python
def test_matrix_shape_and_counts(client, school, staff):
    put(card35, avery, {"detail": "$35 · coffee shop", "done": True}); put(card35, blake, {})
    m = client.get(f"/api/occasions/{staff.o}/matrix", headers=auth()).get_json()
    assert [g["title"] for g in m["gifts"]] == ["Card $35", "Card $25", "Apprec.", "Granola"]
    assert m["gifts"][0] == {"id": card35, "title": "Card $35", "status": "idea", "recipient_count": 2, "done_count": 1}
    assert {"gift_id": card35, "person_id": avery, "detail": "$35 · coffee shop", "done": True} in m["cells"]
    assert {"gift_id": card35, "person_id": blake, "detail": None, "done": False} in m["cells"]
    assert [s["name"] for s in m["staff"]] == ["Ms. Avery", "Ms. Blake", "Ms. Chen", "Mr. Diaz", "Ms. Ellis"]
    assert m["staff"][0]["role"] == "homeroom"

def test_rows_are_recipients_plus_everyone_active_who_is_not_skipped_or_an_owner(...)
def test_a_new_occasion_starts_from_the_previous_staff_list(...)   # POST /api/occasions copy_from, then matrix lists them with no cells
def test_put_adds_then_updates_and_only_changes_the_keys_sent(...)
    assert put({}).status_code == 201; assert put({"done": True}).status_code == 200; assert put({"detail": "x"}).get_json()["done"] is True
    assert put({"detail": None}).get_json()["detail"] is None
@pytest.mark.parametrize("body", [{"done": "yes"}, {"done": 1}, {"detail": 5}, {"detail": "x" * 201}]) -> 400, nothing stored
def test_put_unknown_person_or_gift_is_404(...)
def test_delete_removes_a_cell_and_refuses_the_last_one(...)   # 200 then 409 with the message above, 404 for a non-recipient
def test_add_everyone_with_a_role(...)    # role "Homeroom" adds Avery and Blake, again adds nobody; an inactive homeroom teacher is skipped; existing detail/done of a current recipient are kept
@pytest.mark.parametrize("body", [{}, {"role": ""}, {"role": 3}, {"role": "  "}]) -> 400
def test_editing_recipients_through_patch_keeps_details(...)      # review focus 4, through the routes
def test_cells_survive_deleting_nothing_else...: deleting a gift removes its cells (cascade) and its column
def test_matrix_for_a_missing_occasion_is_404(...)
def test_school_writes_need_the_json_guards(...)   # PUT without X-Tracker is 400, from the LAN 403
```

- [ ] **Step 2: Run; expect failures.**
- [ ] **Step 3: Implement `school.py` and the routes.** Writes run in `db.transaction`; counts are computed inside it.
- [ ] **Step 4: Run the whole suite, including `tests/test_privacy.py` and `tests/test_identity.py` (they walk the new routes).**
- [ ] **Step 5: Commit** `feat(school): staff-by-gift matrix API`

### Task 5: The matrix screen

**Files:**
- Create: `static/js/mode.js`, `static/js/school.js`
- Modify: `static/js/main.js`, `static/js/settings.js`, `static/style.css`, `tests/frontend.test.mjs`

**Interfaces:**
- Produces (`mode.js`, pure): `isSchool(me)`, `lockApplies(me)` (false in school mode), `pickScreen(name, family, school, me)` (the loader for a route: in school mode the board is `null` and `school[name]` replaces `family[name]`), `ROLE_SUGGESTIONS = ["homeroom", "assistant", "special subject", "administrator"]`, `roleChoices(people)` (suggestions plus the roles in use, case-insensitive, first spelling wins), `peopleWithRole(people, role)` (active, case-insensitive).
- Produces (`school.js`): `cellState(cell)` -> `"off" | "on" | "done"`, `columnCaption(gift)` -> `"7 · 3 done"`, `cellPayload({detail, done})`, `newGiftPayload(v, occasionId, people)` (400-style `Error` for an empty title or a role nobody has), `renderMatrix(matrix, roles)`, `renderCellEditor(gift, person, cell)`, `renderAddGift(people)`, `render(ctx)`.
- `main.js`: loads the school home through `pickScreen`; the lock button is hidden, and the idle and hidden-tab timers do nothing, when `lockApplies` is false.
- `settings.js`: in school mode the person form has a Role field (with a datalist of `roleChoices`) and no Protected box, the PIN card is not drawn, and a person's role shows as a badge. `personPayload` sends `role` only when the form has the field. Family mode output is identical.
- Rules: every interpolation goes through `esc` or a `render*` helper; no `style=`; colors through `renderColor` and `applyColors`; `document.title` untouched.

- [ ] **Step 1: Write the failing tests** (`node --test`)

```js
test("school: pickScreen hides the board and replaces home only in school mode", () => {
  const fam = { "": "F", board: "B", gift: "G" }, sch = { "": "S" };
  assert.equal(pickScreen("", fam, sch, { }), "F");
  assert.equal(pickScreen("board", fam, sch, {}), "B");
  assert.equal(pickScreen("", fam, sch, { mode: "school" }), "S");
  assert.equal(pickScreen("board", fam, sch, { mode: "school" }), null);
  assert.equal(pickScreen("gift", fam, sch, { mode: "school" }), "G");
});
test("school: the lock does not apply", () => { assert.equal(lockApplies({ mode: "school" }), false); assert.equal(lockApplies({}), true); });
test("school: roleChoices keeps the suggestions, adds roles in use once, and peopleWithRole ignores case and inactive people", ...);
test("school: cell state and column caption", () => { assert.equal(cellState(undefined), "off"); assert.equal(cellState({ done: false }), "on"); assert.equal(cellState({ done: true }), "done"); assert.equal(columnCaption({ recipient_count: 7, done_count: 3 }), "7 · 3 done"); });
test("school: the matrix escapes names, titles and details and shows a total row", () => { <script> in every typed field is absent from the markup; footer holds each recipient_count });
test("school: newGiftPayload", ...)  // role choice -> every active person with the role; empty title throws; unknown role throws
test("school: settings forms show Role and no Protected box in school mode, and nothing changes in family mode", ...)
```

- [ ] **Step 2: Run; expect failures.**
- [ ] **Step 3: Implement.** Tapping an empty cell adds that person; tapping a filled one opens the editor (detail, Done, Save, Remove). The column head links to the gift and has an "Add role" select that posts to `/api/gifts/<id>/recipients`. The page refreshes the matrix in place after each change. The add-gift form offers "everyone with role X" and single people.
- [ ] **Step 4: Run `python -m pytest -q tests/test_frontend.py`, `node --check` on each module and `node --test tests/frontend.test.mjs`.** No browser is available to the builder: nothing here has been seen rendered; say so in the PR.
- [ ] **Step 5: Commit** `feat(school): matrix screen, role field and a school-aware shell`

### Task 6: Installer instance name

**Files:**
- Modify: `installer/setup-head.sh`, `tests/test_installer_static.py`

**Interfaces:**
- The installer accepts `TRACKER_INSTANCE=<name>` or `--instance <name>` (`--instance=<name>`), for install and `--uninstall`; the flag wins. A name is 1 to 16 of `a-z 0-9 -`, not starting with `-`; anything else exits 1. An unknown argument exits 1 (a typo must not install over the default).
- With no name: service `tracker`, user and group `tracker`, `/opt/tracker`, `/var/lib/tracker`, `/var/backups/tracker`, `/etc/conf.d/tracker`, `/etc/init.d/tracker`, `/var/log/tracker.log`, `/etc/periodic/daily/tracker-backup`, default port 8767. With name `school`: the same with `tracker-school` in place of `tracker`, default port 8768, and the daily job is a small wrapper that exports that instance's `DATA_DIR`, `BACKUP_DIR` and `LOG` and runs `/opt/tracker-school/ops/tracker-backup`.
- The init script is a template rendered with `sed` (markers `# initd-begin` and `# initd-end`), the name and path variables sit between `# instance-begin` and `# instance-end`: the tests run exactly those fragments.
- `.gitignore` gets `deploy/*.env` in Task 2, where `deploy.env.example` first tells people to make `deploy/school.env`.

- [ ] **Step 1: Write the failing tests**

```python
def test_default_install_names_and_paths_are_unchanged():           # runs the instance fragment with no instance name
    assert vars_ == {"NAME": "tracker", "APP_DIR": "/opt/tracker", "DATA_DIR": "/var/lib/tracker", "BACKUP_DIR": "/var/backups/tracker",
                     "CONF": "/etc/conf.d/tracker", "INITD": "/etc/init.d/tracker", "LOG": "/var/log/tracker.log",
                     "SVC_USER": "tracker", "BACKUP_JOB": "/etc/periodic/daily/tracker-backup", "DEFAULT_PORT": "8767"}
def test_default_init_script_is_what_it_was():                      # renders the template, compares with the old script plus the MODE export
    assert rendered == GOLDEN_DEFAULT_INIT
def test_an_instance_gets_its_own_names_paths_and_port():           # school: tracker-school everywhere, 8768, and no path equals a default one
    assert set(default_values).isdisjoint(set(school_values)) for the path-like variables
    assert "/opt/tracker-school" in rendered and "/opt/tracker " not in rendered
@pytest.mark.parametrize("bad", ["Sch", "a b", "-x", "a/b", "a.b", "x" * 17, "../x"]) -> exit 1
def test_instance_flag_and_environment_agree(): `--instance school`, `--instance=school`, TRACKER_INSTANCE=school give the same names
def test_an_unknown_argument_is_refused(): exit 1 and no variables printed
def test_installer_still_binds_loopback_only_and_never_writes_dev_email()   # existing tests keep passing
```

- [ ] **Step 2: Run; expect failures.**
- [ ] **Step 3: Implement.** Port: an existing install keeps its port from its own conf; otherwise `TRACKER_PORT`, otherwise the default above.
- [ ] **Step 4: Run `shellcheck -s sh installer/setup-head.sh installer/build.sh ops/tracker-backup` and the suite.** The installer itself has not been run: it needs root, Alpine and `apk`.
- [ ] **Step 5: Commit** `feat(installer): instance name for a second service on the same container`

### Task 7: Docs

**Files:**
- Modify: `README.md` (a section "A second instance: school" before "Backups"; a line in Development), `CLAUDE.md` (a separate "School mode" section)

- [ ] **Step 1:** README: what school mode is, building with `DEPLOY_ENV=deploy/school.env`, `MODE=school ... sh setup-tracker.sh --instance school`, its own Access application, hostname, tunnel to its port, `rc-service tracker-school ...`, backups, uninstall.
- [ ] **Step 2:** CLAUDE.md: the rules that hold in school mode (no lock, no Santa, no protected; mode-gated routes use `family_only`/`school_only`; family responses gain no key; `gifts.update` keeps recipient rows).
- [ ] **Step 3: Run the full gate.**
- [ ] **Step 4: Commit** `docs: run a second, school-mode instance`

---

## Done when

- The verification gate in `CLAUDE.md` is clean.
- In school mode: no family-only route answers, no response carries `santa`, nobody is protected, the matrix lists staff by gift with per-column counts, a tap adds or removes a person, a detail and a done flag can be set, and "add everyone with role" works.
- In family mode: every existing test passes with the single schema-version assertion updated, and no family response gained a key.
- Unverified until deployed: Cloudflare Access for the second hostname, the installer on a real Alpine container (both instances side by side), and the screens on a phone.
