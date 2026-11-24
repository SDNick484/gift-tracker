"""Ideas: a gift-to-be with no occasion, giver or status, tagged with one or more people. Each tagged person has a
copy (an `idea_people` row) that is open, or taken once a gift was made from it for that person (its gift_id; a
deleted gift reopens it by ON DELETE SET NULL). Route-free: every function takes a sqlite3.Connection and raises
common.ApiError on bad input.

Visibility mirrors gifts.py: an idea with any protected person tagged, or with any copy taken by a protected gift, is
protected. While locked it does not exist: lists take a required `unlocked` and filter on PROTECTED_SQL, and app.py
checks visible() before any read or write of one idea. create/update/delete do not look at the lock; app.py refuses
locked writes that would tag a protected person."""
import common
import db
import gifts
from common import ApiError, flag

# True for an idea (aliased `i` in the enclosing query) with any protected person tagged, or any copy whose gift is
# protected (gifts.PROTECTED_SQL, embedded unchanged over the inner alias `g`): "taken" must never hint at a hidden
# gift. Parenthesised as a whole so `NOT {PROTECTED_SQL}` holds.
PROTECTED_SQL = ("(EXISTS (SELECT 1 FROM idea_people ip JOIN people ipp ON ipp.id = ip.person_id "
                 "WHERE ip.idea_id = i.id AND ipp.protected = 1) "
                 "OR EXISTS (SELECT 1 FROM idea_people iq JOIN gifts g ON g.id = iq.gift_id "
                 f"WHERE iq.idea_id = i.id AND {gifts.PROTECTED_SQL}))")


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


def _held(con, row) -> str | None:
    """Who holds the idea's wish on the wishlist (reserved or purchased), or None. The one place the held state comes
    from; the wishlist link fills it in later (Task 5). Until then nothing is held."""
    return None


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
    _row(con, idea_id)
    f = _fields(data, partial=True)
    tagged = gifts._person_ids(con, data, "person_ids")
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
        f["updated_at"] = common.now()
        con.execute(f"UPDATE ideas SET {', '.join(f'{k} = ?' for k in f)} WHERE id = ?", (*f.values(), idea_id))
    return False


def delete(con, idea_id: int) -> None:
    """Delete the idea and its copies. Gifts taken from it stay."""
    if not con.execute("DELETE FROM ideas WHERE id = ?", (idea_id,)).rowcount:
        raise ApiError(404, "No such idea.")
