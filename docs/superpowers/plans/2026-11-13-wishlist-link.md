# Wishlist Link (Plan 6 of 6) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Read a protected person's wishes, with their reserved and purchased state, from the Family Wishlist; show them on that person's board; and write assignments back as reservations (and "confirmed" as purchased), so a relative browsing the wishlist cannot buy something the tracker has already handed to someone. Santa never crosses.

**Architecture:** Schema version 6 adds `wishlist_cache` (one row: the last good feed, its fetch time and the last failure) and `wishlist_aliases` (a wishlist email or free-text name mapped once to a person), and a partial unique index on `gifts.wishlist_item_id` (one gift per wish). All wishlist logic is one new module, `wishlist.py`: the transport (`_open`, the one function that touches the network, stubbed in tests), the defensive feed parser, the cache, matching, the board decoration and the write-back. The writers that already own a transaction gain one optional, additive parameter each (`boards.assign(after_write=)`, `gifts.set_status(after_write=)`, `gifts.update(after_write=)`, `gifts.create(wishlist_item_id=)`), so the outbound call runs inside the same `BEGIN IMMEDIATE` as the local write. Routes are one separate block of `app.py`, all `family_only` and `@unlock_required`. The board screen gains a wishlist section. No new dependency.

**Tech Stack:** as the core plan (`http.client` from the standard library for the transport).

**Spec:** `docs/superpowers/specs/2026-11-12-gift-tracker-design.md`, "The wishlist link". The other side's endpoint is SDNick484/family-wishlist issue #20; the contract the tracker is built against is copied into `docs/wishlist-feed.md`. Builds on every earlier plan (none is edited here).

## Decisions fixed before the build

- **Schema 6** (assigned by the queue; the issue says 4). `wishlist_cache(id INTEGER PRIMARY KEY CHECK (id = 1), feed TEXT, fetched_at INTEGER, failed_at INTEGER)`; `wishlist_aliases(id, kind TEXT CHECK (kind IN ('email','name')), value TEXT, person_id → people ON DELETE CASCADE, UNIQUE (kind, value))`; `CREATE UNIQUE INDEX gifts_wishlist_item ON gifts(wishlist_item_id) WHERE wishlist_item_id IS NOT NULL`. `people.wishlist_person_id` and `gifts.wishlist_item_id` exist since version 1.
- **Settings.** `WISHLIST_URL` (http or https, a host, optionally a path prefix; no query, fragment or user info) and `WISHLIST_TOKEN` (printable ASCII, no spaces; required when the URL is set). Anything else stops the app at start-up with a message naming the setting. Unset URL: the feature is off, every wishlist route is the generic 404, the board answer is byte-for-byte what it was, and nothing is ever sent. `WISHLIST_URL` is baked from `deploy/deploy.env`; `WISHLIST_TOKEN` never is.
- **Family only, behind the lock.** `wishlist.enabled()` is false in school mode whatever the settings say, so no outbound request is made there; every wishlist route is `family_only`, then `@unlock_required`, then 404 when off. The cache is read only by those routes and by the (already unlocked) board. No `/api/v1` operation; a token never sees the cache, and `/api/v1` status writes that would change a linked wish's purchased state are 409 (change it in the app).
- **Transport.** Requests go to the origin of `WISHLIST_URL` only, to `{prefix}/api/feed`, `{prefix}/api/feed/items/{id}/reserve` and `{prefix}/api/feed/items/{id}/purchase`, where `id` is a positive integer from the cache. No query string ever. `http.client` follows no redirect; a 3xx is a failure. 5 second deadline for the whole exchange, 2 MiB response cap (Content-Length or bytes read). Headers: `Authorization: Bearer <WISHLIST_TOKEN>`, `Accept`, and `Content-Type` on a POST; nothing else (no cookie, no Cloudflare header).
- **Feed parsing.** JSON with no NaN or Infinity; the top level must be `{"people": [{"id": int, "name": str, "items": [{"id": int, ...}]}]}` or the whole feed is malformed. Field by field: text fields that are not text become null; `url` is kept only when it is http(s) and at most 2000 characters; `price` (dollars) becomes `price_cents` only when a finite number from 0 to `MAX_CENTS`; `secret` and `purchased` are booleans or false. A malformed or oversized feed, a timeout, a refused connection or any non-200 answer is "wishlist unreachable", never a 500.
- **Cache.** The parsed feed is stored with `fetched_at`. A read refreshes when the feed is over 60 seconds old and the last failure is over 30 seconds old; `POST /api/wishlist/refresh` always tries. A failure keeps the cache and sets `failed_at`; the board shows the cache, its age and `reachable: false`. Nothing is queued.
- **Off-list.** After a good fetch, a gift linked to a wish that is no longer in the feed is unlinked (`wishlist_item_id = NULL`) and stays as an ordinary gift, but only when the tab of one of its recipients (`people.wishlist_person_id`) is in the feed: a tab removed from `FEED_PEOPLE` does not wipe links. A linked gift whose wish is not in the cache is treated as off-list by every write (no outbound call).
- **Label.** The outbound `by` is built from the givers only, never from Santa or status: any owner among the givers becomes the owners' household label (their shared `household`, else `Us`); every other giver becomes their household, else their name; distinct parts joined by " & ", cut at 80 characters. So Alex alone, Sam alone, both, with or without Santa, all write the same label.
- **Write-back order.** Inside the local `BEGIN IMMEDIATE`: the local write (with all its checks), then the outbound call(s) computed from what was just written, then the cache update, then COMMIT. A wishlist 409 or 404 or an unreachable wishlist raises before COMMIT, so the transaction rolls back (nothing changes locally). If anything after an accepted outbound call raises (a second outbound call, the cache write, the commit), every accepted call is taken back newest first (reserve the old label, release, re-purchase), then the error is raised. A call that timed out may have arrived, so its inverse is sent too (harmless if it did not). (Changed while building: the first draft put the outbound call before the local write, which would have had to repeat the track and status rules of `gifts._set_givers` to know what to send.) The other owner's simultaneous assign waits on the lock and then gets the existing 409 without any request. Holding the write lock across a request (up to 5 s) is accepted for a two-person app.
  - What the wishlist should show is computed from the gift after the local write: purchased by the label when the gift is on the theirs track and `confirmed` or `given`, else reserved by the label (or nothing, with no giver). `_plan` compares that with the cache and sends only the difference: a reserve (`by: null` to release), a purchase, or an un-purchase followed by a reserve (the contract's purchase ends the reservation, so going back to told reserves again). So assign, reassign (a purchased wish moves its purchase to the new label), unassign, status and `PATCH /api/gifts/<id>` all keep it in step through the same hook, and Santa and every other field send nothing because they do not change the label or the purchased state. A reservation or purchase by a signed-in shopper (an email on it) is never ours to change: releasing leaves it, claiming it is 409.
  - Deleting a linked, listed gift that holds a feed reservation releases it first; a purchase stays.
  - `/api/v1` never writes to the wishlist: a status there that would flip a listed wish's purchased state is 409 (change it in the app). (Changed while building: the first draft also refused such changes through `PATCH`; the hook made that unnecessary, so the gift page works on linked gifts.)
  - Wishlist 409 → 409 `{"error": "Someone else has this on the wishlist. Reload."}` and the cache is refreshed; 404 → 409 `This wish is no longer on the wishlist. Reload.` and a refresh; anything else → 503 `The wishlist cannot be reached. Nothing was changed.` with `failed_at` set.
- **Adding a wish.** `POST /api/occasions/<occasion_id>/board/<person_id>/wishes/<item_id>` (body `{}`) creates the linked gift (title, url, store and price from the cached wish; no givers; status idea), sending nothing: an unassigned gift and an open wish agree. Assigning it is the ordinary assign. 404 for a wish not on that person's tab, 409 for a wish already linked or not open in the cache.
- **Reservations made on the wishlist.** A wish on the person's tab that is reserved or purchased and has no linked gift is listed under the person matched by email (an owner's `users.email`, else an email alias), else by a name alias of the free-text name; unmatched ones are listed once each for the owner to map (`PUT /api/wishlist/aliases {kind, value, person_id}`). Mapping is stored, not re-asked.
- **Untrusted text.** Wish names, reserver names and URLs go through `esc()`; a non-http(s) URL was already dropped by the parser and the UI links only http(s). Nothing from the wishlist is written to a log; a log line names only an item id, a status code or an exception class, never the token.

## Review Focus

1. Santa never crosses: no outbound body has any key but `by` and `purchased`, the label for an owners' gift is identical with Santa on or off and with one or both owners, and toggling Santa sends nothing. (Tasks 4, 6; the request allowlist in `tests/test_wishlist.py` checks every request of the file.)
2. Both sides stay consistent: a 409, a 404 or an unreachable wishlist changes nothing locally; a local failure after a successful reserve releases it; two owners assigning at once make one request and one 409. (Task 6)
3. The lock and the mode: every wishlist route is 423 locked and 404 in school mode, the board's wishlist block exists only unlocked, school mode makes no request even with `WISHLIST_URL` set, and both privacy walks pass with the feature on and the cache full of markers. (Tasks 5, 7)
4. Outbound safety: one origin, fixed paths, no redirect followed, http(s) only at start-up, 5 s, size cap, malformed JSON is "unreachable", the token in no log, error or response. (Tasks 3, 4)
5. Off-list and off: a deleted wish keeps its gift as an ordinary gift (and a hidden tab does not unlink), a v5 database migrates keeping its rows, and with `WISHLIST_URL` unset the board answer is unchanged. (Tasks 2, 4, 5)

## File map

```
db.py             + _to_v6, MIGRATIONS[6], SCHEMA_VERSION = 6
settings.py       + WISHLIST_URL, WISHLIST_TOKEN (one block, validated at import)
wishlist.py       NEW: transport, parser, cache, labels, matching, board decoration, aliases, write-back
boards.py         assign(after_write=None)
gifts.py          create(wishlist_item_id=None), set_status(after_write=None), update(after_write=None)
agent.py          set_status refuses a purchased-state flip of a linked wish
app.py            board and assign/status/patch/delete go through wishlist when it is on; one block of wishlist routes
static/js/        board.js (the wishlist section); style.css (+ .wish rules)
.env.example, deploy/deploy.env.example, installer/setup-head.sh, installer/build.sh   the two settings
docs/wishlist-feed.md   NEW: the contract, for the other side
tests/            + test_wishlist.py; test_db.py (v6), test_school.py (FAMILY_ONLY), test_privacy.py (args),
                  test_installer_static.py (golden export line, token never baked), conftest.py (env), frontend.test.mjs
CLAUDE.md, README.md
```

---

### Task 1: This plan

- [ ] Commit `docs: plan for the wishlist link`

### Task 2: Schema v6

**Files:** Modify `db.py`; Test: `tests/test_db.py` (appended).

**Interfaces:** `db.SCHEMA_VERSION = 6`, `db._to_v6(con)` as `MIGRATIONS[6]`.

- [ ] **Step 1: Write the failing tests**

```python
def test_a_v5_database_migrates_to_v6_keeping_its_rows(tmp_path):
    c = _v5_database(tmp_path / "t.db")       # v1..v5 steps, version '5', one gift with wishlist_item_id 7
    db.migrate(c)
    assert db.SCHEMA_VERSION >= 6
    assert c.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()[0] == str(db.SCHEMA_VERSION)
    assert tuple(c.execute("SELECT title, wishlist_item_id FROM gifts").fetchone()) == ("Card", 7)
    assert c.execute("SELECT COUNT(*) FROM wishlist_cache").fetchone()[0] == 0
    db.migrate(c)                              # again: nothing changes
def test_a_fresh_database_has_the_v6_tables(con): column lists of wishlist_cache and wishlist_aliases
def test_the_v6_migrated_tables_match_a_fresh_database(tmp_path, con)
def test_one_gift_per_wish_and_any_number_without_one(con): a second gift with wishlist_item_id 7 raises IntegrityError; two with NULL do not
def test_the_cache_has_one_row_and_aliases_are_unique_and_cascade(con)
```

- [ ] **Step 2: Run; expect failures.** **Step 3: Implement.** **Step 4: Run the whole suite.**
- [ ] **Step 5: Commit** `feat(db): schema v6 with the wishlist cache and aliases`

### Task 3: Settings and the installer

**Files:** Modify `settings.py`, `.env.example`, `deploy/deploy.env.example`, `installer/setup-head.sh`, `installer/build.sh`, `tests/conftest.py` (pop the two names), `tests/test_installer_static.py` (golden export line gains the two names; a new test that the token is never baked); Test: `tests/test_wishlist.py`.

**Interfaces:** `settings.WISHLIST_URL: str` (normalized, no trailing `/`, "" when off), `settings.WISHLIST_TOKEN: str`, `settings.wishlist_url(raw) -> str` (raises `ValueError` naming `WISHLIST_URL`).

- [ ] **Step 1: Write the failing tests**

```python
@pytest.mark.parametrize("raw", ["ftp://192.0.2.10/", "file:///etc/passwd", "javascript:alert(1)", "192.0.2.10:8090",
                                 "http://", "https://user:pw@wishlist.example.com", "http://wishlist.example.com/?a=1",
                                 "http://wishlist.example.com/#x"])
def test_a_bad_wishlist_url_stops_the_app_at_start_up(raw): subprocess import settings exits non-zero, "WISHLIST_URL" in stderr
def test_a_wishlist_url_needs_a_token(): WISHLIST_URL set, WISHLIST_TOKEN empty → non-zero, "WISHLIST_TOKEN" in stderr; "a b" too
@pytest.mark.parametrize("raw,url", [("http://192.0.2.10:8090", "http://192.0.2.10:8090"), (" https://wishlist.example.com/ ", "https://wishlist.example.com"),
                                     ("http://192.0.2.10/wishlist/", "http://192.0.2.10/wishlist"), ("", "")])
def test_good_wishlist_urls_are_normalized(raw, url)
def test_the_wishlist_token_is_never_baked_into_the_installer(tmp_path): deploy.env with WISHLIST_URL and WISHLIST_TOKEN=wl_never_baked → ': "${WISHLIST_URL:=http://192.0.2.10:8090}"' in out, "wl_never_baked" not in out
```

- [ ] **Step 2: Run; expect failures.** **Step 3: Implement.** **Step 4: Run the whole suite and shellcheck.**
- [ ] **Step 5: Commit** `feat(settings): WISHLIST_URL and WISHLIST_TOKEN, checked at start-up`

### Task 4: The client: transport, feed, cache, labels

**Files:** Create `wishlist.py`, `docs/wishlist-feed.md`; Test: `tests/test_wishlist.py`.

**Interfaces:**
- `wishlist.enabled() -> bool`; `_open(method, url, headers, body, timeout) -> (status, bytes)` (the one network function; raises on failure); `_request(method, path, body=None) -> (status, bytes)` (builds the URL from the settings and a fixed path; raises `Unreachable`); `parse_feed(raw: bytes) -> dict` (raises `Unreachable`); `refresh(con) -> bool`; `current(con, *, max_age=60) -> dict` `{"reachable", "fetched_at", "age_seconds", "people": [...]}`; `item(con, item_id) -> dict | None`; `label(con, giver_ids) -> str | None`.
- Exceptions `Unreachable`, `Conflict`, `Gone`.
- The test stub: a fixture that replaces `wishlist._open`, records every request (method, scheme, host, port, path, query, headers, parsed body) in a module-level list, and answers from a scripted feed.

- [ ] **Step 1: Write the failing tests**

```python
def test_off_without_a_url_and_in_school_mode(monkeypatch): enabled() False with URL ""; True with URL and family; False with URL and MODE school
def test_the_feed_is_fetched_from_the_one_origin_with_the_token(client, wl): one GET, scheme http, host 192.0.2.10, port 8090, path "/api/feed", query "", Authorization "Bearer wl-test-token", no Cf-* or Cookie header
def test_a_path_prefix_is_kept_and_nothing_else_from_outside_reaches_the_path(client, wl, monkeypatch): URL http://192.0.2.10/wishlist → "/wishlist/api/feed"
@pytest.mark.parametrize("raw", [b"", b"not json", b"[]", b'{"people": 5}', b'{"people": [{"id": "1", "name": "A", "items": []}]}',
                                 b'{"people": [{"id": 1, "name": "A", "items": [{"id": true}]}]}', b'{"people": [], "x": NaN}', b"[" * 100000])
def test_a_malformed_feed_is_unreachable_never_a_500(raw): parse_feed raises Unreachable
def test_fields_are_cleaned(): url "javascript:alert(1)" → None, "https://example.com/a" kept; price 12.5 → 1250, -1/1e12/"5" → None; name 5 → None; secret "yes" → False
@pytest.mark.parametrize("status", [301, 302, 401, 404, 500])
def test_any_answer_but_200_is_unreachable_and_keeps_the_cache(con, wl, status)
def test_the_real_transport_follows_no_redirect_and_caps_the_size(): a loopback server answering 302 then 200 sees one request and _request raises Unreachable; a body of MAX_BYTES + 1 raises Unreachable
def test_the_real_transport_gives_up_after_the_timeout(monkeypatch): a loopback server that never answers, TIMEOUT 0.3 → Unreachable within 2 s
def test_the_cache_keeps_its_age_and_is_refreshed_only_when_old(con, wl, clock)
def test_a_failure_is_not_retried_for_thirty_seconds(con, wl, clock)
def test_a_deleted_wish_unlinks_its_gift_but_a_hidden_tab_does_not(client, ids, wl, unlock)
def test_the_label_is_the_household_never_santa(con, ids): label([alex]) == label([sam]) == label([alex, sam]) == "Us"; household renamed → that name; [jordan] → "Jordan"; [jordan, riley] with household "Riley House" on both → "Riley House"; [alex, jordan] → "Us & Jordan"; [] → None; an 81-character household is cut to 80
def test_the_token_is_in_no_log_line(con, wl, caplog): every failure path logged; WISHLIST_TOKEN not in caplog.text
```

- [ ] **Step 2: Run; expect failures.** **Step 3: Implement.** **Step 4: Run the whole suite.**
- [ ] **Step 5: Commit** `feat(wishlist): read the feed through one guarded transport, cached`

### Task 5: Wishes on the board, aliases and the routes

**Files:** Modify `app.py` (board decoration; the wishlist block of routes), `gifts.py` (`create(wishlist_item_id=)`), `tests/test_privacy.py` (`args()` gains `item_id`, `alias_id`), `tests/test_school.py` (`FAMILY_ONLY`); Test: `tests/test_wishlist.py`.

**Interfaces:**
- `wishlist.decorate_board(con, board, person_id)`: adds `board["wishlist"] = {"linked", "reachable", "fetched_at", "age_seconds", "tabs", "open", "reserved", "unmatched"}` and `gift["wish"] = {"item_id", "listed", "reserved_by", "purchased", "purchased_by", "in_sync"}` on each linked gift. A wish is `{"id", "name", "url", "store", "price_cents", "occasion", "secret", "reserved_by", "purchased", "purchased_by"}`; a reserved entry adds `person_id` and `label`; an unmatched entry is `{"kind", "value", "label"}`.
- `wishlist.add_wish(con, user, occasion_id, person_id, item_id) -> int`.
- `wishlist.list_aliases(con)`, `set_alias(con, data) -> (dict, created)`, `delete_alias(con, alias_id)`.
- Routes, each `family_only`, `@unlock_required`, `@wishlist_on`: `GET /api/wishlist`, `POST /api/wishlist/refresh`, `POST /api/occasions/<int:occasion_id>/board/<int:person_id>/wishes/<int:item_id>`, `GET`/`PUT /api/wishlist/aliases`, `DELETE /api/wishlist/aliases/<int:alias_id>`.

- [ ] **Step 1: Write the failing tests**

```python
def test_with_the_feature_off_the_board_is_unchanged_and_the_routes_are_404(client, ids, unlock): board has no "wishlist" key; each route answers the generic 404; no request
def test_the_board_lists_open_wishes_of_the_linked_tab(client, ids, wl, unlock): exact wish dicts; the person not linked → linked False and tabs [{id, name}]
def test_adding_a_wish_makes_a_linked_unassigned_gift_and_sends_nothing(client, ids, wl, unlock): 201; title, url, store, price_cents from the wish; giver_ids []; the wish leaves "open"; no POST recorded; again → 409; a wish of another tab → 404; a reserved wish → 409
def test_a_reservation_made_on_the_wishlist_appears_under_the_matched_person(client, ids, wl, unlock): reserved_by_email of an owner → that owner; an email alias → its person; a name alias (any case) → its person; else in unmatched once
def test_mapping_a_name_once_is_remembered(client, ids, wl, unlock): PUT alias 201, again 200 (replaces); bad kind / empty value / unknown person 400; DELETE 200 then 404
def test_the_board_shows_the_cache_and_its_age_when_the_wishlist_is_down(client, ids, wl, unlock, clock): reachable False, age_seconds == 120, the wishes still listed
def test_wish_text_is_returned_as_data(client, ids, wl, unlock): a name "<img src=x onerror=alert(1)>" comes back verbatim (escaped by the UI); a "javascript:" url is null
def test_every_wishlist_route_needs_the_unlock(client, ids, wl): each answers 423 {"error": "Locked", "locked": True} and no request is made
def test_the_privacy_walks_pass_with_the_feature_on(client, world, wl): the locked walk of tests/test_privacy.py over every GET with a feed full of markers leaks nothing; unlocked it sees the wish names (control)
```

- [ ] **Step 2: Run; expect failures.** **Step 3: Implement.** **Step 4: Run the whole suite, both privacy walks included.**
- [ ] **Step 5: Commit** `feat(wishlist): wishes and wishlist reservations on the board`

### Task 6: Write-back

**Files:** Modify `boards.py` (`assign(after_write=)`), `gifts.py` (`set_status(after_write=)`, `update(after_write=)`), `agent.py` (`set_status`), `app.py` (assign, status, patch, delete routes); Test: `tests/test_wishlist.py`.

**Interfaces:** `wishlist.assign(con, gift_id, giver_ids, expected)`, `wishlist.set_status(con, gift_id, status, order, actor)`, `wishlist.update(con, gift_id, data, actor)`, `wishlist.delete_gift(con, gift_id)`, `wishlist.refuse_agent_flip(con, gift_id, status)` (`/api/v1`).

- [ ] **Step 1: Write the failing tests**

```python
def test_assigning_reserves_under_the_household_label(client, ids, wl, unlock): one POST /api/feed/items/11/reserve {"by": "Jordan"}; the cache shows it; reassigning to another of the same label sends nothing
def test_the_owners_label_is_identical_with_and_without_santa(client, ids, wl, unlock): assign to [alex] then santa on, then [sam], then [alex, sam]: every body is {"by": "Us"}; toggling Santa sends nothing
def test_unassigning_releases(client, ids, wl, unlock): {"by": None}
def test_a_409_from_the_wishlist_is_409_changes_nothing_and_refreshes(client, ids, wl, unlock): givers, status, told_at unchanged; a GET /api/feed followed
def test_unreachable_is_503_and_changes_nothing(client, ids, wl, unlock): and failed_at is set; the board shows reachable False
def test_a_removed_wish_is_409_and_becomes_off_list(client, ids, wl, unlock): wishlist 404 → 409; after the refresh the gift is unlinked and assign works locally with no request
def test_a_local_failure_after_a_reserve_releases_it(client, ids, wl, unlock, monkeypatch): the cache write after the reserve raises → 500; the second request is {"by": None}; the givers are unchanged
def test_a_local_failure_after_a_relabel_restores_the_old_label(...): the COMMIT fails → the undo is {"by": "Jordan"}
def test_two_owners_assigning_at_once_make_one_request(client, ids, wl, unlock): threads as in test_boards; one 200, one 409; exactly one reserve recorded
def test_confirmed_marks_purchased_and_told_undoes_it(client, ids, wl, unlock): {"by": "Jordan", "purchased": True} then False; given after confirmed sends nothing
def test_reassigning_a_purchased_wish_moves_the_purchase(client, ids, wl, unlock): un-purchase then purchase under the new label; to us: un-purchase then reserve "Us"
def test_a_failure_on_the_second_write_takes_back_the_first(...): the reserve after an un-purchase is refused, so the purchase is sent again
def test_a_shoppers_reservation_is_never_ours_to_change(...)
def test_ours_track_status_and_santa_never_send(client, ids, wl, unlock)
def test_deleting_a_reserved_gift_releases_it(client, ids, wl, unlock): and when the release fails the gift stays (503)
def test_patch_keeps_the_wishlist_in_step(client, ids, wl, unlock): giver change reserves, status confirmed purchases, unreachable is 503 and changes nothing
def test_v1_never_writes_to_the_wishlist(client, ids, wl, unlock): full unlocked token, confirmed → 409, nothing sent; lower scopes 404
def test_off_list_and_feature_off_writes_are_local_only(client, ids, wl, unlock, monkeypatch)
def test_zz_every_request_of_this_file_was_allowed(): every captured body's keys ⊆ {"by", "purchased"}, values str|None and bool, no query, the only Authorization is the configured token; at least one reserve and one purchase were captured
```

- [ ] **Step 2: Run; expect failures.** **Step 3: Implement.** **Step 4: Run the whole suite.**
- [ ] **Step 5: Commit** `feat(wishlist): write assignments back as reservations`

### Task 7: School mode and the token

**Files:** Test only (`tests/test_wishlist.py`), plus a fix if a test finds one.

```python
def test_school_mode_has_no_wishlist_even_with_a_url(client, ids, wl, monkeypatch): every wishlist route is the generic 404; status, delete and PATCH of a gift with a wishlist_item_id send nothing
def test_the_token_is_in_no_response(client, ids, wl, unlock): every route and every failure answer; the board; /api/me
```

- [ ] **Commit** `test(wishlist): school mode and the token in no answer`

### Task 8: Screen

**Files:** Modify `static/js/board.js`, `static/style.css`, `tests/frontend.test.mjs`.

**Interfaces:** `wishlistLine(wl)`, `renderWishlist(wl, choices, people)`, `wishBadge(gift)`, `aliasBody(entry, personId)`, `safeHref(url)`.

- [ ] Tests (node): every name, reserver and URL is escaped; a non-http(s) URL is not a link; Assign controls are disabled when `reachable` is false; the age line; an unmatched entry offers a person picker; the board renders exactly as before when there is no `wishlist` key.
- [ ] **Commit** `feat(ui): the wishlist on the board`

### Task 9: Docs

- [ ] `CLAUDE.md` section "Wishlist link", `README.md` (settings, how it works, setup), full gate in both venvs. **Commit** `docs: the wishlist link`
