# After Purchase (Plan 3 of 6) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** From purchase to unwrapping, a gift shows seller, ship-to address, expected delivery, hiding place and wrapped state; a wrong ship-to is flagged while the order can still be cancelled; and three working lists (Not here yet, To wrap, Where is it?) plus their counts on the home screen answer "what is on its way" and "where did we hide it".

**Architecture:** Schema version 3 adds `locations` (a self-referencing tree), `addresses` (exactly one home) and eight columns on `gifts`. Route-free logic goes in three new modules that take a `sqlite3.Connection`: `locations.py`, `addresses.py`, `orders.py` (order fields, the ship-to rule and the one function that writes them) and `lists.py` (the three lists and their counts). Their routes sit in one separate block of `app.py`. The frontend gains `lists.js` and `places.js`, an order card on the gift screen and a counts row on the home screen. No new dependency and no new setting.

**Tech Stack:** as the core plan.

**Spec:** `docs/superpowers/specs/2026-11-12-gift-tracker-design.md`, "Data model" and "Screens" 4 and 5. Builds on the core plan and the school plan (`2026-11-13-school-instance.md`, schema version 2). None of them is edited here.

## Decisions fixed before the build

- **Schema 3.** `locations(id, parent_id → locations ON DELETE CASCADE, name, sort_order)`; `addresses(id, label, is_home)` with a partial unique index so at most one row has `is_home = 1`, and the migration seeds one row, `Home`, so there is always exactly one; `gifts` gains `seller, order_number, ship_to_id → addresses, ship_elsewhere_ok (0/1), eta, arrived_on, ordered_by, location_id → locations ON DELETE SET NULL`. Dates are `YYYY-MM-DD` text. A v2 database keeps every row.
- **One home, always.** Making another address home moves the flag; unflagging the only home is 400; deleting the home is 409. Deleting an address that any gift ships to is 409.
- **The ancestor rule.** A location cannot be moved under itself or any of its descendants (400). Deleting a location whose subtree holds any gift is 409; otherwise the subtree goes with it.
- **Deleting needs the unlock.** Both "an address is on an order" and "a location holds gifts" would tell a locked caller whether a hidden gift uses it (the same reasoning that made deleting a person need the unlock), so `DELETE` on an address and on a location is `@unlock_required`.
- **No gift counts in the location tree.** `GET /api/locations` returns the tree only (id, parent_id, name, sort_order, path, depth). Which gifts sit where is shown only by `/lists/where`, which filters on `gifts.PROTECTED_SQL`.
- **The ship-to rule.** `POST /api/gifts/<id>/status {"status": "ordered", ...}` needs `seller`, `ship_to_id` and `eta`, taken from the body or already stored on the gift; otherwise 400 `{"error": "Ordered needs seller, eta.", "missing": ["seller", "eta"]}` and nothing changes. `ship_warning` is true when `ship_to_id` is set, that address is not home and `ship_elsewhere_ok` is false; it is computed, not stored, so changing the home address or the flag changes it at once. `POST /api/gifts` with `status: "ordered"` stays as it is (an existing test creates one without order fields), so the rule guards the act of marking ordered, not the row; the deviation is stated in the PR.
- **One function writes order fields.** `orders.update(con, gift_id, data)` validates and stores any of `seller, order_number, ship_to_id, ship_elsewhere_ok, eta, arrived_on` that are present (empty text or null clears one). `PATCH /api/gifts/<id>` and the status route call it; the agent API (#10) will too. `orders.parse` is the validation half and `gifts.set_status(con, id, status, order=None, actor=None)` adds the ordered rule, `ordered_by = actor` when the status is set to ordered, and `arrived_on = today` when it is set to arrived with no date on the gift or in the body.
- **Lists.** `GET /api/occasions/<oid>/lists/pending`: `{"gifts": [...]}`, ordered gifts by `eta` (no date last), each with `overdue` (true when `eta` is before today: a gift due today is not overdue). `/lists/to-wrap`: `{"groups": [{"person_id", "gifts": [...]}]}`, arrived gifts under each recipient (a gift for two people is in both groups) with `location_path`. `/lists/where`: `{"groups": [{"location_id", "path", "gifts": [...]}]}`, arrived or wrapped gifts by location in tree order, gifts with no place in a last group with `location_id` and `path` null. `/lists/counts`: `{"pending": n, "to_wrap": n}` counted in gifts, not rows. All four filter on `PROTECTED_SQL`; none carries `santa`. A location path is `Garage › Top shelf › Blue bin` (` › `).
- **School mode.** Hiding places are hidden, as the spec says: the four location routes and `/lists/where` answer the generic 404 (`family_only`); gift JSON carries no `location_id` or `location_path`; a body naming `location_id` is 400 "Hiding places are not used in school mode."; the lists omit location keys. Addresses, order fields, `/lists/pending`, `/lists/to-wrap` and `/lists/counts` stay available in both modes (a school order can also ship to the wrong place). The school home screen is the matrix and does not link to the lists; the lists screen is reachable by its address and shows no "Where is it?" tab.
- **Home screen.** `Not here yet N · To wrap N · Where is it?` under the `+ Idea` button, from `/lists/counts`; "Where is it?" is a link with no number, as in the spec.
- Gift JSON gains `seller, order_number, ship_to_id, ship_elsewhere_ok, eta, arrived_on, ordered_by, ship_warning` in both modes, and `location_id, location_path` in family mode only.
- Existing tests edited (stated in the PR): the two stored-schema-version assertions in `tests/test_db.py` compare with `str(db.SCHEMA_VERSION)` (approved by the coordinator); `tests/test_privacy.py::args()` gains values for `address_id` and `location_id`.

## Review Focus

1. Marking a gift ordered without seller, ship-to and expected date is 400 naming what is missing and changes nothing; a non-home ship-to carries `ship_warning` until `ship_elsewhere_ok` is set, and changing the home address moves the warning. (Task 4)
2. A location can never become its own ancestor, by a direct move or through a descendant, and a location holding a gift cannot be deleted. (Task 3)
3. While locked, none of the three lists, the counts, the location tree or any order field shows a protected gift, including a protected gift shared with an unprotected recipient, and no response contains `santa`; the hidden gift's seller, order number and place never appear. (Task 5)
4. Overdue at the date boundary: due yesterday is overdue, due today is not, a gift with no date is not and sorts last. (Task 5)
5. A v2 database migrates to v3 keeping every row with exactly one `Home` address; and in school mode the location routes and the where list are 404, gift JSON has no location keys and a body naming `location_id` is 400, while order fields still work. (Tasks 2, 3, 4)

## File map

```
db.py             + _to_v3, MIGRATIONS[3], SCHEMA_VERSION = 3
addresses.py      NEW: list_all, create, update, delete (exactly one home)
locations.py      NEW: tree, path_map, descendant_ids, create, update (ancestor rule), delete, set_gift_location
orders.py         NEW: FIELDS, parse, update (the one writer), require_ordered, ship_warning, to_json
lists.py          NEW: pending, to_wrap, where, counts
gifts.py          to_json gains the order and location keys; update() calls orders.update and
                  locations.set_gift_location; set_status(order=None, actor=None)
app.py            + gift_json drops location keys in school mode, refuse_locked_write refuses location_id in
                  school mode, the status route passes the body, one new block of routes
static/js/        + lists.js, places.js; home.js (counts row), gift.js (order card), main.js (two routes),
                  settings.js (one link)
static/style.css  + .counts, .overdue, .warn, .tabs
tests/            + test_purchase.py; test_db.py (version assertions, v3 migration), test_privacy.py (args),
                  test_school.py untouched, frontend.test.mjs extended
CLAUDE.md, README.md  the new modules, school-mode note, screens
```

---

### Task 1: This plan

- [ ] Commit `docs: plan for orders, ship-to check, hiding places and lists`

### Task 2: Schema v3

**Files:** Modify `db.py`, `tests/test_db.py` (the two stored-version assertions, and the v3 migration tests appended); no other test changes.

**Interfaces:**
- Produces: `db.SCHEMA_VERSION = 3`; `db._to_v3(con)` registered as `MIGRATIONS[3]` on its own line under the v2 entry; `SCHEMA` (version 1) is not edited. `_to_v3` creates `locations`, `addresses` (+ index `locations_parent`, unique partial index `addresses_one_home ON addresses(is_home) WHERE is_home = 1`), inserts `(label 'Home', is_home 1)`, then `ALTER TABLE gifts ADD COLUMN` for `seller TEXT, order_number TEXT, ship_to_id INTEGER REFERENCES addresses(id), ship_elsewhere_ok INTEGER NOT NULL DEFAULT 0, eta TEXT, arrived_on TEXT, ordered_by TEXT, location_id INTEGER REFERENCES locations(id) ON DELETE SET NULL`.

- [ ] **Step 1: Write the failing tests**

```python
def test_a_v2_database_migrates_to_v3_keeping_its_rows(tmp_path):
    c = _v2_database(tmp_path / "t.db")            # v1 schema, then MIGRATIONS[2], meta version '2', a gift with a done recipient
    db.migrate(c)
    assert c.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()[0] == str(db.SCHEMA_VERSION) == "3"
    assert tuple(c.execute("SELECT title, status, santa, seller, order_number, ship_to_id, ship_elsewhere_ok, eta, "
                           "arrived_on, ordered_by, location_id FROM gifts").fetchone()) == ("Card", "given", 1, None, None, None, 0, None, None, None, None)
    assert [tuple(r) for r in c.execute("SELECT gift_id, person_id, detail, done FROM gift_recipients")] == [(1, 1, "$35", 1)]
    assert [tuple(r) for r in c.execute("SELECT label, is_home FROM addresses")] == [("Home", 1)]
    assert c.execute("SELECT COUNT(*) FROM locations").fetchone()[0] == 0
    db.migrate(c)                                   # again: nothing changes
    assert c.execute("SELECT COUNT(*) FROM addresses").fetchone()[0] == 1
def test_a_fresh_database_has_the_v3_tables_and_columns(con): PRAGMA table_info for locations, addresses, the eight gift columns (ship_elsewhere_ok NOT NULL default 0)
def test_the_migrated_gift_columns_match_a_fresh_database(tmp_path, con): same shape() comparison as v2's
def test_a_second_home_address_is_refused_by_the_database(con): INSERT INTO addresses (label, is_home) VALUES ('X', 1) -> sqlite3.IntegrityError; is_home 0 twice is fine
def test_deleting_a_location_unplaces_its_gifts(con): insert a location and a gift in it, DELETE the location, gifts.location_id is NULL and the gift remains
def test_a_location_deletes_its_children(con): parent and child, DELETE parent -> no rows
```

- [ ] **Step 2: Run; expect failures.** **Step 3: Implement.** **Step 4: Run the whole suite.**
- [ ] **Step 5: Commit** `feat(db): schema v3 with locations, addresses and order fields`

### Task 3: Addresses and the location tree

**Files:** Create `addresses.py`, `locations.py`; modify `app.py` (the routes block and the `family_only` wrapping), `tests/test_privacy.py` (`args()`: `address_id`, `location_id`), `tests/test_school.py` untouched; Test: `tests/test_purchase.py`.

**Interfaces:**
- Produces `addresses.list_all(con) -> [{id, label, is_home}]` (home first, then by label, id); `create(con, data) -> dict` (`label` 1 to 100 characters, `is_home` optional); `update(con, id, data) -> dict`; `delete(con, id) -> None` (409 for the home, 409 when a gift ships there).
- Produces `locations.SEP = " › "`; `locations.tree(con) -> [{id, parent_id, name, sort_order, path, depth}]` depth-first, siblings by `(sort_order, lower(name), id)`; `path_map(con) -> {id: path}`; `descendant_ids(con, id) -> set` (includes `id`); `create(con, data)`, `update(con, id, data)`, `delete(con, id)`; `set_gift_location(con, gift_id, location_id | None)`.
- Routes: `GET, POST /api/addresses`; `PATCH, DELETE /api/addresses/<int:address_id>`; `GET, POST /api/locations`, `PATCH, DELETE /api/locations/<int:location_id>`. The four location routes are `family_only`; both `DELETE`s are `@unlock_required`. `PATCH`/`POST` bodies go through `json_body()` and `text()`; `parent_id` is a whole number or null; a missing parent is 400.

- [ ] **Step 1: Write the failing tests**

```python
def test_exactly_one_home_address(client):
    a = client.get("/api/addresses", headers=auth()).get_json()
    assert a == [{"id": 1, "label": "Home", "is_home": True}]
    gp = post("/api/addresses", {"label": "Grandparents' house"})                  # 201, is_home false
    assert homes() == [1]
    patch(f"/api/addresses/{gp['id']}", {"is_home": True}); assert homes() == [gp["id"]]
    r = patch(f"/api/addresses/{gp['id']}", {"is_home": False}); assert r.status_code == 400 and homes() == [gp["id"]]
    assert delete(f"/api/addresses/{gp['id']}").status_code in (409, 423)
def test_an_address_on_an_order_cannot_be_deleted(client, ids, unlock): ...      # 409; unused one is 200
def test_location_paths_use_the_arrow(client): Garage > Top shelf > Blue bin -> path "Garage › Top shelf › Blue bin", depth 2, tree order Garage, Top shelf, Blue bin
def test_a_location_cannot_be_its_own_ancestor(client):
    garage, shelf, bin_ = chain
    assert patch(f"/api/locations/{garage}", {"parent_id": garage}).status_code == 400      # itself
    assert patch(f"/api/locations/{garage}", {"parent_id": bin_}).status_code == 400        # a descendant
    assert patch(f"/api/locations/{shelf}", {"parent_id": bin_}).status_code == 400
    assert parents() == {garage: None, shelf: garage, bin_: shelf}                          # nothing moved
    assert patch(f"/api/locations/{bin_}", {"parent_id": None}).status_code == 200          # a legal move
def test_a_missing_parent_and_bad_names_are_400(client): parent 999 -> 400; name "" / 101 chars / 5 -> 400; parent_id "1" -> 400
def test_a_location_holding_a_gift_cannot_be_deleted(client, ids, unlock):
    # gift in Blue bin; DELETE Garage -> 409 and all three rows stay; move the gift out, DELETE Garage -> 200 and the subtree is gone
def test_deleting_a_location_or_address_needs_the_unlock(client, ids): 423 {"error": "Locked", "locked": true} for both, for a real and a missing id
def test_location_routes_are_404_in_school_mode(client, school): every location route and /lists/where answer the generic 404
```

- [ ] **Step 2: Run; expect failures.** **Step 3: Implement.** **Step 4: Run the whole suite**, including `tests/test_privacy.py` and `tests/test_identity.py` (new routes are in the url_map walks).
- [ ] **Step 5: Commit** `feat(api): addresses with one home, and the location tree`

### Task 4: Order fields and the ship-to check

**Files:** Create `orders.py`; modify `gifts.py`, `app.py` (gift_json, refuse_locked_write, status route); Test: `tests/test_purchase.py`.

**Interfaces:**
- Produces `orders.FIELDS = ("seller", "order_number", "ship_to_id", "ship_elsewhere_ok", "eta", "arrived_on")`; `orders.parse(con, data) -> dict` (column -> value for the keys present: text up to 200 characters for `seller`, 100 for `order_number`; `ship_to_id` an existing address or null; `ship_elsewhere_ok` true/false; `eta` and `arrived_on` real `YYYY-MM-DD` dates; empty text and null clear); `orders.update(con, gift_id, data) -> None` (parse, write, touch `updated_at`; the writer the agent API calls); `orders.require_ordered(merged: dict) -> None` (400 with `missing`); `orders.ship_warning(con, row) -> bool`; `orders.to_json(con, row) -> dict` (the seven keys the gift JSON gains).
- Changes: `gifts.set_status(con, gift_id, status, order=None, actor=None)`; `gifts.update` accepts the order fields and `location_id` (school mode: refused in app.py); `gifts.to_json` adds `orders.to_json` and, via `locations.path_map`, `location_id` and `location_path`.

- [ ] **Step 1: Write the failing tests**

```python
def test_ordered_needs_seller_ship_to_and_eta(client, ids):
    g = mk_gift(client, ids.o, "Camera", to=[ids.jordan], frm=[ids.alex], status="decided")
    for body in ({}, {"seller": "Camera shop"}, {"seller": "Camera shop", "ship_to_id": 1}, {"eta": "2026-12-09", "ship_to_id": 1}):
        r = status(g, "ordered", **body)
        assert r.status_code == 400 and set(r.get_json()["missing"]) <= {"seller", "ship_to_id", "eta"}
    assert status(g, "ordered").get_json()["missing"] == ["seller", "ship_to_id", "eta"]
    assert con_rows("SELECT status, seller, eta, ordered_by FROM gifts WHERE id = ?", g) == [("decided", None, None, None)]   # nothing changed
    r = status(g, "ordered", seller="Camera shop", ship_to_id=1, eta="2026-12-09", order_number="1234")
    j = r.get_json(); assert (r.status_code, j["status"], j["seller"], j["order_number"], j["eta"], j["ship_warning"], j["ordered_by"]) == (200, "ordered", "Camera shop", "1234", "2026-12-09", False, "Alex")
def test_fields_stored_earlier_count(client, ids): PATCH the three fields, then POST {"status": "ordered"} alone -> 200
def test_ship_to_elsewhere_warns_until_marked_as_meant(client, ids):
    gp = address("Grandparents' house"); j = ordered_gift(ship_to_id=gp)
    assert j["ship_warning"] is True
    assert patch_gift(g, {"ship_elsewhere_ok": True})["ship_warning"] is False
    assert patch_gift(g, {"ship_elsewhere_ok": False, "ship_to_id": 1})["ship_warning"] is False      # home
    make_home(gp)                                                                                    # the warning follows the home address
    assert get_gift(g)["ship_warning"] is True
def test_no_ship_to_means_no_warning(client, ids): ship_warning false and ship_to_id null on a plain gift
def test_order_field_validation(client, ids): eta "12/9/2026", "2026-02-30", 5 -> 400; ship_to_id 999 -> 400, "1" -> 400; seller 201 chars -> 400; ship_elsewhere_ok "yes" -> 400; seller "" clears; a 400 leaves the other fields in the same body unwritten
def test_arrived_stamps_today_unless_a_date_is_given(client, ids, freeze_today): store purchase: decided -> arrived gives arrived_on = frozen day; with {"arrived_on": "2026-12-08"} keeps that; a later status change does not overwrite
def test_orders_update_is_the_one_writer(con, ids): calling orders.update(con, g, {...}) directly stores the fields; orders.update(con, 999, {}) -> ApiError 404
def test_a_gift_for_a_theirs_track_cannot_be_ordered(client, ids): status "ordered" on a gift from Casey -> 400 (existing track rule)
def test_gift_json_has_the_order_keys_and_school_mode_has_no_location_keys(client, ids, school): keys present; "location_id" and "location_path" absent; PATCH {"location_id": 1} -> 400 "Hiding places are not used in school mode."; order fields still PATCH fine
def test_a_gift_can_be_placed_and_unplaced(client, ids): location_id set -> location_path "Garage › Top shelf"; null clears; 999 -> 400
```

- [ ] **Step 2: Run; expect failures.** **Step 3: Implement.** **Step 4: Run the whole suite.**
- [ ] **Step 5: Commit** `feat(orders): order fields, the ship-to check and hiding places on gifts`

### Task 5: The three lists and their counts

**Files:** Create `lists.py`; modify `app.py` (routes); Test: `tests/test_purchase.py`.

**Interfaces:**
- Produces `lists.pending`, `lists.to_wrap`, `lists.where`, `lists.counts`, each `(con, occasion_id, *, unlocked)`; 404 for a missing occasion. Shapes as in the decisions. A list item is `{id, title, status, recipient_ids, seller, order_number, eta, arrived_on, ship_to_id, ship_warning, overdue, location_id, location_path}` (the two location keys in family mode only); never `santa`.
- Routes: `GET /api/occasions/<oid>/lists/pending|to-wrap|counts`; `GET .../lists/where` (`family_only`).

- [ ] **Step 1: Write the failing tests**

```python
def test_pending_is_ordered_gifts_by_eta_with_overdue(client, ids, freeze_today):
    freeze_today("2026-12-10")
    # four ordered gifts: eta 2026-12-12, 2026-12-09 (yesterday), 2026-12-10 (today), none (created through the API as ordered), and a decided and an arrived gift
    j = get(f"/api/occasions/{ids.o}/lists/pending")
    assert [(g["title"], g["eta"], g["overdue"]) for g in j["gifts"]] == [("Yesterday", "2026-12-09", True), ("Today", "2026-12-10", False), ("Later", "2026-12-12", False), ("No date", None, False)]
def test_overdue_flips_at_midnight(client, ids, freeze_today): eta 2026-12-10: today 2026-12-10 -> False, 2026-12-11 -> True
def test_to_wrap_groups_arrived_gifts_by_recipient_with_the_place(...): two recipients -> in both groups; groups follow the people order; arrived only (not wrapped); location_path present, None when unplaced
def test_where_groups_by_place_in_tree_order(...): arrived and wrapped, not ordered or given; "Garage › Top shelf › Blue bin" before "Hall closet"; unplaced last with path None
def test_counts_are_gifts_not_rows(...): pending 2, to_wrap 1 for a two-recipient gift
def test_the_lists_hide_protected_gifts_while_locked(client, ids, unlock):
    # a protected gift (kid1), a gift shared by kid2 and jordan, each ordered with seller "ZZ-SELLER", order_number "ZZ-ORDER", arrived and placed in "ZZ-PLACE"; one visible control
    for path in (pending, to-wrap, where, counts, /api/locations, /api/addresses): no marker in the text, "santa" not in text, only the control is listed, counts only count the control
    unlock(); the same requests now list the protected gifts (the walk is not blind)
def test_the_lists_404_for_a_missing_occasion(client)
def test_where_is_404_in_school_mode_and_the_other_lists_have_no_location_keys(client, school)
```
`tests/test_privacy.py` must still pass: its walk reaches `lists/pending|to-wrap|where|counts` with the shared "arrived" ZZ-SECRET gift in the occasion, so a leak there fails an existing test too.

- [ ] **Step 2: Run; expect failures.** **Step 3: Implement.** **Step 4: Run the whole suite.**
- [ ] **Step 5: Commit** `feat(api): not-here-yet, to-wrap and where-is-it lists`

### Task 6: Lists screen and home counts

**Files:** Create `static/js/lists.js`; modify `static/js/home.js` (additive: `renderCounts`, optional fourth argument), `static/js/main.js` (routes), `static/style.css`; Test: `tests/frontend.test.mjs` appended.

**Interfaces:**
- `lists.js` exports `renderTabs(active, school)`, `renderPending(data, people)`, `renderToWrap(data, people)`, `renderWhere(data, people)`, `render(ctx)` for `#/lists/<pending|to-wrap|where>` (default pending; `where` in school mode draws a notice).
- `home.js` exports `renderCounts(counts)`: `<p class="counts"><a href="#/lists/pending">Not here yet N</a> · <a href="#/lists/to-wrap">To wrap N</a> · <a href="#/lists/where">Where is it?</a></p>`; `renderHome(summary, occasions, anyProtected, counts)` shows it only when `counts` is given (the existing three-argument calls are unchanged).

- [ ] **Step 1: Write the failing tests** (node): counts row text and links; a title, seller and location containing `<img onerror>` come out escaped in all three lists; an overdue row has class `overdue` and the word "overdue"; `ship_warning` shows a warning; the tab bar marks the active tab and has no "Where is it?" in school mode; the screen requests the right URL for each tab and draws "Nothing on its way." for an empty list.
- [ ] **Step 2: Run; expect failures.** **Step 3: Implement.** **Step 4: gate.**
- [ ] **Step 5: Commit** `feat(ui): the three lists and the counts on home`

### Task 7: Order card, hiding place and places screen

**Files:** Create `static/js/places.js`; modify `static/js/gift.js` (additive: `orderPayload`, `statusBody`, `renderOrder`, optional fifth argument of `renderPage`), `static/js/main.js`, `static/js/settings.js` (one link), `static/style.css`; Test: `tests/frontend.test.mjs` appended.

**Interfaces:**
- `gift.js`: `orderPayload(f, school) -> body` (`ship_to_id` null for the empty choice, `location_id` omitted in school mode); `statusBody(status, f, school)` (`{status}` plus the order fields only for `ordered`); `renderOrder(gift, addresses, locations, school)`; `renderPage(gift, people, occasionName, unlocked, places = null)` draws the order card only when `places` is given and the gift is on the ours track.
- `places.js`: `parentChoices(locations, id)` (everything except the location and its descendants), `renderAddresses(list)`, `renderLocations(list, school)`, `render(ctx)` for `#/places` (addresses always, locations in family mode).

- [ ] **Step 1: Write the failing tests** (node): `orderPayload` trims, nulls the empty ship-to, leaves out `location_id` in school mode; `statusBody("ordered", ...)` carries the fields and `statusBody("wrapped", ...)` is `{status: "wrapped"}`; the order card escapes seller, order number and location path, shows the ship warning sentence for a non-home ship-to, offers only addresses and (family) locations from its arguments, and is absent for a theirs-track gift; `parentChoices` excludes self and descendants; the gift screen's Mark-ordered tap posts the form's values to `/status` and shows the server's 400 sentence.
- [ ] **Step 2: Run; expect failures.** **Step 3: Implement.** **Step 4: gate.**
- [ ] **Step 5: Commit** `feat(ui): order card, hiding place picker and places screen`

### Task 8: Docs

**Files:** Modify `CLAUDE.md` (file structure; the rules a future change must keep: the order writer, lists filter on `PROTECTED_SQL`, deletes need the unlock, school-mode location behaviour), `README.md` (screens and routes).

- [ ] **Step 1: Edit.** **Step 2: Run the full gate.**
- [ ] **Step 3: Commit** `docs: orders, locations and lists`
