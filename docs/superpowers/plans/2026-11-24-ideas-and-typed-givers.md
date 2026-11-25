# Ideas and Typed Givers Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ideas that stand apart from occasions, tagged with several people and taken per person; a share flag and a
copyable list of open shared ideas; and a typed giver name on a gift for one-off givers.

**Architecture:** Schema version 8 adds `ideas`, `idea_people` and `gifts.giver_name`. Idea logic is a new
route-free module `ideas.py`; taking an idea creates the gift through `gifts.create`, which links the copy inside its
own transaction. A typed name joins the giver ids everywhere the track, a label or the wishlist `by` is worked out
(`gifts.track` gains a `giver_name` argument). The wishlist pull-over makes an idea instead of a gift. The frontend
gains `ideas.js` and changes `idea.js`, `gift.js`, `board.js`, `person.js` and `home.js`.

**Tech Stack:** Flask + SQLite, native ES modules, pytest, `node --test`. No new dependency, no new setting.

**Spec:** `docs/superpowers/specs/2026-11-24-ideas-and-typed-givers-design.md`. Read it first; this plan argues from it.

## Global Constraints

- Every rule in `CLAUDE.md` holds; the privacy rule under the idle lock most of all.
- `SCHEMA_VERSION = 8`; `SCHEMA` (version 1) and earlier migrations are not edited.
- Typed name: trimmed, 1 to 80 characters (`wishlist.LABEL_MAX`); empty or `null` clears it.
- The share flag defaults to off. The copied list carries title, store and link only.
- Ideas, the share list, giver-name suggestions, take, back-to-ideas and move-to-ideas are **family-only**
  (`family_only`), and a body naming `giver_name` in school mode is 400 `"Typed givers are not used in school mode."`.
  Gift JSON in school mode has no `giver_name` key.
- `/api/v1`: `giver_name` appears in v1 gift JSON only where `giver_ids` does (`full`); `agent.create_gift` answers
  400 for a body naming `giver_name`. No v1 route is added.
- User-visible text goes through `esc()`; no inline styles or scripts; no gift title or person name in a log line.
- Commits: Conventional Commits, author `SDNick484 <SDNick484@users.noreply.github.com>`, the session's trailers.
- Gate before every commit (this machine: `.venv/bin/python`, and shellcheck via
  `docker run --rm -v "$PWD:/mnt:ro" -w /mnt koalaman/shellcheck:stable -s sh ...`):
  `python -m pytest -q && ruff check && for f in static/js/*.js; do node --check "$f"; done && node --test tests/frontend.test.mjs && shellcheck -s sh installer/setup-head.sh installer/build.sh ops/tracker-backup`

## Decisions fixed before the build

- **Schema 8.** `ideas(id, title NOT NULL, url, store, price_cents, notes, share INTEGER NOT NULL DEFAULT 0,
  wishlist_item_id INTEGER, created_by TEXT NOT NULL, created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL)` with
  `CREATE UNIQUE INDEX ideas_wishlist_item ON ideas(wishlist_item_id) WHERE wishlist_item_id IS NOT NULL`;
  `idea_people(idea_id → ideas ON DELETE CASCADE, person_id → people, gift_id → gifts ON DELETE SET NULL,
  PRIMARY KEY (idea_id, person_id))` with `CREATE INDEX idea_people_gift ON idea_people(gift_id)`;
  `ALTER TABLE gifts ADD COLUMN giver_name TEXT`.
- **Idea protection.** `ideas.PROTECTED_SQL` over alias `i`: any tagged person protected, **or** any copy whose gift
  matches `gifts.PROTECTED_SQL`. Write it with an inner alias `g` for the gift so `gifts.PROTECTED_SQL` can be
  embedded unchanged.
- **Copy state**, per tagged person: `taken` when `gift_id` is set; else `held` when the idea has a wish, the
  wishlist is on, and the cached wish is reserved or purchased; else `open`. Held is computed on read and carries
  `held_by` (the wishlist's `reserved_by` or `purchased_by`).
- **Idea JSON:** `{id, title, url, store, price_cents, notes, share, wishlist_item_id, created_by, updated_at,
  people: [{person_id, state, gift_id, occasion_id, held_by}]}` ordered by person sort order. No giver, no Santa.
- **Routes** (endpoint names in brackets; all `family_only`):
  `GET /api/ideas` [ideas_list] with optional `?person=<id>`; `POST /api/ideas` [ideas_create];
  `GET|PATCH|DELETE /api/ideas/<int:idea_id>` [ideas_get, ideas_update, ideas_delete];
  `POST /api/ideas/<int:idea_id>/take/<int:person_id>` [ideas_take] body `{giver_ids, giver_name?, occasion_id?}`,
  201 with gift JSON; `GET /api/ideas/share` [ideas_share] with optional `?people=1,2,3`, answers `{text}`;
  `GET /api/giver-names` [giver_names] answers a list of strings;
  `POST /api/gifts/<int:gift_id>/back-to-ideas` [gifts_back_to_ideas];
  `POST /api/gifts/<int:gift_id>/to-idea` [gifts_to_idea], 201 with idea JSON.
  `GET /api/ideas/share` is registered before `/api/ideas/<int:idea_id>` (an int converter would not match anyway).
- **Lock rules for ideas** (locked = `app.unlocked()` false): a protected idea answers `404 {"error": "No such
  idea."}` exactly like a missing one, for GET, PATCH, DELETE and take; it is absent from lists and the share text; a
  body whose `person_ids` names a protected person is 423; `?people=` naming a protected person is 423; `?person=`
  naming a protected person answers `[]`.
- **Validation.** `person_ids`: a non-empty list of existing people, de-duplicated (400 otherwise). Title, url,
  store, notes and price as `gifts._fields` (factor the shared part so the title message can say "An idea needs a
  title of 1 to 200 characters."). `share`: `common.flag`. A wish-linked idea's `person_ids` must be exactly the
  person whose `wishlist_person_id` is the wish's tab: 400 `"A wish is for one person."`. PATCH `person_ids: []`
  deletes the idea and answers `{"deleted": true}`. Untagging a taken copy drops the row and leaves the gift.
- **Taking.** `gifts.create(con, user, data, *, wishlist_item_id=None, idea_copy=None)` where
  `idea_copy=(idea_id, person_id)`: inside its transaction, after the gift insert, run
  `UPDATE idea_people SET gift_id = ? WHERE idea_id = ? AND person_id = ? AND gift_id IS NULL`; rowcount 0 raises
  `ApiError(409, "Someone just took this. Reload.")`, which rolls the gift back. Held is 409
  `"Someone already has this on the wishlist. Reload."`. Occasion defaults to `occasions.current()`; none is 400
  `"Create an occasion first."`. Giver required: no ids and no name is 400 `"Say who is giving it."`.
- **Reopening.** Deleting a gift reopens by `ON DELETE SET NULL`. `gifts.update` removing a recipient sets
  `gift_id = NULL` for that person's copy in the same transaction. On a gift that is some copy's `gift_id` (an
  "idea gift"), clearing every giver and the name (PATCH or assign) is 400
  `"Send it back to ideas instead."`. Back to ideas deletes through `wishlist.delete_gift`. Move to ideas is 400 for
  an idea gift or a gift with a giver or name, and 400 `"Remove the other recipients first."` for a wish-linked gift
  with more than one recipient; it creates the idea (recipients as tags, same fields, same wish, share off) and
  deletes the gift in one transaction (`wishlist_item_id` moves from gift to idea, so no wishlist call is needed:
  an unassigned gift holds no reservation).
- **Track.** `gifts.track(giver_ids, user_ids, giver_name=None) -> str`: `"ours"` when there is no giver id and no
  name, or an owner is among the ids; else `"theirs"`. Every caller that has a gift row passes its `giver_name`
  (grep `track(`: gifts, budgets, boards, wishlist, agent, lists). Santa needs an owner among the ids, as today.
- **Labels.** Board group key is `(tuple(giver_ids), giver_name)`; its label is the existing label of the ids, then
  the name, joined by `" & "`. `wishlist.label(con, giver_ids, giver_name=None)` appends the trimmed name as one more
  distinct part before the 80-character cut.
- **Suggestions.** Distinct `giver_name` over gifts the caller can see (`unlocked or NOT gifts.PROTECTED_SQL`),
  case-insensitively de-duplicated keeping the most recent spelling, ordered by latest `updated_at`, at most 50.
- **Share text.** For each person (in `people` sort order) with at least one open copy of a shared visible idea:
  the person's name, then one line per idea `- <title>` + `" - <store>"` if any + `" - <url>"` if any, ideas by id;
  a blank line between people. No people: the empty string.
- **Board.** `boards.board` gains `ideas`: the person's ideas whose copy for them is `open` or `held`, as idea JSON
  plus `copy_state`. `board["unassigned"]` keeps gifts with no giver and no name. `wishlist.decorate_board` leaves out
  of `open` and `reserved` any wish linked to an idea.
- **Wishlist pull-over.** The existing route `POST /api/occasions/<occasion_id>/board/<person_id>/wishes/<item_id>`
  keeps its path and checks, and now creates an idea through `wishlist.add_wish` (returns the idea id; 201 with idea
  JSON). `_unlink_gone` also clears `ideas.wishlist_item_id` for a gone wish when the tagged person's tab is in the
  feed. A wish counts as linked (not open) when a gift **or** an idea names it.
- Existing tests edited (say so in the PR): `tests/test_db.py` version literal, `tests/test_privacy.py` (`args()`,
  fixture, `protected_state`, `HOSTILE`), `tests/test_school.py::FAMILY_ONLY`, `tests/test_wishlist.py` (add_wish now
  returns an idea), `tests/frontend.test.mjs` (idea screen helpers).

## Review Focus

1. A locked caller learns nothing of a protected idea: not by id, list, `?person=`, the share text, or the giver-name
   suggestions; and an unprotected idea whose taken copy's gift later gains a protected co-recipient disappears too.
   (Tasks 2, 3, 4)
2. Two owners take the same copy at once: exactly one gift exists, the second answers 409. (Task 4)
3. Taking a wish-linked idea while the wishlist is unreachable answers 503, leaves no gift, and the copy is still
   open. (Task 5)
4. Reopening touches only the right copy: deleting, back-to-ideas, or dropping one recipient reopens that person's
   copy and leaves every other copy of the idea as it was. (Tasks 4, 5)
5. Typed names: `" uncle casey "` and `"Uncle Casey"` are one suggestion; 81 characters is 400; `""` clears; a name
   with `<b>` renders escaped on every screen. (Tasks 3, 7)

## File map

```
db.py              _to_v8, MIGRATIONS[8], SCHEMA_VERSION = 8
ideas.py           NEW: PROTECTED_SQL, create, get_json, list_json, update, delete, take, share_text, to_idea, copy_of_gift
gifts.py           giver_name in create/update/_set_givers/to_json; track(..., giver_name); idea_copy on create;
                   copy reopening and the "send it back" refusal in update
boards.py          assign takes giver_name/expected_giver_name; group key and label; board["ideas"]
budgets.py, lists.py, agent.py   pass giver_name to track; agent JSON and create_gift rules
wishlist.py        label/_expected with giver_name; add_wish -> idea; take_idea; held state; _unlink_gone for ideas
app.py             the idea routes, giver-names, back-to-ideas, to-idea; refuse_locked_write covers person_ids;
                   gift_json drops giver_name in school mode
static/js/core.js  giverText(gift, people)
static/js/idea.js  quick idea: For (multi), What, Link, Store, Share
static/js/ideas.js NEW: #/ideas list, filter, take, copy list
static/js/gift.js, board.js, person.js, home.js, main.js   typed giver, ideas column, back/move to ideas, links
tests/             test_ideas.py, test_typed_givers.py (new); edits listed above
CLAUDE.md          an "Ideas and typed givers" section
```

---

### Task 1: Schema version 8

**Files:** Modify `db.py`; Test `tests/test_db.py`

**Interfaces:** Produces tables `ideas`, `idea_people`, column `gifts.giver_name` as fixed above.

- [ ] **Step 1: Write the failing tests** in `tests/test_db.py`:
  `test_v7_database_migrates_to_v8_keeping_rows` (build a v7 database the way the existing v6→v7 test does, insert a
  gift, migrate, assert `schema_version == "8"`, the gift row unchanged with `giver_name IS NULL`, and
  `ideas`/`idea_people` empty); `test_idea_people_gift_set_null_on_gift_delete` (insert idea, copy with gift_id,
  delete the gift, assert `gift_id IS NULL`); `test_ideas_one_per_wish` (two ideas with the same
  `wishlist_item_id` raise `sqlite3.IntegrityError`). Update any `== "7"` literal to read `db.SCHEMA_VERSION`.
- [ ] **Step 2:** `python -m pytest tests/test_db.py -q` → the new tests FAIL.
- [ ] **Step 3:** Add `_to_v8(con)` and `MIGRATIONS[8]`; set `SCHEMA_VERSION = 8`. Comment above it in the style of
  the other steps.
- [ ] **Step 4:** `python -m pytest tests/test_db.py -q` → PASS; then the full gate.
- [ ] **Step 5:** Commit `feat(db): schema 8 for ideas and typed givers`.

### Task 2: Ideas: create, read, edit, delete, under the lock

**Files:** Create `ideas.py`, `tests/test_ideas.py`; Modify `gifts.py` (shared field parsing), `app.py`,
`tests/test_privacy.py`, `tests/test_school.py`

**Interfaces:**
- Produces: `ideas.PROTECTED_SQL: str`; `ideas.is_protected(con, idea_id) -> bool`;
  `ideas.visible(con, idea_id, *, unlocked: bool) -> bool` (missing counts as visible);
  `ideas.create(con, user: dict, data: dict, *, wishlist_item_id: int | None = None) -> int`;
  `ideas.get_json(con, idea_id) -> dict` (404 `"No such idea."`);
  `ideas.list_json(con, *, unlocked: bool, person_id: int | None = None) -> list[dict]`;
  `ideas.update(con, idea_id, data) -> bool` (True when the idea was deleted for having no people);
  `ideas.delete(con, idea_id) -> None`; `ideas.copy_state(con, row, person_id) -> tuple[str, str | None]`
  (state, held_by; held needs Task 5, so return `"open"`/`"taken"` here and leave a single hook point).
- Produces in `app.py`: `visible_idea(idea_id)` (the 404), and `refuse_locked_write` also 423s on a protected id in
  `person_ids`.

- [ ] **Step 1: Write the failing tests** in `tests/test_ideas.py` (use `ids`, `unlock`, `client`):
  - `test_create_one_idea_for_three_people`: POST `{"title": "Animal tracking bracelet", "person_ids": [kid1, kid2,
    kid3], "store": "Shop", "url": "https://example.com/b"}` unlocked → 201; JSON `share is False`, three people each
    `state == "open"`, `gift_id is None`.
  - `test_validation`: no title, `person_ids` missing/empty/`[999999]`/`["x"]`, url `"ftp://x"` → 400 each.
  - `test_patch_fields_tags_and_share`: PATCH title, `share: true`, `person_ids` drop one → reflected.
  - `test_patch_no_people_deletes`: PATCH `person_ids: []` → `{"deleted": true}`, then GET 404.
  - `test_list_filter_by_person`.
  - Lock: `test_locked_protected_idea_is_missing` (an idea tagged kid1 and jordan, created unlocked; after
    `client.post("/api/lock/lock", json={}, headers=mut())`, GET/PATCH/DELETE answer exactly
    `(404, {"error": "No such idea."})` as id 999999 does; list and `?person=<jordan>` omit it; `?person=<kid1>` is
    `[]`); `test_locked_create_naming_protected_is_423`; `test_locked_unprotected_idea_works`.
  - `test_school_mode_ideas_404` with the `school` fixture pattern from `tests/test_school.py`.
  - In `tests/test_privacy.py`: `args()` gains `"idea_id": [<protected idea>, <visible idea>, 999999]`; the `world`
    fixture creates the protected idea `"ZZ-SECRET-IDEA-ROW"` tagged kid1 and jordan (insert through
    `ideas.create` with a direct connection, as `mk_protected_gift` does) and a visible idea; `protected_state()`
    adds the rows of `ideas` and `idea_people` for ideas with a protected tag; the first `HOSTILE` body gains
    `"person_ids": "KID"`, and `_fill` maps it; an assertion that a request whose `idea_id` is the protected idea
    answers 404 or 423.
  - In `tests/test_school.py`: add `ideas_list, ideas_create, ideas_get, ideas_update, ideas_delete` to `FAMILY_ONLY`.
- [ ] **Step 2:** Run `python -m pytest tests/test_ideas.py tests/test_privacy.py tests/test_school.py -q` → FAIL.
- [ ] **Step 3:** Implement `ideas.py` and the five routes. Field parsing shared with `gifts._fields` via a helper
  that takes the noun for the title message. `created_by` is the user's email, as on gifts.
- [ ] **Step 4:** Same command → PASS; full gate.
- [ ] **Step 5:** Commit `feat(ideas): ideas tagged with several people, behind the lock`.

### Task 3: Typed givers on gifts

**Files:** Modify `gifts.py`, `budgets.py`, `lists.py`, `boards.py`, `agent.py`, `wishlist.py` (`label` only),
`app.py`; Create `tests/test_typed_givers.py`; Modify `tests/test_school.py` (`giver_names` to `FAMILY_ONLY`)

**Interfaces:**
- Produces: `gifts.track(giver_ids: set[int], user_ids: set[int], giver_name: str | None = None) -> str`;
  `gifts.giver_name_of(data: dict) -> str | None | object` (a sentinel for "absent"); gift JSON key `giver_name`;
  `boards.parse_assign(data) -> tuple[list[int], list[int], str | None, str | None]` (giver_ids, expected ids,
  giver_name, expected_giver_name; both names optional, default `None`);
  `boards.assign(con, gift_id, giver_ids, expected_giver_ids, after_write=None, *, giver_name=None,
  expected_giver_name=None)`; `wishlist.label(con, giver_ids, giver_name=None)`;
  `gifts.giver_names(con, *, unlocked: bool) -> list[str]`.

- [ ] **Step 1: Write the failing tests** in `tests/test_typed_givers.py`:
  - `test_typed_name_only_is_theirs`: `mk_gift(..., to=[jordan], frm=[], giver_name="Uncle Casey")` → JSON
    `giver_name == "Uncle Casey"`, `track == "theirs"`, `status == "told"`, `told_at` set.
  - `test_owner_and_name_is_ours`; `test_santa_refused_with_name_only` (400); `test_exchange_gift_refuses_name`
    (400, using the exchange fixtures from `tests/test_exchanges.py`).
  - `test_name_trimmed_and_limits`: `"  Uncle Casey  "` stored trimmed; 81 characters → 400; `""` and `null` clear;
    a non-string → 400.
  - `test_patch_changes_track`: adding the name to a giverless gift moves it to theirs (status told); removing it maps
    back as `_set_givers` does.
  - `test_not_counted_in_spending`: a priced gift from a name only adds nothing to `spent_cents`/`planned_cents` in the
    summary.
  - `test_board_groups_by_name`: two gifts for kid1, one from `"Uncle Casey"`, one from Pat household; the board has
    a group labelled `"Uncle Casey"` with `is_us` false and `list_text` starting `"Uncle Casey\n\n"`.
  - `test_assign_with_name_and_conflict`: assign `{giver_ids: [], giver_name: "Uncle Casey", expected_giver_ids: [],
    expected_giver_name: null}` → 200; repeating with the old expectation → 409.
  - `test_suggestions` (with `clock`): gifts named `"Uncle Casey"` at t, `"Aunt Riley"` at t+10 and
    `" uncle casey "` at t+20 → `GET /api/giver-names` is exactly `["uncle casey", "Aunt Riley"]`; a name used only
    on a protected gift is absent while locked and present unlocked.
  - `test_school_mode_name_is_400_and_key_absent`.
  - `test_agent_json`: a `full` token sees `giver_name`; `shop` and `delivery` do not; `POST /api/v1/gifts` with
    `giver_name` → 400.
  - `test_wishlist_label_includes_name` (unit: `wishlist.label(con, [], "Uncle Casey") == "Uncle Casey"`;
    with a Pat & Lee id → `"Pat & Lee & Uncle Casey"`; with an owner → `"<us label> & Uncle Casey"`).
- [ ] **Step 2:** `python -m pytest tests/test_typed_givers.py -q` → FAIL.
- [ ] **Step 3:** Implement. Every `track(` call with a gift row passes `row["giver_name"]`; `budgets._gifts` rows
  gain `giver_name`. `_set_givers(con, gift_id, giver_ids, giver_name=<keep>)` writes both and applies the track
  mapping once. `refuse_locked_write` is unchanged (a name is not protected data). Route `GET /api/giver-names`
  is `family_only`.
- [ ] **Step 4:** `python -m pytest -q` → all PASS (existing board, budget, wishlist and agent tests must not change);
  full gate.
- [ ] **Step 5:** Commit `feat(gifts): a typed giver name for one-off givers`.

### Task 4: Taking an idea, reopening a copy, and the share list

**Files:** Modify `ideas.py`, `gifts.py`, `boards.py`, `app.py`, `tests/test_ideas.py`, `tests/test_school.py`,
`tests/test_privacy.py`

**Interfaces:**
- Consumes: Task 2's `ideas.*`, Task 3's `giver_name` handling.
- Produces: `gifts.create(..., idea_copy: tuple[int, int] | None = None)`;
  `ideas.take(con, user, idea_id, person_id, data) -> int` (gift id; no wishlist call, Task 5 wraps it);
  `ideas.is_idea_gift(con, gift_id) -> bool`; `ideas.share_text(con, person_ids: list[int] | None, *,
  unlocked: bool) -> str`; `ideas.to_idea(con, user, gift_id) -> int`; `board["ideas"]`.

- [ ] **Step 1: Write the failing tests** in `tests/test_ideas.py`:
  - `test_take_for_one_leaves_others_open`: bracelet for kid1..kid3; take for kid2 with `giver_name: "Uncle Casey"`
    → 201 gift with `recipient_ids == [kid2]`, title/url/store/price copied, occasion = current; idea shows kid2
    `taken` with that gift and occasion, kid1 and kid3 `open`.
  - `test_take_needs_a_giver` (400), `test_take_untagged_person` (404), `test_take_twice_is_409`.
  - `test_take_race`: call `ideas.take` twice on two connections for the same copy, the second inside a patched
    `gifts.create` that runs after the first commits (or simply call it after) → second raises 409 and
    `SELECT COUNT(*) FROM gifts` is 1.
  - Reopening: `test_delete_gift_reopens_only_that_copy`; `test_back_to_ideas` (POST → 200, gift gone, copy open);
    `test_removing_recipient_reopens`: PATCH the taken gift's `recipient_ids` to `[jordan]` → kid2's copy open, the
    others unchanged; `test_clearing_givers_on_idea_gift_is_400` (PATCH and assign).
  - `test_taken_gift_turning_protected_hides_the_idea`: idea tagged jordan only, taken for jordan, then the gift
    gains kid1 as a recipient (unlocked); locked: the idea is 404 and absent from list and share text.
  - `test_to_idea`: an unassigned gift for kid1 and kid2 → 201 idea tagged both, share off, gift gone; a gift with a
    giver → 400; an idea gift → 400.
  - Share list: `test_share_text_format`: shared bracelet (store, url) for kid1..kid3 taken for kid2, a shared
    puzzle (url only) for kid2, an unshared idea for kid1 → with `?people=<kid1>,<kid2>,<kid3>` unlocked, text equals
    exactly `"Kid 1\n- Animal tracking bracelet - Shop - https://example.com/b\n\nKid 2\n- Puzzle - https://example.com/p\n\nKid 3\n- Animal tracking bracelet - Shop - https://example.com/b"`;
    `test_share_locked`: `?people=<kid1>` → 423; no parameter → unprotected people only; bad `?people=x` → 400.
  - `test_board_lists_open_ideas`: kid1's board `ideas` has the open bracelet, not the one taken for kid1.
  - `tests/test_school.py::FAMILY_ONLY` gains `ideas_take, ideas_share, gifts_back_to_ideas, gifts_to_idea`;
    `tests/test_privacy.py::args()` already has `idea_id` and `person_id`.
- [ ] **Step 2:** `python -m pytest tests/test_ideas.py tests/test_privacy.py tests/test_school.py -q` → FAIL.
- [ ] **Step 3:** Implement. `ideas_take` route: `visible_idea`, `refuse_locked_write` on the gift body, then
  `ideas.take` (Task 5 swaps in `wishlist.take_idea`). `gifts_back_to_ideas`: `visible_gift`, 400
  `"This gift did not come from an idea."` unless `ideas.is_idea_gift`, then `wishlist.delete_gift`.
  `gifts_to_idea`: `visible_gift`, then `ideas.to_idea`. Board-like routes (`take`, `share` with protected people,
  `to-idea`, `back-to-ideas`) rely on visibility checks, not `@unlock_required`, so unprotected ideas work while
  locked.
- [ ] **Step 4:** Same command, then `python -m pytest -q` → PASS; full gate.
- [ ] **Step 5:** Commit `feat(ideas): take an idea for one person, send it back, copy the shared list`.

### Task 5: The wishlist and ideas

**Files:** Modify `wishlist.py`, `app.py`, `tests/test_wishlist.py`

**Interfaces:**
- Consumes: `ideas.create(..., wishlist_item_id=)`, `ideas.take`, `wishlist._write_through`.
- Produces: `wishlist.add_wish(con, user, occasion_id, person_id, item_id) -> int` (an **idea** id now);
  `wishlist.take_idea(con, user, idea_id, person_id, data) -> int`; `wishlist.held(con, item_id) -> str | None`
  (the holder's label when the cached wish is reserved or purchased, else None; None when the link is off);
  `ideas.copy_state` uses it.

- [ ] **Step 1: Write the failing tests** in `tests/test_wishlist.py` (its `wl` fixture checks every request):
  - `test_add_wish_creates_an_idea`: 201 idea JSON tagged the child, `wishlist_item_id` set, share off; the board's
    `wishlist.open` no longer lists the wish and `board["ideas"]` has it; no request was sent.
  - `test_wish_idea_is_for_one_person`: PATCH `person_ids` adding a sibling → 400 `"A wish is for one person."`.
  - `test_take_wish_idea_reserves`: take with Pat & Lee → one `reserve` request with `{"by": "Pat & Lee"}`, gift has
    the wish link; with `giver_name: "Uncle Casey"` → `{"by": "Uncle Casey"}`.
  - `test_take_wish_idea_unreachable`: the `wl` fixture answers unreachable → 503, `SELECT COUNT(*) FROM gifts` is
    0, copy `open`.
  - `test_held_wish`: cache shows the wish reserved by a signed-in shopper → idea copy `state == "held"` with
    `held_by`; share text omits it; take → 409; after the feed releases it → `open`.
  - `test_back_to_ideas_releases`: taken wish idea, back to ideas → one `reserve` with `{"by": null}`; copy open.
  - `test_gone_wish_unlinks_idea`: feed without the item (tab present) → `ideas.wishlist_item_id IS NULL`, idea kept.
  - `test_to_idea_moves_the_wish_link`: an unassigned wish gift (made the old way through `gifts.create(...,
    wishlist_item_id=)`) → idea with the wish, no request sent; a wish gift with two recipients → 400.
- [ ] **Step 2:** `python -m pytest tests/test_wishlist.py -q` → the new tests FAIL.
- [ ] **Step 3:** Implement. `take_idea`: `gid = ideas.take(...)`; if `_listed(con, gid)` is None return it; else
  run `_write_through(con, gid, write)` where `write(hook)` opens a transaction that only runs the hook; on any
  exception delete the gift locally (`gifts.delete`) and re-raise, so the copy reopens. Linked-wish checks in
  `decorate_board` and `add_wish` look at `ideas.wishlist_item_id` too. Route `ideas_take` calls
  `wishlist.take_idea`; `wishlist_add_wish` returns idea JSON.
- [ ] **Step 4:** `python -m pytest -q` → PASS; full gate.
- [ ] **Step 5:** Commit `feat(wishlist): pulled-over wishes become ideas`.

### Task 6: Quick idea and the ideas screen

**Files:** Modify `static/js/idea.js`, `static/js/core.js`, `static/js/main.js`, `static/js/home.js`,
`static/style.css`, `tests/frontend.test.mjs`; Create `static/js/ideas.js`

**Interfaces:**
- Consumes: the idea routes, `GET /api/ideas/share`.
- Produces: `idea.js` exports `forChoices` (unchanged), `backTarget` (unchanged), `ideaBody(form) -> object`
  (`{title, person_ids, url?, store?, share}`; empty link/store omitted); `ideas.js` exports
  `copyState(idea, personId) -> "open" | "taken" | "held"`, `filterIdeas(ideas, personId | null)`,
  `kidsIds(people) -> number[]` (active protected people), `renderIdeas(ideas, people)`; `core.js` exports
  `giverText(gift, people) -> string` (`giverLabel` of the ids plus the name, joined `" & "`).

- [ ] **Step 1: Write the failing tests** in `tests/frontend.test.mjs`: `ideaBody` for a form stub with two people
  checked, link and store blank, share unchecked → `{title, person_ids: [a, b], share: false}`; `copyState` for each
  state; `filterIdeas`; `kidsIds`; `giverText({giver_ids: [], giver_name: "Uncle <b>"}, people) === "Uncle <b>"`
  and `renderIdeas` output contains `Uncle &lt;b&gt;`-style escaping for a title with `<b>`. Remove the `fromChoices`
  test and import.
- [ ] **Step 2:** `node --test tests/frontend.test.mjs` → FAIL.
- [ ] **Step 3:** Implement. Quick idea: For as a check list (recent first, the `?for=` person checked), What, Link,
  Store, "Share with relatives" checkbox (unchecked), no From; Save POSTs `/api/ideas`. `#/ideas`: a person filter
  (`?person=`), each idea with its people and their state (taken shows the occasion name, held shows "on the
  wishlist: <holder>"), a Share toggle, edit fields, Delete, "Take for <name>…" per open copy opening an inline
  form (giver: the board's `assignChoices`, plus a text input with a `<datalist>` from `GET /api/giver-names`;
  occasion picker defaulting to the current one), and Copy list buttons for "This person" (when filtered), "The
  kids" and "Everyone" that GET `/api/ideas/share` and put `text` on the clipboard (reuse board.js's copy fallback).
  Home gets an "Ideas" link next to the counts. Register `ideas` in `main.js` `SCREENS` and in `mode.js`'s
  `FAMILY_ONLY_SCREENS`.
- [ ] **Step 4:** `node --test tests/frontend.test.mjs` and `python -m pytest tests/test_frontend.py -q` → PASS; full
  gate.
- [ ] **Step 5:** Commit `feat(ui): quick idea for several people and the ideas screen`.

### Task 7: Typed givers, boards and gifts in the UI

**Files:** Modify `static/js/gift.js`, `static/js/board.js`, `static/js/person.js`, `static/style.css`,
`tests/frontend.test.mjs`

**Interfaces:**
- Consumes: `giverText`, `copyState`, the routes from Tasks 3–5.
- Produces: `board.js` `assignBody(gift, value, name = null)` →
  `{giver_ids, giver_name, expected_giver_ids, expected_giver_name}`; `gift.js` `buildPatch` sends `giver_name`.

- [ ] **Step 1: Write the failing tests**: `assignBody` with `"other"` and a name → ids `[]`, the name, and the
  gift's current ids and name as expected; `buildPatch` includes `giver_name: null` when the box is cleared and the
  trimmed text otherwise; `renderBoard` for a board with `ideas` renders an "Ideas" section with each idea title
  escaped and an Assign select per idea, and a gift with `giver_name: "<i>x"` renders escaped; `renderGiftPage` shows
  a "Back to ideas" button for a gift whose `from_idea` is true and "Move to ideas" for an unassigned gift that
  is not. (Gift JSON gains `from_idea: bool` in family mode — add it in `app.gift_json` with a server test in
  `tests/test_ideas.py` in this task, and drop it in school mode.)
- [ ] **Step 2:** `node --test tests/frontend.test.mjs` → FAIL.
- [ ] **Step 3:** Implement. Gift screen: From keeps the people checkboxes and adds "Someone else" text input with a
  `<datalist>` of suggestions; the sub-line uses `giverText`. Board: an "Ideas" section above the giver groups
  (open and held ideas; held ones dim with the holder, no Assign); the Assign menu on ideas and gifts gains
  "Someone else…" which reveals an inline name input with the datalist and an OK button; Unassign on an idea gift
  reads "Back to ideas" and POSTs `/back-to-ideas`; "Unassigned" keeps old giverless gifts with a "Move to ideas"
  action; the wishlist section's "Add to board…" select becomes one "Add to ideas" button per open wish. Person
  screen: the person's open ideas with Take, and the "+ Idea" link unchanged.
- [ ] **Step 4:** `node --test tests/frontend.test.mjs`, `python -m pytest -q` → PASS; full gate.
- [ ] **Step 5:** Commit `feat(ui): typed givers, ideas on the board, back and move to ideas`.

### Task 8: Docs and the whole-branch check

**Files:** Modify `CLAUDE.md`, `docs/wishlist-feed.md`

- [ ] **Step 1:** `CLAUDE.md`: add `ideas.py` and `static/js/ideas.js` to the file structure, and an "Ideas and typed
  givers" section (decisions in this plan and the spec): idea protection includes taken copies' gifts; every idea
  read filters on `ideas.PROTECTED_SQL`; taking goes through `gifts.create(idea_copy=)` / `wishlist.take_idea`;
  reopening rules; `track` takes `giver_name`; typed givers and ideas are family-only; v1 shows `giver_name` to
  `full` only and never writes it. `docs/wishlist-feed.md` "When the tracker writes": the `by` may be a typed name,
  and pulling a wish over sends nothing.
- [ ] **Step 2:** Run the app locally (`DEV_EMAIL=alex@example.com` on loopback, per `CLAUDE.md`) and walk the Uncle
  Casey story from the spec: one bracelet for three kids, share on, copy the kids' list, take for one kid with a
  typed name, copy again and see that kid's line gone.
- [ ] **Step 3:** Full gate → PASS.
- [ ] **Step 4:** Commit `docs: ideas and typed givers`.
