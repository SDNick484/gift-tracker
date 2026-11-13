"""The wishlist link: read a protected person's wishes and their reserved and purchased state from the Family Wishlist,
and write assignments back as reservations. Route-free: every function takes a sqlite3.Connection. The contract with
the other side is docs/wishlist-feed.md; the decisions are in docs/superpowers/plans/2026-11-13-wishlist-link.md.

Who owns what. The wishlist owns each wish and its reserved and purchased state. The tracker owns givers, Santa,
status, order fields, hiding places and money, keyed by `gifts.wishlist_item_id`, and none of that is ever sent: an
outbound body has the keys `by` and `purchased` and nothing else, and `by` is built from the givers alone (label()).

Off and on. enabled() is false without WISHLIST_URL and always false in school mode; then nothing here sends a request
(_request refuses), and app.py answers 404 on every wishlist route. Everything that reads the cache is behind the
unlock (app.py), because the wishes belong to protected people.

The transport. _open is the one function that touches the network (tests replace it). Requests go to the origin and
path prefix of WISHLIST_URL only, on fixed paths with integer item ids and no query string; http.client follows no
redirect; the whole exchange has a TIMEOUT-second deadline and the answer at most MAX_BYTES. Anything unexpected is
Unreachable, never a 500. Log lines name an item id, a status code or an exception class: never the token, a wish or
a person.

Writing back. The outbound call runs inside the local BEGIN IMMEDIATE, before the local write (the before_write hooks of
boards.assign and gifts.set_status). A wishlist refusal or an unreachable wishlist raises before anything local is
written, so the transaction rolls back; if the local write or the commit fails after a successful call, _Undo puts the
wishlist back as the cache said it was. The other owner's simultaneous write waits on the lock and then sees the
change."""
import http.client
import json
import logging
import math
import re
import sqlite3
import time
from urllib.parse import urlsplit

import boards
import common
import db
import gifts
import occasions
import people
import settings
from common import ApiError, is_school, text, whole

log = logging.getLogger("tracker.wishlist")

TIMEOUT = 5.0                 # seconds for a whole request, connect to last byte
MAX_BYTES = 2 * 1024 * 1024   # the largest answer read; a feed over this is "unreachable"
MAX_AGE = 60                  # a read refreshes a feed older than this many seconds
RETRY_AFTER = 30              # ... unless the last attempt failed less than this many seconds ago
LABEL_MAX = 80                # the wishlist cuts `by` at 80 characters
FEED_PATH = "/api/feed"
PURCHASED = ("confirmed", "given")  # a theirs-track gift in one of these is purchased on the wishlist

UNREACHABLE = "The wishlist cannot be reached. Nothing was changed."
CONFLICT = "Someone else has this on the wishlist. Reload."
GONE = "This wish is no longer on the wishlist. Reload."


class Unreachable(Exception):
    """No usable answer: refused, timed out, redirected, too big, malformed, or a status we do not expect."""


class Conflict(Exception):
    """The wishlist answered 409: someone else holds the item, or it is purchased."""


class Gone(Exception):
    """The wishlist answered 404: the wish was deleted, or its tab is no longer in the feed."""


class _TooLarge(Exception):
    pass


def enabled() -> bool:
    """Whether the link is on: WISHLIST_URL set and family mode. Read at call time, so tests can switch it."""
    return bool(settings.WISHLIST_URL) and not is_school()


# ---------------------------------------------------------------- the transport
def _open(method: str, url: str, headers: dict, body: bytes | None, timeout: float) -> tuple[int, bytes]:
    """Send one request and return (status, up to MAX_BYTES + 1 bytes of the body). The one place that touches the
    network (tests stub it). http.client never follows a redirect: a 3xx is returned as it is."""
    u = urlsplit(url)
    cls = http.client.HTTPSConnection if u.scheme == "https" else http.client.HTTPConnection
    deadline = time.monotonic() + timeout
    conn = cls(u.hostname, u.port, timeout=timeout)
    try:
        conn.request(method, u.path, body=body, headers=headers)
        sock = conn.sock  # the response may detach it from conn; its timeout still governs every read
        resp = conn.getresponse()
        length = resp.getheader("Content-Length") or ""
        if length.isdigit() and int(length) > MAX_BYTES:
            raise _TooLarge()
        data = bytearray()
        while len(data) <= MAX_BYTES and not resp.isclosed():  # closed: the whole body has been read
            left = deadline - time.monotonic()
            if left <= 0:
                raise TimeoutError()
            sock.settimeout(left)
            chunk = resp.read1(min(64 * 1024, MAX_BYTES + 1 - len(data)))
            if not chunk:
                break
            data += chunk
        return resp.status, bytes(data)
    finally:
        conn.close()


def _request(method: str, path: str, payload: dict | None = None) -> tuple[int, bytes]:
    """One request to WISHLIST_URL's origin and prefix plus `path`, a fixed string built here (never user input).
    Raises Unreachable for anything but an answer of at most MAX_BYTES."""
    if not enabled():
        raise Unreachable()  # off, or school mode: never a request
    base = urlsplit(settings.WISHLIST_URL)
    url = f"{base.scheme}://{base.netloc}{base.path}{path}"
    headers = {"Authorization": f"Bearer {settings.WISHLIST_TOKEN}", "Accept": "application/json"}
    body = None
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    try:
        status, data = _open(method, url, headers, body, TIMEOUT)
    except (OSError, http.client.HTTPException, ValueError, _TooLarge) as exc:
        log.warning("wishlist: %s %s failed (%s)", method, _log_path(path), type(exc).__name__)
        raise Unreachable() from None
    if len(data) > MAX_BYTES:
        log.warning("wishlist: %s %s answered more than %d bytes", method, _log_path(path), MAX_BYTES)
        raise Unreachable()
    return status, data


def _log_path(path: str) -> str:
    return path  # fixed text and an integer item id only (see _item_path); kept as a function so that stays visible


def _item_path(item_id, action: str) -> str:
    if isinstance(item_id, bool) or not isinstance(item_id, int) or item_id <= 0 or action not in ("reserve", "purchase"):
        raise ValueError("bad wishlist item")
    return f"{FEED_PATH}/items/{item_id}/{action}"


def _write(item_id: int, action: str, payload: dict) -> None:
    """POST reserve or purchase. 2xx is done; 409 is Conflict; 404 is Gone; anything else is Unreachable."""
    path = _item_path(item_id, action)
    status, _data = _request("POST", path, payload)
    if 200 <= status < 300:
        return
    if status == 409:
        raise Conflict()
    if status == 404:
        raise Gone()
    log.warning("wishlist: POST %s answered %d", _log_path(path), status)
    raise Unreachable()


# ---------------------------------------------------------------- reading the feed
def _reject_constant(_name):
    raise ValueError("NaN and Infinity are not JSON")


def _is_id(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool) and v > 0


def _str(v, limit: int) -> str | None:
    if not isinstance(v, str):
        return None
    v = v.strip()[:limit].strip()
    return v or None


_CONTROL = re.compile(r"[\x00-\x20\x7f]")


def _url(v) -> str | None:
    """Only an http(s) link of at most 2000 characters with no space or control character; anything else is dropped,
    as gifts._fields refuses it."""
    if not isinstance(v, str):
        return None
    v = v.strip()
    if len(v) > 2000 or _CONTROL.search(v) or not v.lower().startswith(("http://", "https://")):
        return None
    return v


def _price_cents(v) -> int | None:
    """The wishlist's price (dollars, a JSON number) as cents, or None for anything but a finite amount in range."""
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or v < 0:
        return None
    cents = round(v * 100)
    return cents if cents <= common.MAX_CENTS else None


def _clean_item(it: dict) -> dict:
    archived = it.get("archived_at")
    return {"id": it["id"], "name": _str(it.get("name"), 200), "url": _url(it.get("url")),
            "store": _str(it.get("store"), 200), "price_cents": _price_cents(it.get("price")),
            "occasion": _str(it.get("occasion"), 100), "secret": it.get("secret") is True,
            "reserved_by": _str(it.get("reserved_by"), LABEL_MAX),
            "reserved_by_email": (_str(it.get("reserved_by_email"), 254) or "").lower() or None,
            "purchased": it.get("purchased") is True, "purchased_by": _str(it.get("purchased_by"), LABEL_MAX),
            "purchased_by_email": (_str(it.get("purchased_by_email"), 254) or "").lower() or None,
            "archived_at": archived if isinstance(archived, int) and not isinstance(archived, bool) else None}


def parse_feed(raw: bytes) -> dict:
    """{"people": [{"id", "name", "items": [cleaned items]}]}, or Unreachable when the shape is not the contract's: not
    JSON, NaN or Infinity, a missing or non-integer id, a duplicate item id, or anything but a list where one belongs.
    A field of the wrong type inside an item is dropped (null), not fatal."""
    try:
        doc = json.loads(raw.decode("utf-8"), parse_constant=_reject_constant)
    except (ValueError, UnicodeDecodeError, RecursionError):
        raise Unreachable() from None
    if not isinstance(doc, dict) or not isinstance(doc.get("people"), list):
        raise Unreachable()
    out, seen = [], set()
    for p in doc["people"]:
        if not isinstance(p, dict) or not _is_id(p.get("id")) or not isinstance(p.get("items"), list):
            raise Unreachable()
        items = []
        for it in p["items"]:
            if not isinstance(it, dict) or not _is_id(it.get("id")) or it["id"] in seen:
                raise Unreachable()
            seen.add(it["id"])
            items.append(_clean_item(it))
        out.append({"id": p["id"], "name": _str(p.get("name"), 100), "items": items})
    return {"people": out}


def _fetch() -> dict:
    status, data = _request("GET", FEED_PATH)
    if status != 200:
        log.warning("wishlist: GET %s answered %d", FEED_PATH, status)
        raise Unreachable()
    try:
        return parse_feed(data)
    except Unreachable:
        log.warning("wishlist: GET %s answered a malformed feed", FEED_PATH)
        raise


# ---------------------------------------------------------------- the cache
def _row(con):
    return con.execute("SELECT feed, fetched_at, failed_at FROM wishlist_cache WHERE id = 1").fetchone()


def _mark_failed(con) -> None:
    con.execute("INSERT INTO wishlist_cache (id, failed_at) VALUES (1, ?) "
                "ON CONFLICT(id) DO UPDATE SET failed_at = excluded.failed_at", (common.now(),))


def refresh(con) -> bool:
    """Fetch the feed now. On success store it (failed_at cleared) and unlink gifts whose wish is gone; on failure keep
    the old feed and set failed_at. Returns whether the fetch worked."""
    try:
        feed = _fetch()
    except Unreachable:
        _mark_failed(con)
        return False
    with db.transaction(con):
        con.execute("INSERT INTO wishlist_cache (id, feed, fetched_at, failed_at) VALUES (1, ?, ?, NULL) "
                    "ON CONFLICT(id) DO UPDATE SET feed = excluded.feed, fetched_at = excluded.fetched_at, failed_at = NULL",
                    (json.dumps(feed), common.now()))
        _unlink_gone(con, feed)
    return True


def _unlink_gone(con, feed: dict) -> None:
    """A gift linked to a wish that is no longer in the feed becomes an ordinary (off-list) gift, but only when the tab
    of one of its recipients is in the feed: a tab taken out of the wishlist's FEED_PEOPLE does not wipe the links."""
    present = {it["id"] for p in feed["people"] for it in p["items"]}
    tabs = {p["id"] for p in feed["people"]}
    for r in con.execute("SELECT id, wishlist_item_id FROM gifts WHERE wishlist_item_id IS NOT NULL").fetchall():
        if r["wishlist_item_id"] in present:
            continue
        rtabs = {x[0] for x in con.execute("SELECT p.wishlist_person_id FROM gift_recipients gr JOIN people p "
                                           "ON p.id = gr.person_id WHERE gr.gift_id = ?", (r["id"],))}
        if rtabs & tabs:
            con.execute("UPDATE gifts SET wishlist_item_id = NULL, updated_at = ? WHERE id = ?", (common.now(), r["id"]))


def _feed(con) -> dict | None:
    row = _row(con)
    return json.loads(row["feed"]) if row and row["feed"] else None


def current(con, *, max_age: int = MAX_AGE) -> dict:
    """The cached feed, refreshed first when it is older than max_age and the last failure is RETRY_AFTER old:
    {"reachable", "fetched_at", "age_seconds", "people"}. reachable is false after a failed attempt (the old feed, if
    any, is still returned). Callers check enabled() first."""
    row, now = _row(con), common.now()
    stale = row is None or row["fetched_at"] is None or now - row["fetched_at"] > max_age
    may_retry = row is None or row["failed_at"] is None or now - row["failed_at"] >= RETRY_AFTER
    if stale and may_retry:
        refresh(con)
        row, now = _row(con), common.now()
    return status(row, now)


def status(row, now: int) -> dict:
    fetched = row["fetched_at"] if row else None
    feed = json.loads(row["feed"]) if row and row["feed"] else {"people": []}
    return {"reachable": bool(row) and row["failed_at"] is None and fetched is not None, "fetched_at": fetched,
            "age_seconds": None if fetched is None else max(0, now - fetched), "people": feed["people"]}


def item(con, item_id) -> dict | None:
    """The cached wish with this id, with `tab_id` (its wishlist person), or None."""
    feed = _feed(con)
    for p in (feed or {"people": []})["people"]:
        for it in p["items"]:
            if it["id"] == item_id:
                return {**it, "tab_id": p["id"]}
    return None


def _patch_cache(con, item_id: int, **changes) -> None:
    """Record in the cache what a successful write did, so the board shows it without a refetch. Runs inside the
    writer's transaction: if that rolls back, so does this."""
    feed = _feed(con)
    if feed is None:
        return
    for p in feed["people"]:
        for it in p["items"]:
            if it["id"] == item_id:
                it.update(changes)
    con.execute("UPDATE wishlist_cache SET feed = ? WHERE id = 1", (json.dumps(feed),))


# ---------------------------------------------------------------- the label written as `by`
def us_label(con) -> str:
    """The owners' household label: their shared household, else "Us"."""
    hs = {r[0] for r in con.execute("SELECT household FROM people WHERE id IN (SELECT person_id FROM users)")}
    return next(iter(hs)) if len(hs) == 1 and next(iter(hs)) else people.US


def label(con, giver_ids) -> str | None:
    """The `by` written for these givers, or None for no giver. Any owner among them is the owners' household label
    (so it is the same for Santa and not, and for one owner or both); anyone else is their household, else their
    name. Distinct parts joined by " & ", at most LABEL_MAX characters. Takes nothing but the giver ids, on purpose."""
    ids = set(giver_ids or ())
    if not ids:
        return None
    owners = people.user_person_ids(con)
    parts = [us_label(con)] if ids & owners else []
    for pid in sorted(ids - owners):
        r = con.execute("SELECT name, household FROM people WHERE id = ?", (pid,)).fetchone()
        part = (r["household"] or r["name"]).strip() if r else ""
        if part and part not in parts:
            parts.append(part)
    return " & ".join(parts)[:LABEL_MAX].strip() or None


# ---------------------------------------------------------------- matching who holds a wish
def _norm_name(name: str) -> str:
    return " ".join(name.split()).casefold()


def _holder(it: dict) -> tuple[str | None, str | None]:
    """(display name, email) of whoever holds the wish on the wishlist: the purchaser, else the reserver."""
    if it["purchased"]:
        return it["purchased_by"], it["purchased_by_email"]
    return it["reserved_by"], it["reserved_by_email"]


def _match(con, name: str | None, email: str | None) -> int | None:
    """The person a wishlist holder is: an owner by their own email, else an email alias, else a name alias."""
    if email:
        r = con.execute("SELECT person_id FROM users WHERE email = ?", (email,)).fetchone() or con.execute(
            "SELECT person_id FROM wishlist_aliases WHERE kind = 'email' AND value = ?", (email,)).fetchone()
        if r:
            return r[0]
    if name:
        r = con.execute("SELECT person_id FROM wishlist_aliases WHERE kind = 'name' AND value = ?", (_norm_name(name),)).fetchone()
        if r:
            return r[0]
    return None


# ---------------------------------------------------------------- the board
WISH_KEYS = ("id", "name", "url", "store", "price_cents", "occasion", "secret", "reserved_by", "purchased", "purchased_by")


def _wish_json(it: dict) -> dict:
    return {k: it[k] for k in WISH_KEYS}


def _expected(con, gift_id: int) -> tuple[bool, str | None]:
    """What the wishlist should show for a linked gift: (purchased, by)."""
    row = gifts.get(con, gift_id)
    givers = gifts._ids(con, "gift_givers", gift_id)
    trk = gifts.track(set(givers), people.user_person_ids(con))
    return trk == "theirs" and row["status"] in PURCHASED, label(con, givers)


def _in_sync(it: dict, expected: tuple[bool, str | None]) -> bool:
    purchased, by = expected
    if purchased:
        return it["purchased"] and it["purchased_by"] == by and not it["purchased_by_email"]
    return not it["purchased"] and it["reserved_by"] == by and not (by and it["reserved_by_email"])


def tabs(con, feed_people: list) -> list[dict]:
    """The wishlist's tabs in the feed, each with the tracker person linked to it (or None)."""
    linked = {r[0]: r[1] for r in con.execute(
        "SELECT wishlist_person_id, MIN(id) FROM people WHERE wishlist_person_id IS NOT NULL GROUP BY wishlist_person_id")}
    return [{"id": p["id"], "name": p["name"], "person_id": linked.get(p["id"])} for p in feed_people]


def decorate_board(con, board: dict, person_id: int) -> None:
    """Add the wishlist to a board (boards.board's answer, for an unlocked caller):

    board["wishlist"] = {linked, tab_id, reachable, fetched_at, age_seconds, tabs, open, reserved, unmatched}: the
    wishes on the person's tab with no gift in the tracker, open ones in `open`, reserved or purchased ones in
    `reserved` with the matched `person_id` (or None) and the wishlist's `label`; each unmatched holder once in
    `unmatched` ({kind, value, label}) for the owner to map. And on every gift linked to a wish, gift["wish"] =
    {item_id, listed, reserved_by, purchased, purchased_by, in_sync}."""
    state = current(con)
    tab = people.get_person(con, person_id)["wishlist_person_id"]
    by_id = {it["id"]: it for p in state["people"] for it in p["items"]}
    linked_ids = {r[0] for r in con.execute("SELECT wishlist_item_id FROM gifts WHERE wishlist_item_id IS NOT NULL")}
    items = next((p["items"] for p in state["people"] if p["id"] == tab), []) if tab is not None else []
    open_, reserved, unmatched, seen = [], [], [], set()
    for it in items:
        if it["id"] in linked_ids:
            continue
        name, email = _holder(it)
        if not it["purchased"] and it["reserved_by"] is None:
            open_.append(_wish_json(it))
            continue
        pid = _match(con, name, email)
        reserved.append({**_wish_json(it), "person_id": pid, "label": name})
        key = ("email", email) if email else ("name", _norm_name(name)) if name else None
        if pid is None and key and key not in seen:
            seen.add(key)
            unmatched.append({"kind": key[0], "value": key[1], "label": name or email})
    board["wishlist"] = {"linked": tab is not None, "tab_id": tab, "reachable": state["reachable"],
                         "fetched_at": state["fetched_at"], "age_seconds": state["age_seconds"],
                         "tabs": tabs(con, state["people"]), "open": open_, "reserved": reserved, "unmatched": unmatched}
    for gj in [*board["unassigned"], *(g for grp in board["givers"] for g in grp["gifts"])]:
        item_id = con.execute("SELECT wishlist_item_id FROM gifts WHERE id = ?", (gj["id"],)).fetchone()[0]
        if item_id is None:
            continue
        it = by_id.get(item_id)
        gj["wish"] = {"item_id": item_id, "listed": it is not None,
                      "reserved_by": it["reserved_by"] if it else None, "purchased": bool(it and it["purchased"]),
                      "purchased_by": it["purchased_by"] if it else None,
                      "in_sync": bool(it) and _in_sync(it, _expected(con, gj["id"]))}


def overview(con, *, max_age: int = MAX_AGE) -> dict:
    """GET /api/wishlist: whether it answers, the age of the cache, and its tabs with the person linked to each."""
    state = current(con, max_age=max_age)
    return {"reachable": state["reachable"], "fetched_at": state["fetched_at"], "age_seconds": state["age_seconds"],
            "tabs": tabs(con, state["people"])}


def add_wish(con, user: dict, occasion_id: int, person_id: int, item_id: int) -> int:
    """Put an open wish of the person's tab on their board: a gift linked to it with the wish's name, link, store and
    price, no giver, status idea. Sends nothing (an unassigned gift and an open wish agree); assigning it is the
    ordinary assign. 404 for a wish that is not on this person's tab in the cache; 409 when it is taken."""
    occasions.get(con, occasion_id)  # 404
    person = boards._protected_person(con, person_id)  # 404 for anyone without a board
    it = item(con, item_id)
    if it is None or person["wishlist_person_id"] is None or it["tab_id"] != person["wishlist_person_id"]:
        raise ApiError(404, "No such wish.")
    if it["purchased"] or it["reserved_by"] is not None:
        raise ApiError(409, "Someone already has this on the wishlist. Reload.")
    data = {"occasion_id": occasion_id, "title": it["name"] or "Wish", "recipient_ids": [person_id], "giver_ids": [],
            "url": it["url"], "store": it["store"], "price": it["price_cents"]}
    try:
        return gifts.create(con, user, data, wishlist_item_id=item_id)
    except sqlite3.IntegrityError:
        raise ApiError(409, "This wish is already on a board. Reload.") from None


# ---------------------------------------------------------------- aliases: a wishlist holder mapped once to a person
def _alias(row) -> dict:
    return {"id": row["id"], "kind": row["kind"], "value": row["value"], "person_id": row["person_id"]}


def list_aliases(con) -> list[dict]:
    return [_alias(r) for r in con.execute("SELECT * FROM wishlist_aliases ORDER BY kind, value, id")]


def set_alias(con, data: dict) -> tuple[dict, bool]:
    """{kind: "email" | "name", value, person_id}: create the mapping, or point an existing one at another person.
    Returns (alias, created)."""
    kind, value = text(data, "kind"), text(data, "value")
    if kind not in ("email", "name"):
        raise ApiError(400, "kind must be email or name.")
    value = value.lower() if kind == "email" else _norm_name(value)
    if not value or len(value) > (254 if kind == "email" else LABEL_MAX):
        raise ApiError(400, "value must be 1 to 254 characters for an email, 80 for a name.")
    person_id = whole(data, "person_id")
    if person_id is None or not con.execute("SELECT 1 FROM people WHERE id = ?", (person_id,)).fetchone():
        raise ApiError(400, "No such person.")
    with db.transaction(con):
        row = con.execute("SELECT id FROM wishlist_aliases WHERE kind = ? AND value = ?", (kind, value)).fetchone()
        if row:
            con.execute("UPDATE wishlist_aliases SET person_id = ? WHERE id = ?", (person_id, row["id"]))
            aid, created = row["id"], False
        else:
            aid = con.execute("INSERT INTO wishlist_aliases (kind, value, person_id) VALUES (?, ?, ?)",
                              (kind, value, person_id)).lastrowid
            created = True
    return _alias(con.execute("SELECT * FROM wishlist_aliases WHERE id = ?", (aid,)).fetchone()), created


def delete_alias(con, alias_id: int) -> None:
    if not con.execute("DELETE FROM wishlist_aliases WHERE id = ?", (alias_id,)).rowcount:
        raise ApiError(404, "No such alias.")
