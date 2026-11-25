"""Gifts: one row per gift however many people give or receive it, two status tracks, the Santa rule, and
visibility under the idle lock. Route-free: every function takes a sqlite3.Connection and raises common.ApiError
on bad input.

Visibility: a gift with any protected recipient is protected. While locked it is hidden as a whole: reads that
list gifts take a required `unlocked` and filter on PROTECTED_SQL, and app.py checks visible() before any read or
write of one gift. create/update/set_status/set_givers do not look at the lock; app.py refuses locked writes.

A gift's givers are people plus at most one typed name (`giver_name`, for a one-off giver who is not a person). Its
track follows them: "ours" when no giver and no name is set or an owner (a user) is among the people, and "theirs"
otherwise. Money is integer cents (common.parse_cents)."""
import common
import db
import exchanges
import locations
import occasions
import orders
import people
from common import ApiError, flag, parse_cents, text

OURS = ("idea", "decided", "ordered", "arrived", "wrapped", "given")
THEIRS = ("told", "confirmed", "given")

# True for a gift (aliased `g` in the enclosing query) with any protected recipient. Every query that can show a
# gift while locked filters on this, so a gift shared with a protected person is hidden as a whole.
#
# A gift linked to an exchange is also protected when any giver is: it is the gift for a draw, and a draw whose drawer
# is protected is protected data (exchanges.py). The whole predicate is parenthesised so `NOT {PROTECTED_SQL}` holds.
PROTECTED_SQL = ("(EXISTS (SELECT 1 FROM gift_recipients pr JOIN people pp ON pp.id = pr.person_id "
                 "WHERE pr.gift_id = g.id AND pp.protected = 1) "
                 "OR (g.exchange_id IS NOT NULL AND EXISTS (SELECT 1 FROM gift_givers pg JOIN people pq ON pq.id = pg.person_id "
                 "WHERE pg.gift_id = g.id AND pq.protected = 1)))")


GIVER_NAME_MAX = 80  # the wishlist's `by` limit (wishlist.LABEL_MAX)
ABSENT = object()    # giver_name_of: the body does not name giver_name
EXCHANGE_NAME = "A gift for an exchange is from the drawer: no typed name."
BACK_TO_IDEAS = "Send it back to ideas instead."
TAKEN = "Someone just took this. Reload."
IDEA_TAKEN_ELSEWHERE = ("This gift came from an idea also taken for someone else, so it cannot go behind the lock. "
                        "Send it back to ideas first.")


def _ideas():
    import ideas  # late: ideas imports this module
    return ideas


def refuse_exposing_an_idea(con, gift_id: int) -> None:
    """400 when this gift, a copy of an idea, has just made that idea protected while another copy's gift is still
    visible (ideas.EXPOSED_SQL): that visible gift would point at a hidden idea. Runs inside the writer's transaction,
    after the write. Only an unlocked caller gets here (a locked one is 423 for a protected recipient or giver first),
    so the message reveals nothing."""
    if _ideas().exposed(con, gift_id=gift_id):
        raise ApiError(400, IDEA_TAKEN_ELSEWHERE)


def track(giver_ids: set[int], user_ids: set[int], giver_name: str | None = None) -> str:
    return "ours" if (not giver_ids and not giver_name) or giver_ids & user_ids else "theirs"


def parse_giver_name(value, key: str = "giver_name") -> str | None:
    """A typed giver name: trimmed, 1 to GIVER_NAME_MAX characters; empty or None is None (no name)."""
    if value is None:
        return None
    if not isinstance(value, str):
        raise ApiError(400, f"{key} must be text.")
    value = value.strip()
    if len(value) > GIVER_NAME_MAX:
        raise ApiError(400, f"A giver's name is 1 to {GIVER_NAME_MAX} characters.")
    return value or None


def giver_name_of(data: dict):
    """data["giver_name"] parsed (parse_giver_name), or ABSENT when the body does not name it. Typed givers are
    family-only: naming one at all in school mode is 400."""
    if "giver_name" not in data:
        return ABSENT
    if common.is_school():
        raise ApiError(400, "Typed givers are not used in school mode.")
    return parse_giver_name(data["giver_name"])


def _statuses(trk: str) -> tuple[str, ...]:
    return OURS if trk == "ours" else THEIRS


# ---------------------------------------------------------------- reading
def _row(con, gift_id: int):
    row = con.execute("SELECT * FROM gifts WHERE id = ?", (gift_id,)).fetchone()
    if row is None:
        raise ApiError(404, "No such gift.")
    return row


def _ids(con, table: str, gift_id: int) -> list[int]:
    return [r[0] for r in con.execute(f"SELECT person_id FROM {table} WHERE gift_id = ? ORDER BY person_id", (gift_id,))]


def get(con, gift_id: int):
    """The gift row, or 404. Does not look at the lock: routes check visible() first."""
    return _row(con, gift_id)


def is_protected(con, gift_id: int) -> bool:
    """Whether any recipient is a protected person (active or not), or, for a gift linked to an exchange, any giver.
    The same rule as PROTECTED_SQL."""
    return con.execute("SELECT 1 FROM gifts g WHERE g.id = ? AND " + PROTECTED_SQL, (gift_id,)).fetchone() is not None


def visible(con, gift_id: int, *, unlocked: bool) -> bool:
    """Whether this request may see or change the gift. A gift that does not exist counts as visible, so the
    caller's own lookup answers 404 exactly as it would for a protected gift while locked."""
    return unlocked or not is_protected(con, gift_id)


def to_json(con, row, *, unlocked: bool) -> dict:
    recipients, givers = _ids(con, "gift_recipients", row["id"]), _ids(con, "gift_givers", row["id"])
    out = {"id": row["id"], "occasion_id": row["occasion_id"], "title": row["title"], "status": row["status"],
           "track": track(set(givers), people.user_person_ids(con), row["giver_name"]), "url": row["url"],
           "store": row["store"], "price_cents": row["price_cents"], "notes": row["notes"], "recipient_ids": recipients,
           "giver_ids": givers, "giver_name": row["giver_name"], "told_at": row["told_at"], "created_by": row["created_by"],
           "updated_at": row["updated_at"], **orders.to_json(con, row), "location_id": row["location_id"],
           "location_path": locations.path_map(con).get(row["location_id"]) if row["location_id"] else None,
           "exchange_id": row["exchange_id"]}
    if unlocked:
        out["santa"] = bool(row["santa"])
    return out


def list_for_person(con, occasion_id: int, person_id: int, *, unlocked: bool) -> list:
    """The person's gifts in one occasion. While locked, gifts with any protected recipient are left out."""
    occasions.get(con, occasion_id)
    people.get_person(con, person_id)
    return con.execute(
        "SELECT g.* FROM gifts g JOIN gift_recipients r ON r.gift_id = g.id "
        f"WHERE g.occasion_id = ? AND r.person_id = ? AND (? OR NOT {PROTECTED_SQL}) ORDER BY g.id",
        (occasion_id, person_id, int(unlocked))).fetchall()


def history(con, person_id: int, *, unlocked: bool) -> list:
    """Every gift the person receives, newest occasion first. Works for inactive people. While locked, gifts with
    any protected recipient are left out."""
    people.get_person(con, person_id)
    return con.execute(
        "SELECT g.*, o.name AS occasion_name, o.date AS occasion_date FROM gifts g "
        "JOIN gift_recipients r ON r.gift_id = g.id JOIN occasions o ON o.id = g.occasion_id "
        f"WHERE r.person_id = ? AND (? OR NOT {PROTECTED_SQL}) ORDER BY o.date DESC, g.id",
        (person_id, int(unlocked))).fetchall()


def from_idea(con, gift_id: int) -> bool:
    """Whether the gift was taken from an idea: some copy (idea_people) names it. Such a gift always has a giver;
    clearing them all is 400 (refuse_no_giver), since sending it back to ideas is what that would mean."""
    return con.execute("SELECT 1 FROM idea_people WHERE gift_id = ?", (gift_id,)).fetchone() is not None


def refuse_no_giver(con, gift_id: int, giver_ids, giver_name) -> None:
    """400 when these givers are nobody and the gift came from an idea."""
    if not giver_ids and not giver_name and from_idea(con, gift_id):
        raise ApiError(400, BACK_TO_IDEAS)


def giver_names(con, *, unlocked: bool) -> list[str]:
    """Typed giver names to suggest: distinct case-insensitively (the most recent spelling wins), most recently used
    first, at most 50. Only gifts this request may see count, so a name used only on a hidden gift is not offered."""
    out: dict[str, str] = {}
    for r in con.execute(f"SELECT g.giver_name FROM gifts g WHERE g.giver_name IS NOT NULL AND (? OR NOT {PROTECTED_SQL}) "
                         "ORDER BY g.updated_at DESC, g.id DESC", (int(unlocked),)):
        out.setdefault(r[0].casefold(), r[0])
        if len(out) == 50:
            break
    return list(out.values())


# ---------------------------------------------------------------- validation
def _person_ids(con, data: dict, key: str) -> list[int] | None:
    """data[key] as a de-duplicated list of existing person ids; None when the key is absent."""
    if key not in data:
        return None
    raw = data[key]
    if not isinstance(raw, list) or any(isinstance(i, bool) or not isinstance(i, int) for i in raw):
        raise ApiError(400, f"{key} must be a list of person ids.")
    ids = list(dict.fromkeys(raw))
    for pid in ids:
        if not con.execute("SELECT 1 FROM people WHERE id = ?", (pid,)).fetchone():
            raise ApiError(400, "No such person.")
    return ids


def _fields(data: dict, partial: bool) -> dict:
    return item_fields(data, partial, "A gift")


def item_fields(data: dict, partial: bool, noun: str) -> dict:
    """Title, url, store, notes and price as stored on a gift or an idea (ideas.py). `noun` starts the title message
    ("A gift", "An idea"). With `partial`, a missing title is left alone."""
    out = {}
    if not partial or "title" in data:
        title = text(data, "title")
        if not title or len(title) > 200:
            raise ApiError(400, f"{noun} needs a title of 1 to 200 characters.")
        out["title"] = title
    if "url" in data:
        url = text(data, "url")
        if len(url) > 2000 or (url and not url.lower().startswith(("http://", "https://"))):
            raise ApiError(400, "url must start with http:// or https://.")
        out["url"] = url or None
    for key, limit in (("store", 200), ("notes", 2000)):
        if key in data:
            value = text(data, key)
            if len(value) > limit:
                raise ApiError(400, f"{key} is too long.")
            out[key] = value or None
    if "price" in data:
        out["price_cents"] = parse_cents(data["price"])
    return out


def _check_santa(santa: bool, giver_ids: set[int], user_ids: set[int], exchange_id=None, giver_name=None) -> None:
    if santa and exchange_id is not None:
        raise ApiError(400, "A gift for an exchange is from the drawer, never from Santa.")
    if santa and not (track(giver_ids, user_ids, giver_name) == "ours" and giver_ids & user_ids):
        raise ApiError(400, "Santa is only for a gift from an owner.")


# ---------------------------------------------------------------- writing
def create(con, user: dict, data: dict, *, wishlist_item_id: int | None = None,
           idea_copy: tuple[int, int] | None = None) -> int:
    """`wishlist_item_id` links the new gift to a wish (wishlist.add_wish only; never read from a request body). One
    gift per wish: a second link raises sqlite3.IntegrityError and nothing is created.

    `idea_copy` = (idea_id, person_id) marks that copy of an idea taken by the new gift (ideas.take), in the same
    transaction and only while it is still open: a copy someone else took first is 409 and the gift is rolled back."""
    f = _fields(data, partial=False)
    recipients = _person_ids(con, data, "recipient_ids")
    if not recipients:
        raise ApiError(400, "A gift needs at least one recipient.")
    givers = _person_ids(con, data, "giver_ids") or []
    name = giver_name_of(data)
    name = None if name is ABSENT else name
    exchange_id = exchanges.exchange_id_of(con, data) if "exchange_id" in data else None
    if exchange_id is not None and name:
        raise ApiError(400, EXCHANGE_NAME)
    givers = exchanges.expand_givers(con, givers, exchange_id)  # a protected drawer's gift is also from the owners
    occasion_id = data.get("occasion_id")
    if isinstance(occasion_id, bool) or not isinstance(occasion_id, int) or not con.execute(
            "SELECT 1 FROM occasions WHERE id = ?", (occasion_id,)).fetchone():
        raise ApiError(400, "A gift needs an existing occasion.")
    user_ids = people.user_person_ids(con)
    trk = track(set(givers), user_ids, name)
    status = data.get("status")
    if status is None:
        status = "idea" if trk == "ours" else "told"
    elif status not in _statuses(trk):
        raise ApiError(400, f"On this gift's track, status must be one of {', '.join(_statuses(trk))}.")
    santa = bool(flag(data, "santa", False))
    _check_santa(santa, set(givers), user_ids, exchange_id, name)
    now = common.now()
    with db.transaction(con):
        gid = con.execute(
            "INSERT INTO gifts (occasion_id, title, status, santa, url, store, price_cents, notes, told_at, "
            "giver_name, created_by, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (occasion_id, f["title"], status, int(santa), f.get("url"), f.get("store"), f.get("price_cents"),
             f.get("notes"), common.today() if trk == "theirs" else None, name, user["email"], now, now)).lastrowid
        con.executemany("INSERT INTO gift_recipients (gift_id, person_id) VALUES (?, ?)", [(gid, p) for p in recipients])
        con.executemany("INSERT INTO gift_givers (gift_id, person_id) VALUES (?, ?)", [(gid, p) for p in givers])
        if exchange_id is not None:
            con.execute("UPDATE gifts SET exchange_id = ? WHERE id = ?", (exchange_id, gid))
            exchanges.check_gift(con, gid)  # 400 / 409 roll the gift back
        if wishlist_item_id is not None:
            con.execute("UPDATE gifts SET wishlist_item_id = ? WHERE id = ?", (wishlist_item_id, gid))
        if idea_copy is not None and not con.execute(
                "UPDATE idea_people SET gift_id = ? WHERE idea_id = ? AND person_id = ? AND gift_id IS NULL",
                (gid, *idea_copy)).rowcount:
            raise ApiError(409, TAKEN)
        if idea_copy is not None and _ideas().exposed(con, idea_id=idea_copy[0]):  # ideas.take checked it first
            raise ApiError(400, _ideas().ALSO_LOCKED)
    return gid


def update(con, gift_id: int, data: dict, actor: str | None = None, *, after_write=None) -> None:
    """Change title, url, store, price, notes, recipients, givers (people, and the typed name: giver_name_of),
    Santa, the order fields (orders.FIELDS), the hiding place (location_id), the exchange it is a gift for
    (exchange_id, which refuses a typed name) or the status. A changed giver list can change the track, with the
    consequences set_givers describes; Santa is checked against the result; a status goes through the same rules as
    set_status, ordered included. All of it applies or none of it. `after_write()` runs inside the transaction after
    the local write (wishlist.update); if it raises, nothing is written."""
    old = _row(con, gift_id)
    f = _fields(data, partial=True)
    order = orders.parse(con, data)
    status = data.get("status")
    if status is not None and not isinstance(status, str):
        raise ApiError(400, "status must be text.")
    recipients = _person_ids(con, data, "recipient_ids")
    if recipients is not None and not recipients:
        raise ApiError(400, "A gift needs at least one recipient.")
    givers = _person_ids(con, data, "giver_ids")
    name = giver_name_of(data)
    santa = flag(data, "santa")
    if "exchange_id" in data:
        f["exchange_id"] = exchanges.exchange_id_of(con, data)
    link = f["exchange_id"] if "exchange_id" in data else old["exchange_id"]
    if link is not None and (old["giver_name"] if name is ABSENT else name):
        raise ApiError(400, EXCHANGE_NAME)
    if link is not None and "exchange_id" in data and (old["santa"] if santa is None else santa):
        raise ApiError(400, "A gift for an exchange is from the drawer, never from Santa.")  # linking a Santa gift
    if link is not None and (givers is not None or "exchange_id" in data):
        # A protected drawer's gift is also from the owners; this runs on a new giver list and on a new link.
        givers = exchanges.expand_givers(con, _ids(con, "gift_givers", gift_id) if givers is None else givers, link)
    with db.transaction(con):
        if givers is not None or name is not ABSENT:
            _set_givers(con, gift_id, _ids(con, "gift_givers", gift_id) if givers is None else givers, giver_name=name)
        if recipients is not None:
            # Change the set, not the rows: a recipient who stays keeps their detail and done flag (school mode).
            have = set(_ids(con, "gift_recipients", gift_id))
            con.executemany("DELETE FROM gift_recipients WHERE gift_id = ? AND person_id = ?",
                            [(gift_id, p) for p in have - set(recipients)])
            # A recipient taken off reopens their copy of the idea the gift came from, if any.
            con.executemany("UPDATE idea_people SET gift_id = NULL WHERE gift_id = ? AND person_id = ?",
                            [(gift_id, p) for p in have - set(recipients)])
            con.executemany("INSERT INTO gift_recipients (gift_id, person_id) VALUES (?, ?)",
                            [(gift_id, p) for p in recipients if p not in have])
        if givers is not None or name is not ABSENT:  # after the recipients: one taken off reopens their copy
            refuse_no_giver(con, gift_id, _ids(con, "gift_givers", gift_id), _row(con, gift_id)["giver_name"])
        if santa is not None:
            _check_santa(santa, set(_ids(con, "gift_givers", gift_id)), people.user_person_ids(con), link,
                         _row(con, gift_id)["giver_name"])
            f["santa"] = int(santa)
        f["updated_at"] = common.now()
        con.execute(f"UPDATE gifts SET {', '.join(f'{k} = ?' for k in f)} WHERE id = ?", (*f.values(), gift_id))
        if "location_id" in data:
            locations.set_gift_location(con, gift_id, data["location_id"])
        if status is not None:
            _apply_status(con, gift_id, status, data, actor)  # writes the order fields too
        else:
            orders.write(con, gift_id, order)
        exchanges.check_gift(con, gift_id)  # the link, new or old, must still be a draw
        refuse_exposing_an_idea(con, gift_id)  # a protected recipient or giver now, on one copy of a shared idea
        if after_write:
            after_write()


def set_status(con, gift_id: int, status: str, order: dict | None = None, actor: str | None = None, *,
               after_write=None) -> None:
    """Any status on the gift's own track may be set directly; one from the other track is 400.

    `order` may carry order fields (orders.FIELDS), stored together with the status. Setting ordered needs seller,
    ship_to_id and eta, from `order` or already on the gift (400 naming what is missing, and nothing changes); it
    records `actor` as ordered_by. Setting arrived stamps arrived_on with today unless the gift or `order` has a
    date. `after_write()` runs inside the transaction after the local write (wishlist.set_status); if it raises,
    nothing is written."""
    _row(con, gift_id)
    with db.transaction(con):
        _apply_status(con, gift_id, status, order or {}, actor)
        if after_write:
            after_write()


def _apply_status(con, gift_id: int, status: str, order: dict, actor: str | None) -> None:
    """set_status inside the caller's transaction."""
    row = _row(con, gift_id)
    trk = track(set(_ids(con, "gift_givers", gift_id)), people.user_person_ids(con), row["giver_name"])
    if status not in _statuses(trk):
        raise ApiError(400, f"On this gift's track, status must be one of {', '.join(_statuses(trk))}.")
    fields = orders.parse(con, order)
    if status == "ordered":
        orders.require_ordered({k: fields[k] if k in fields else row[k] for k in orders.REQUIRED_FOR_ORDERED})
        fields["ordered_by"] = actor or row["ordered_by"]
    if status == "arrived" and not (fields["arrived_on"] if "arrived_on" in fields else row["arrived_on"]):
        fields["arrived_on"] = common.today()
    orders.write(con, gift_id, {**fields, "status": status})


def set_givers(con, gift_id: int, giver_ids: list[int]) -> None:
    ids = _person_ids(con, {"giver_ids": list(giver_ids)}, "giver_ids")
    _row(con, gift_id)
    with db.transaction(con):
        _set_givers(con, gift_id, ids)


def _set_givers(con, gift_id: int, giver_ids: list[int], *, giver_name=ABSENT) -> None:
    """Replace the givers, and the typed name unless it is ABSENT (kept). When that moves the gift to the other track
    its status maps across (to theirs: told, to ours: decided; given stays given), and a gift no longer from an owner
    loses Santa. Runs inside the caller's transaction; the caller refuses a typed name on a gift for an exchange."""
    row = _row(con, gift_id)
    name = row["giver_name"] if giver_name is ABSENT else giver_name
    user_ids = people.user_person_ids(con)
    before = track(set(_ids(con, "gift_givers", gift_id)), user_ids, row["giver_name"])
    after = track(set(giver_ids), user_ids, name)
    con.execute("DELETE FROM gift_givers WHERE gift_id = ?", (gift_id,))
    con.executemany("INSERT INTO gift_givers (gift_id, person_id) VALUES (?, ?)", [(gift_id, p) for p in giver_ids])
    refuse_exposing_an_idea(con, gift_id)  # a protected giver on a gift for an exchange
    status, santa, told_at = row["status"], row["santa"], row["told_at"]
    if after != before and status != "given":
        status = "told" if after == "theirs" else "decided"
    if after == "theirs" and not told_at:
        told_at = common.today()
    if santa and not (after == "ours" and set(giver_ids) & user_ids):
        santa = 0  # Santa needs a giver who is an owner
    con.execute("UPDATE gifts SET status = ?, santa = ?, told_at = ?, giver_name = ?, updated_at = ? WHERE id = ?",
                (status, santa, told_at, name, common.now(), gift_id))


def delete(con, gift_id: int) -> None:
    if not con.execute("DELETE FROM gifts WHERE id = ?", (gift_id,)).rowcount:
        raise ApiError(404, "No such gift.")
