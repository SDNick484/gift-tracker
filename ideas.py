"""Ideas: a gift-to-be with no occasion, giver or status, tagged with one or more people. Each tagged person has a
copy (an `idea_people` row) that is open, or taken once a gift was made from it for that person (its gift_id; a
deleted gift reopens it by ON DELETE SET NULL). Route-free: every function takes a sqlite3.Connection and raises
common.ApiError on bad input.

Visibility mirrors gifts.py: an idea with any protected person tagged, or with any copy taken by a protected gift, is
protected. While locked it does not exist: lists take a required `unlocked` and filter on PROTECTED_SQL, and app.py
checks visible() before any read or write of one idea. create/update/delete/take/to_idea do not look at the lock;
app.py refuses locked writes that would tag a protected person. share_text takes `unlocked` like a list. No write may
leave a hidden idea with a visible gift taken from it (EXPOSED_SQL, exposed()).

Taking a copy makes a gift for that one person (take); the copy reopens when the gift is deleted (ON DELETE SET NULL,
which is also "back to ideas"), or when that person is taken off the gift (gifts.update). to_idea turns an unassigned
gift back into an idea."""
import sqlite3

import common
import db
import gifts
import occasions
import people
from common import ApiError, flag, locked

# True for an idea (aliased `i` in the enclosing query) with any protected person tagged, or any copy whose gift is
# protected (gifts.PROTECTED_SQL, embedded unchanged over the inner alias `g`): "taken" must never hint at a hidden
# gift. Parenthesised as a whole so `NOT {PROTECTED_SQL}` holds.
PROTECTED_SQL = ("(EXISTS (SELECT 1 FROM idea_people ip JOIN people ipp ON ipp.id = ip.person_id "
                 "WHERE ip.idea_id = i.id AND ipp.protected = 1) "
                 "OR EXISTS (SELECT 1 FROM idea_people iq JOIN gifts g ON g.id = iq.gift_id "
                 f"WHERE iq.idea_id = i.id AND {gifts.PROTECTED_SQL}))")


# True for an idea (alias `i`) that is protected while some taken copy's gift is not: that gift would be visible while
# locked and point at an idea that does not exist (from_idea, back to ideas, move to ideas would all tell). No write may
# leave an idea in this state; each writer that could checks exposed() inside its transaction.
EXPOSED_SQL = (f"({PROTECTED_SQL} AND EXISTS (SELECT 1 FROM idea_people ix JOIN gifts g ON g.id = ix.gift_id "
               f"WHERE ix.idea_id = i.id AND NOT {gifts.PROTECTED_SQL}))")


def exposed(con, *, idea_id: int | None = None, gift_id: int | None = None) -> bool:
    """Whether some idea is in the EXPOSED_SQL state: that idea, the ideas this gift is a copy of, or (no argument)
    any idea. Never in school mode, which has no lock (nor ideas)."""
    if common.is_school():
        return False
    scope, params = "1", ()
    if idea_id is not None:
        scope, params = "i.id = ?", (idea_id,)
    elif gift_id is not None:
        scope, params = "EXISTS (SELECT 1 FROM idea_people iz WHERE iz.idea_id = i.id AND iz.gift_id = ?)", (gift_id,)
    return con.execute(f"SELECT 1 FROM ideas i WHERE {scope} AND {EXPOSED_SQL}", params).fetchone() is not None


# ---------------------------------------------------------------- reading
def _row(con, idea_id: int):
    row = con.execute("SELECT * FROM ideas WHERE id = ?", (idea_id,)).fetchone()
    if row is None:
        raise ApiError(404, "No such idea.")
    return row


def is_protected(con, idea_id: int) -> bool:
    """The same rule as PROTECTED_SQL."""
    return con.execute("SELECT 1 FROM ideas i WHERE i.id = ? AND " + PROTECTED_SQL, (idea_id,)).fetchone() is not None


def visible(con, idea_id: int, *, unlocked: bool) -> bool:
    """Whether this request may see or change the idea. A missing idea counts as visible, so the caller's own lookup
    answers 404 exactly as it would for a protected idea while locked."""
    return unlocked or not is_protected(con, idea_id)


HELD = "Someone already has this on the wishlist. Reload."
ALSO_LOCKED = "This idea is also for someone behind the lock. Make a separate idea for this person."
WISH_ONE_PERSON = "A wish is for one person."
ON_A_BOARD = "This wish is already on a board. Reload."


def _wishlist():
    import wishlist  # late: wishlist imports boards, which imports this module
    return wishlist


def _held(con, row) -> str | None:
    """Who holds the idea's wish on the wishlist (reserved or purchased), or None. The one place the held state comes
    from: wishlist.held, which reads the cache and is None when the link is off."""
    return _wishlist().held(con, row["wishlist_item_id"]) if row["wishlist_item_id"] is not None else None


def _check_wish_person(con, item_id: int, tagged: list[int]) -> None:
    """A wish-linked idea is for exactly the person whose wishlist tab the wish is on (as the cache shows it)."""
    it = _wishlist().item(con, item_id)
    tab = con.execute("SELECT wishlist_person_id FROM people WHERE id = ?", (tagged[0],)).fetchone() \
        if len(tagged) == 1 else None
    if it is None or tab is None or tab[0] != it["tab_id"]:
        raise ApiError(400, WISH_ONE_PERSON)


def copy_state(con, row, person_id: int) -> tuple[str, str | None]:
    """(state, held_by) of `person_id`'s copy of the idea `row`: "taken" when a gift was made from it, "held" when its
    wish is held on the wishlist (held_by names the holder), else "open"."""
    copy = con.execute("SELECT gift_id FROM idea_people WHERE idea_id = ? AND person_id = ?",
                       (row["id"], person_id)).fetchone()
    if copy is not None and copy["gift_id"] is not None:
        return "taken", None
    held_by = _held(con, row)
    return ("held", held_by) if held_by else ("open", None)


def _json(con, row) -> dict:
    copies = con.execute(
        "SELECT ip.person_id, ip.gift_id, g.occasion_id FROM idea_people ip JOIN people p ON p.id = ip.person_id "
        "LEFT JOIN gifts g ON g.id = ip.gift_id WHERE ip.idea_id = ? ORDER BY p.sort_order, p.id", (row["id"],))
    people = []
    for c in copies:
        state, held_by = copy_state(con, row, c["person_id"])
        people.append({"person_id": c["person_id"], "state": state, "gift_id": c["gift_id"],
                       "occasion_id": c["occasion_id"], "held_by": held_by})
    return {"id": row["id"], "title": row["title"], "url": row["url"], "store": row["store"],
            "price_cents": row["price_cents"], "notes": row["notes"], "share": bool(row["share"]),
            "wishlist_item_id": row["wishlist_item_id"], "created_by": row["created_by"],
            "updated_at": row["updated_at"], "people": people}


def get_json(con, idea_id: int) -> dict:
    """The idea as JSON, or 404. Does not look at the lock: routes check visible() first."""
    return _json(con, _row(con, idea_id))


def list_json(con, *, unlocked: bool, person_id: int | None = None) -> list[dict]:
    """Every idea (or every idea tagged with `person_id`), oldest first. While locked, protected ideas are left out,
    so a protected person's list is empty."""
    where, params = f"WHERE (? OR NOT {PROTECTED_SQL})", [int(unlocked)]
    if person_id is not None:
        where += " AND EXISTS (SELECT 1 FROM idea_people t WHERE t.idea_id = i.id AND t.person_id = ?)"
        params.append(person_id)
    return [_json(con, r) for r in con.execute(f"SELECT i.* FROM ideas i {where} ORDER BY i.id", params)]


# ---------------------------------------------------------------- writing
def _fields(data: dict, partial: bool) -> dict:
    f = gifts.item_fields(data, partial, "An idea")
    share = flag(data, "share")
    if share is not None:
        f["share"] = int(share)
    return f


def create(con, user: dict, data: dict, *, wishlist_item_id: int | None = None) -> int:
    """`wishlist_item_id` links the idea to a wish (the wishlist link only; never read from a request body). One idea
    per wish: a second link raises sqlite3.IntegrityError and nothing is created."""
    f = _fields(data, partial=False)
    tagged = gifts._person_ids(con, data, "person_ids")
    if not tagged:
        raise ApiError(400, "An idea needs at least one person.")
    if wishlist_item_id is not None:
        _check_wish_person(con, wishlist_item_id, tagged)
    now = common.now()
    with db.transaction(con):
        iid = con.execute(
            "INSERT INTO ideas (title, url, store, price_cents, notes, share, wishlist_item_id, created_by, created_at, "
            "updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (f["title"], f.get("url"), f.get("store"), f.get("price_cents"), f.get("notes"), f.get("share", 0),
             wishlist_item_id, user["email"], now, now)).lastrowid
        con.executemany("INSERT INTO idea_people (idea_id, person_id) VALUES (?, ?)", [(iid, p) for p in tagged])
    return iid


def update(con, idea_id: int, data: dict) -> bool:
    """Change title, url, store, price, notes, share or the tagged people. Tags change as a set: a person who stays
    keeps their copy (taken or not); untagging a taken copy drops it and leaves the gift alone. No people left
    deletes the idea, and the answer is True. All of it applies or none of it."""
    row = _row(con, idea_id)
    f = _fields(data, partial=True)
    tagged = gifts._person_ids(con, data, "person_ids")
    if tagged and row["wishlist_item_id"] is not None:
        have = {r[0] for r in con.execute("SELECT person_id FROM idea_people WHERE idea_id = ?", (idea_id,))}
        if set(tagged) != have:  # an unchanged set is fine even when the cache no longer knows the wish
            _check_wish_person(con, row["wishlist_item_id"], tagged)
    with db.transaction(con):
        if tagged is not None and not tagged:
            con.execute("DELETE FROM ideas WHERE id = ?", (idea_id,))
            return True
        if tagged is not None:
            have = {r[0] for r in con.execute("SELECT person_id FROM idea_people WHERE idea_id = ?", (idea_id,))}
            con.executemany("DELETE FROM idea_people WHERE idea_id = ? AND person_id = ?",
                            [(idea_id, p) for p in have - set(tagged)])
            con.executemany("INSERT INTO idea_people (idea_id, person_id) VALUES (?, ?)",
                            [(idea_id, p) for p in tagged if p not in have])
            if exposed(con, idea_id=idea_id):  # a protected person tagged beside a copy taken for someone else
                raise ApiError(400, ALSO_LOCKED)
        f["updated_at"] = common.now()
        con.execute(f"UPDATE ideas SET {', '.join(f'{k} = ?' for k in f)} WHERE id = ?", (*f.values(), idea_id))
    return False


def delete(con, idea_id: int) -> None:
    """Delete the idea and its copies. Gifts taken from it stay."""
    if not con.execute("DELETE FROM ideas WHERE id = ?", (idea_id,)).rowcount:
        raise ApiError(404, "No such idea.")


# ---------------------------------------------------------------- taking a copy, and back
def is_idea_gift(con, gift_id: int) -> bool:
    """Whether some copy of an idea was taken by this gift (so "back to ideas" applies)."""
    return gifts.from_idea(con, gift_id)


def take(con, user: dict, idea_id: int, person_id: int, data: dict) -> int:
    """Make a gift from `person_id`'s copy of the idea and mark the copy taken; answers the gift id. `data` is
    {giver_ids, giver_name?, occasion_id?}: someone must give it (people or a typed name), and the occasion defaults
    to occasions.current(). The gift carries the idea's title, link, store, price and notes, for that person alone.
    404 when the idea is missing or not for that person, 409 when the copy is taken or held. Two owners taking the same
    copy at once: gifts.create marks it inside its transaction, so the second gets 409 and no gift."""
    row = _row(con, idea_id)
    if not con.execute("SELECT 1 FROM idea_people WHERE idea_id = ? AND person_id = ?", (idea_id, person_id)).fetchone():
        raise ApiError(404, "This idea is not for that person.")
    state, _ = copy_state(con, row, person_id)
    if state == "taken":
        raise ApiError(409, gifts.TAKEN)
    if state == "held":
        raise ApiError(409, HELD)
    if is_protected(con, idea_id) and person_id not in people.protected_ids(con):
        # the gift would be visible while locked and point at a hidden idea (gifts.create checks again in its
        # transaction); a protected person's own gift is protected itself
        raise ApiError(400, ALSO_LOCKED)
    giver_ids = gifts._person_ids(con, data, "giver_ids") or []
    name = gifts.giver_name_of(data)
    name = None if name is gifts.ABSENT else name
    if not giver_ids and not name:
        raise ApiError(400, "Say who is giving it.")
    if "occasion_id" in data:
        occasion_id = data["occasion_id"]  # gifts.create checks it
    else:
        current = occasions.current(con)
        if current is None:
            raise ApiError(400, "Create an occasion first.")
        occasion_id = current["id"]
    gift = {"occasion_id": occasion_id, "title": row["title"], "url": row["url"] or "", "store": row["store"] or "",
            "notes": row["notes"] or "", "price": row["price_cents"], "recipient_ids": [person_id],
            "giver_ids": giver_ids, "giver_name": name}
    try:
        return gifts.create(con, user, gift, wishlist_item_id=row["wishlist_item_id"], idea_copy=(idea_id, person_id))
    except sqlite3.IntegrityError:
        if row["wishlist_item_id"] is None:
            raise
        raise ApiError(409, ON_A_BOARD) from None  # a gift already names the wish


def to_idea(con, user: dict, gift_id: int) -> int:
    """Turn an unassigned gift into an idea and delete the gift, in one transaction; answers the idea id. Its
    recipients become the tagged people, with the same title, link, store, price, notes and wish; share is off. The
    wish moves from the gift to the idea, and an unassigned gift holds no reservation, so the wishlist hears nothing.
    400 for a gift taken from an idea (send it back instead), a gift someone gives, or a wish-linked gift for several
    people (a wish is for one person)."""
    gifts.get(con, gift_id)  # 404
    now = common.now()
    try:
        with db.transaction(con):
            # Every check is read inside the transaction: an owner assigning the gift meanwhile (which may reserve its
            # wish) is either fully before it, and refused here, or waits for it.
            row = gifts.get(con, gift_id)
            if is_idea_gift(con, gift_id):
                raise ApiError(400, "This gift came from an idea: send it back to ideas instead.")
            recipients = gifts._ids(con, "gift_recipients", gift_id)
            if gifts._ids(con, "gift_givers", gift_id) or row["giver_name"]:
                raise ApiError(400, "Only a gift nobody is giving yet can move to ideas.")
            if row["wishlist_item_id"] is not None and len(recipients) > 1:
                raise ApiError(400, "Remove the other recipients first.")
            # Directly, not through wishlist.delete_gift: an unassigned gift holds no wishlist reservation (the
            # decision under "Reopening"), so there is nothing to release. First: the idea takes over the wish link.
            gifts.delete(con, gift_id)
            iid = con.execute(
                "INSERT INTO ideas (title, url, store, price_cents, notes, share, wishlist_item_id, created_by, "
                "created_at, updated_at) VALUES (?, ?, ?, ?, ?, 0, ?, ?, ?, ?)",
                (row["title"], row["url"], row["store"], row["price_cents"], row["notes"], row["wishlist_item_id"],
                 user["email"], now, now)).lastrowid
            con.executemany("INSERT INTO idea_people (idea_id, person_id) VALUES (?, ?)",
                            [(iid, p) for p in recipients])
    except sqlite3.IntegrityError:  # another idea already names this wish; the gift stays
        raise ApiError(409, "This wish is already an idea. Reload.") from None
    return iid


# ---------------------------------------------------------------- the share list
def share_text(con, person_ids: list[int] | None, *, unlocked: bool) -> str:
    """The shared ideas still open for each person, as text to paste to family: per person (people sort order) with at
    least one, their name and then "- <title>[ - <store>][ - <url>]" per idea (by id), a blank line between people.
    `person_ids` None means everyone this request may see. Naming a missing person is 400, a protected one while
    locked 423. Protected ideas are left out while locked, whoever they are for."""
    if person_ids is not None:
        for pid in person_ids:
            if not con.execute("SELECT 1 FROM people WHERE id = ?", (pid,)).fetchone():
                raise ApiError(400, "No such person.")
        if not unlocked and people.protected_ids(con) & set(person_ids):
            raise locked()
    wanted = None if person_ids is None else set(person_ids)
    rows = con.execute(f"SELECT i.* FROM ideas i WHERE i.share = 1 AND (? OR NOT {PROTECTED_SQL}) ORDER BY i.id",
                       (int(unlocked),)).fetchall()
    lines: dict[int, list[str]] = {}
    for row in rows:
        line = "- " + "".join([row["title"], *(f" - {row[k]}" for k in ("store", "url") if row[k])])
        for c in con.execute("SELECT person_id FROM idea_people WHERE idea_id = ?", (row["id"],)):
            pid = c["person_id"]
            # a protected person is tagged only on protected ideas, which the query leaves out while locked
            if (wanted is None or pid in wanted) and copy_state(con, row, pid)[0] == "open":
                lines.setdefault(pid, []).append(line)
    blocks = [f"{p['name']}\n" + "\n".join(lines[p["id"]])
              for p in people.list_people(con, include_inactive=True) if p["id"] in lines]
    return "\n\n".join(blocks)
