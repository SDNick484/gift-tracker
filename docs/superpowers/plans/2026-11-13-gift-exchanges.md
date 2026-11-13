# Gift Exchanges (Plan 5 of 6) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Track the gift exchanges of an occasion (Secret Santa, Favorite Things, a staff exchange): name, budget, date, which member of this household drew whom, and the gift for each draw, with a gift over the budget flagged. Only this household's side is held, never the full pairing table.

**Architecture:** Schema version 5 adds `exchanges`, `exchange_draws` and `gifts.exchange_id`. Route-free logic goes in one new module, `exchanges.py` (every function takes a `sqlite3.Connection`). The lock rule is one change to the shared predicate: `gifts.PROTECTED_SQL` (and `gifts.is_protected`) also cover a gift linked to an exchange whose giver is a protected person, so every existing query, list, total and the agent API hide such a gift with no further edit. Exchange routes sit in one separate block of `app.py`. The frontend gains `exchanges.js` and a section on the home screen. No new dependency and no new setting. No agent API or MCP surface: the issue does not ask for one.

**Tech Stack:** as the core plan.

**Spec:** `docs/superpowers/specs/2026-11-12-gift-tracker-design.md`, "Data model" and "Screens" 1. Builds on the core, school, after-purchase and agent plans (none is edited here).

## Decisions fixed before the build

- **Schema 5** (assigned by the queue; the issue says 6). `exchanges(id, occasion_id → occasions ON DELETE CASCADE, name, budget_cents, date)`, `exchange_draws(exchange_id → exchanges ON DELETE CASCADE, drawer_id → people, recipient_id → people, PRIMARY KEY (exchange_id, drawer_id))`, and `gifts.exchange_id → exchanges` (default NULL, no cascade: an exchange with gifts cannot be deleted). `date` and `budget_cents` may be NULL. `SCHEMA` (version 1) is not edited.
- **Who may draw.** A drawer is an owner (a user) or a protected person; anyone else is 400. The recipient is any existing person other than the drawer. A drawer has at most one draw per exchange (the primary key). In school mode no person is protected, so only an owner may draw.
- **One gift per draw.** `gifts.exchange_id` is set only when the gift has exactly one giver and one recipient and that pair is a draw of that exchange, in the gift's own occasion (400 otherwise); a second gift for the same draw is 409. The gift's giver is the drawer and its recipient the person drawn. A gift whose giver is not an owner is on the "theirs" track (told / confirmed / given), so an exchange gift from a protected child is not counted in spent or planned money; this follows the issue's "the giver is the drawer" literally and is flagged in the PR.
- **The lock.** A draw is protected when its drawer or its recipient is protected; a gift linked to an exchange is protected when any recipient (as always) or any giver is protected. The test is made at read time from `people.protected`, never stored, so protecting a person later hides their draws at once. While locked a protected draw does not exist: it is absent from the exchange routes, the home summary and every total, and `PUT` / `DELETE` on a draw whose drawer or new recipient is protected is 423 (that fact is public: people are listed while locked), on a hidden draw whose drawer is not protected 404. Writing a gift with `exchange_id` and a protected giver is 423. Deleting an exchange needs the unlock and is 409 while any gift is linked (a hidden gift linked to it would otherwise be unlinked, and so un-hidden, or its existence told by the answer). Residual leak, accepted: a locked `PUT` to a drawer whose existing draw is hidden because of a protected recipient answers 423, which tells a locked owner that this drawer has some protected draw (not whom, not the gift).
- **Over budget.** `over_budget` is true for a draw when its gift has a price, the exchange has a budget and the price is above it. Flagged, never refused. A gift with no price, or an exchange with no budget, is never over.
- **Home summary.** `GET /api/occasions/<id>/summary` gains `exchanges: [{id, name, budget_cents, date, draws: [{drawer_id, recipient_id, gift_id, gift_title, gift_status, price_cents, over_budget}]}]`, protected draws removed while locked (the exchange itself, with its name, budget and date, is not protected data). Draws are in drawer then recipient order; `gift_*` are null when no gift exists yet. The same list is `GET /api/occasions/<id>/exchanges`.
- **Carry-forward.** Copying an occasion copies each exchange's name and budget into the new occasion with no date and no draws; protected or not, as nothing private is copied.
- **School mode.** Exchanges exist in both modes (a staff gift exchange is a real use) and no route is mode-specific. School has no lock (`app.unlocked()` is true) and no protected person is reported, so nothing in an exchange is hidden there and only owners draw; the tests pin this.
- Gift JSON gains `exchange_id` in both modes. No `santa` appears anywhere new.
- Existing tests edited (stated in the PR): `tests/test_db.py` v4 migration test drops the `== "4"` literal; `tests/test_privacy.py::args()` gains `exchange_id`.

## Review Focus

1. A locked request never sees a protected draw or its gift: not in the exchange list, the home summary, person gift lists, history, totals or the agent API, whether the drawer or the recipient is the protected one, and after a person is protected later. (Tasks 3, 4, 5)
2. A protected draw is immutable while locked: `PUT`, `DELETE` and a gift write naming it answer 404 or 423 and change nothing; deleting an exchange needs the unlock and is refused while gifts are linked. (Tasks 3, 4)
3. A gift can be linked only to a real draw of its own occasion, one gift per draw, and the link survives edits only while giver and recipient still match the draw. (Task 4)
4. Over budget: above is flagged, equal is not, no price or no budget is not, and the gift is saved either way. (Task 5)
5. A v4 database migrates to v5 keeping every row; copying an occasion copies exchange names and budgets and no draws; in school mode exchanges work, only owners draw and nothing is locked. (Tasks 2, 6, 7)

## File map

```
db.py             + _to_v5, MIGRATIONS[5], SCHEMA_VERSION = 5
exchanges.py      NEW: get, create, update, delete, set_draw, remove_draw, check_gift, list_for_occasion
gifts.py          PROTECTED_SQL and is_protected cover protected givers of exchange gifts; to_json gains exchange_id;
                  create/update validate exchange_id through exchanges.check_gift
occasions.py      create(copy_from) also copies exchanges
people.py         delete_person refuses a person in a draw
budgets.py        summary gains `exchanges`
app.py            refuse_locked_write for exchange gifts; one block of exchange routes
static/js/        + exchanges.js; home.js (exchanges section), main.js (one route), settings.js (one link)
static/style.css  + .exchange rules
tests/            + test_exchanges.py; test_db.py (v5 migration), test_privacy.py (args), frontend.test.mjs extended
CLAUDE.md, README.md  the module, the lock rule, school-mode note, screens
```

---

### Task 1: This plan

- [ ] Commit `docs: plan for gift exchanges`

### Task 2: Schema v5

**Files:** Modify `db.py`, `tests/test_db.py` (the v4 `== "4"` literal; v5 tests appended).

**Interfaces:**
- Produces `db.SCHEMA_VERSION = 5` and `db._to_v5(con)` registered as `MIGRATIONS[5]` on its own line under the v4 entry. It creates `exchanges`, `exchange_draws`, index `exchange_draws_recipient`, and `ALTER TABLE gifts ADD COLUMN exchange_id INTEGER REFERENCES exchanges(id)`.

- [ ] **Step 1: Write the failing tests**

```python
def test_a_v4_database_migrates_to_v5_keeping_its_rows(tmp_path):
    c = _v4_database(tmp_path / "t.db")          # v1..v4 steps, version '4', one ordered gift
    db.migrate(c)
    assert c.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()[0] == str(db.SCHEMA_VERSION)
    assert db.SCHEMA_VERSION >= 5
    assert tuple(c.execute("SELECT title, status, seller, exchange_id FROM gifts").fetchone()) == ("Card", "ordered", "Shop", None)
    assert c.execute("SELECT COUNT(*) FROM exchanges").fetchone()[0] == 0
    db.migrate(c)                                 # again: nothing changes
    assert c.execute("SELECT COUNT(*) FROM gifts").fetchone()[0] == 1
def test_a_fresh_database_has_the_v5_tables(con): column names of exchanges and exchange_draws; gifts.exchange_id is INTEGER, nullable, default None
def test_the_migrated_tables_match_a_fresh_database(tmp_path, con): _shape of gifts, exchanges, exchange_draws equal
def test_deleting_an_exchange_deletes_its_draws_but_not_while_a_gift_uses_it(con): draws cascade; a gift row with exchange_id makes DELETE raise sqlite3.IntegrityError
def test_deleting_an_occasion_deletes_its_exchanges_draws_and_gifts(con)
```

- [ ] **Step 2: Run; expect failures.** **Step 3: Implement.** **Step 4: Run the whole suite.**
- [ ] **Step 5: Commit** `feat(db): schema v5 with gift exchanges and their draws`

### Task 3: Exchanges, draws and the lock

**Files:** Create `exchanges.py`; modify `gifts.py` (the predicate), `people.py` (`delete_person`), `app.py` (routes), `tests/test_privacy.py` (`args()`); Test: `tests/test_exchanges.py`.

**Interfaces:**
- Produces `exchanges.get(con, id)` (404), `create(con, occasion_id, data) -> dict`, `update(con, id, data) -> dict`, `delete(con, id)` (409 while a gift is linked), `set_draw(con, exchange_id, drawer_id, data, *, unlocked) -> dict`, `remove_draw(con, exchange_id, drawer_id, *, unlocked)`, `list_for_occasion(con, occasion_id, *, unlocked) -> list[dict]`. Bodies: `{name, budget_cents?, date?}`; `{recipient_id}`.
- Produces `gifts.PROTECTED_SQL`, now `(EXISTS recipient protected OR (g.exchange_id IS NOT NULL AND EXISTS giver protected))`, wrapped in parentheses so `NOT {PROTECTED_SQL}` keeps meaning; `gifts.is_protected` the same rule.
- Routes: `GET, POST /api/occasions/<int:occasion_id>/exchanges`; `PATCH, DELETE /api/exchanges/<int:exchange_id>` (`DELETE` is `@unlock_required`); `PUT, DELETE /api/exchanges/<int:exchange_id>/draws/<int:person_id>` (the person is the drawer). All go through `json_body()` and `text()`; none is mode-specific.

- [ ] **Step 1: Write the failing tests**

```python
def test_create_and_list_an_exchange(client, ids):
    r = post(f"/api/occasions/{ids.o}/exchanges", {"name": "Secret Santa", "budget_cents": 2500, "date": "2026-12-20"})
    assert (r.status_code, r.get_json()) == (201, {"id": 1, "name": "Secret Santa", "budget_cents": 2500, "date": "2026-12-20", "draws": []})
    assert get(f"/api/occasions/{ids.o}/exchanges") == [that]
def test_bad_exchange_bodies_are_400(...): empty name, 101 chars, budget -1 / 1.5 / True / "5", date "2026-13-01", date 5, unknown occasion 404
def test_a_draw_is_an_owner_or_a_protected_person(client, ids): alex and kid1 drew; jordan as drawer 400; drawer unknown 404; recipient unknown 400; recipient == drawer 400
def test_a_draw_can_be_replaced_and_removed(client, ids)
def test_a_locked_request_sees_no_protected_draw(client, ids): draws alex->jordan (visible), alex->kid1 (protected recipient), kid2->riley (protected drawer); locked list shows exactly the first; unlocked shows three
def test_a_person_protected_later_hides_their_draws_at_once(client, ids)
def test_a_protected_draw_is_immutable_while_locked(client, ids): PUT with drawer kid2, or recipient kid1, is 423; the PUT on alex (hidden draw, protected recipient) is 423; DELETE of kid2's draw 423; DELETE of alex's hidden draw 404 = DELETE of a draw that does not exist; the rows are unchanged
def test_deleting_an_exchange_needs_the_unlock_and_no_gifts(client, ids, unlock): locked 423 body {"error": "Locked", "locked": True}; unlocked 200 with draws gone; 409 with a linked gift
def test_a_person_in_a_draw_cannot_be_deleted(client, ids, unlock)
def test_exchange_routes_take_part_in_the_privacy_walks(): args() has exchange_id
```

- [ ] **Step 2: Run; expect failures.** **Step 3: Implement.** **Step 4: Run the whole suite, including both privacy walks.**
- [ ] **Step 5: Commit** `feat(exchanges): exchanges, draws and the lock on protected draws`

### Task 4: A gift for a draw

**Files:** Modify `gifts.py` (`to_json`, `create`, `update`), `app.py` (`refuse_locked_write`, `gifts_update`); Test: `tests/test_exchanges.py`.

**Interfaces:**
- Produces `exchanges.exchange_id_of(con, data)` (null, or an existing exchange's id, else 400) and `exchanges.check_gift(con, gift_id)`, run inside the writer's transaction after the givers and recipients are stored: the gift's occasion is the exchange's, it has exactly one giver and one recipient forming a draw of the exchange (400), and no other gift is linked to the draw (409). `exchange_id: null` unlinks. `gifts.create` and `gifts.update` accept `exchange_id`; gift JSON carries it (null when none).
- `app.refuse_locked_exchange_write(data, gift_id=None)`, called beside `refuse_locked_write`: while locked, a gift that is or would be an exchange gift (the body names `exchange_id`, or the gift being changed has one) with a protected giver (from the body, else the stored ones) is 423, before the exchange is looked at. `agent.create_gift` refuses `exchange_id` (400): no agent access.

- [ ] **Step 1: Write the failing tests**

```python
def test_a_gift_for_a_draw(client, ids): POST /api/gifts {exchange_id, giver_ids [alex], recipient_ids [jordan], price "20"} is 201 with exchange_id set and track "ours"
def test_a_gift_must_match_a_draw_of_its_own_occasion(client, ids): wrong recipient 400; two recipients 400; no giver 400; another occasion's exchange 400; unknown exchange 400; bool/str exchange_id 400
def test_one_gift_per_draw(client, ids): the second is 409 and the first is unchanged; unlinking the first (exchange_id null) lets a second be linked
def test_editing_a_linked_gift_cannot_break_the_draw(client, ids): PATCH recipient_ids to riley 400 and the rows are unchanged (rolled back); PATCH title works
def test_a_draw_with_a_gift_cannot_be_removed_or_redrawn(client, ids): DELETE and PUT-to-another-recipient are 409
def test_a_gift_from_a_protected_drawer_is_hidden_while_locked(client, ids, unlock): kid1 draws jordan; unlocked, a gift from kid1 to jordan exists; locked: GET /api/gifts/<id> is the 404 of a missing gift, PATCH/POST status/DELETE 404, absent from jordan's list, history, the summary headline, count and totals, the three lists and counts; creating it while locked is 423; a visible gift for jordan from alex still shows and counts
def test_a_gift_for_a_protected_recipient_is_still_hidden(client, ids, unlock)
def test_an_agent_token_below_full_never_sees_a_protected_draw_gift(client, ids, unlock): delivery, shop and a locked full token list none and GET it 404; an unlocked full token sees it
def test_a_locked_patch_cannot_move_a_visible_exchange_gift_onto_a_protected_giver(client, ids): 423, rows unchanged
```

- [ ] **Step 2: Run; expect failures.** **Step 3: Implement.** **Step 4: Run the whole suite.**
- [ ] **Step 5: Commit** `feat(exchanges): link a gift to a draw, hidden with a protected drawer`

### Task 5: The home summary and the over-budget flag

**Files:** Modify `budgets.py` (`summary`), `exchanges.py` (`list_for_occasion`); Test: `tests/test_exchanges.py`.

**Interfaces:** `summary(...)["exchanges"]` as in the decisions; `over_budget(price_cents, budget_cents) -> bool`.

- [ ] **Step 1: Write the failing tests**

```python
def test_over_budget_boundaries(): (2501, 2500) True; (2500, 2500) False; (None, 2500) False; (9999, None) False; (0, 0) False
def test_the_summary_lists_each_exchange_with_its_draws(client, ids): exact dict for one exchange, one draw with a gift at 3000 against 2500: over_budget True, gift_status "idea"; a draw with no gift has gift_id None, over_budget False
def test_an_over_budget_gift_is_saved(client, ids): the POST is 201 and the stored price is 3000
def test_the_summary_exchanges_hide_protected_draws_while_locked(client, ids, unlock): the locked list has the visible draw only, the unlocked list all; the locked text contains neither a protected person's gift title nor "santa"
def test_exchange_gifts_count_in_money_for_unprotected_recipients(client, ids): an ours gift at $20 for jordan shows in jordan's planned_cents
```

- [ ] **Step 2: Run; expect failures.** **Step 3: Implement.** **Step 4: Run the whole suite.**
- [ ] **Step 5: Commit** `feat(exchanges): exchanges on the home summary with the over-budget flag`

### Task 6: Carry-forward

**Files:** Modify `occasions.py` (`create`); Test: `tests/test_exchanges.py`.

- [ ] **Step 1: Write the failing tests**

```python
def test_copying_an_occasion_copies_exchange_names_and_budgets_only(client, ids): the copy has "Secret Santa" 2500, date None, draws []; the source keeps its draws and gifts; a copy of an occasion with no exchanges has none
def test_copying_does_not_leak_a_protected_draw(client, ids): the copy's locked and unlocked lists both have no draws
```

- [ ] **Step 2: Run; expect failures.** **Step 3: Implement.** **Step 4: Run the whole suite.**
- [ ] **Step 5: Commit** `feat(exchanges): carry exchange names and budgets into a copied occasion`

### Task 7: School mode

**Files:** Test only (`tests/test_exchanges.py`), plus a fix if a test finds one.

- [ ] **Step 1: Write the tests** (family mode untouched; `monkeypatch.setattr(settings, "MODE", "school")`):

```python
def test_exchanges_work_in_school_mode_and_nothing_is_locked(client, ids): a staff exchange, an owner's draw of a staff member, a gift for it, the summary list: all 200/201 with no unlock; no "santa", no "locked" key on /api/me
def test_in_school_mode_only_owners_draw(client, ids): drawer jordan 400; a person whose stored protected flag is 1 is still just a person: drawing as them is 400 and a draw from an owner to them is visible
def test_no_exchange_route_is_mode_specific(): no rule whose endpoint starts with "exchange" carries mode_only; tests/test_school.py pins are unchanged
```

- [ ] **Step 2: Run; make them pass.** **Step 3: Run the whole suite.**
- [ ] **Step 4: Commit** `test(exchanges): exchanges in school mode`

### Task 8: Screen

**Files:** Create `static/js/exchanges.js`; modify `static/js/home.js`, `main.js`, `settings.js`, `static/style.css`, `tests/frontend.test.mjs`.

**Interfaces:** `renderExchangeCards(list, people)` and `drawLine(draw, people)` (pure, escaped markup); `exchangePayload(form)`; the home screen's `renderExchangesSection(list)` (links, no forms). The screen is `#/exchanges`: a card per exchange with its budget and date, a draw row per drawer (drawer, recipient select, gift title, price, status, an "over budget" badge), an add-gift form per draw without a gift, and a form to add an exchange. While locked, protected people are not offered (`choosablePeople()`).

- [ ] Tests (node): `drawLine` shows the over-budget badge only when `over_budget`; every name and title goes through `esc`; `exchangePayload` turns dollars into cents and blank into null and refuses a bad amount; the home section renders nothing for an empty list and links to `#/exchanges`; no `style=` and no inline script in the new markup.
- [ ] **Commit** `feat(ui): exchanges screen and the exchanges on home`

### Task 9: Docs

- [ ] `CLAUDE.md` section "Gift exchanges", `README.md` screens, full gate in both venvs. **Commit** `docs: gift exchanges`
