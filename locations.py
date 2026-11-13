"""Hiding places: a self-referencing tree of any depth with no required layer (Garage > Top shelf > Blue bin), the
same shape as the locations of SDNick484/matinee. Route-free: every function takes a sqlite3.Connection and raises
common.ApiError on bad input.

A location is never its own ancestor (update refuses it). Nothing here says which gifts sit where: tree() is the
places only, so it is safe to show while locked; lists.where() is the view that places gifts, and it filters on the
lock. delete() looks at every gift, so app.py requires the unlock for it."""
import db
from common import ApiError, text, whole

SEP = " › "  # Garage › Top shelf › Blue bin
MAX_NAME = 100


def _sort_key(r):
    return (r["sort_order"], r["name"].lower(), r["id"])


def _children(con) -> dict:
    kids: dict = {}
    for r in sorted(con.execute("SELECT id, parent_id, name, sort_order FROM locations").fetchall(), key=_sort_key):
        kids.setdefault(r["parent_id"], []).append(r)
    return kids


def tree(con) -> list[dict]:
    """Every location, depth first, siblings by sort_order then name. Each has `path` ("Garage › Top shelf") and
    `depth` (0 for a top-level location)."""
    kids, out, seen = _children(con), [], set()
    stack = [(r, "", 0) for r in reversed(kids.get(None, []))]
    while stack:
        r, prefix, depth = stack.pop()
        if r["id"] in seen:
            continue
        seen.add(r["id"])
        path = f"{prefix}{SEP}{r['name']}" if prefix else r["name"]
        out.append({"id": r["id"], "parent_id": r["parent_id"], "name": r["name"], "sort_order": r["sort_order"],
                    "path": path, "depth": depth})
        stack.extend((c, path, depth + 1) for c in reversed(kids.get(r["id"], [])))
    return out


def path_map(con) -> dict[int, str]:
    return {loc["id"]: loc["path"] for loc in tree(con)}


def descendant_ids(con, location_id: int) -> set[int]:
    """The location and everything below it."""
    kids, found, queue = _children(con), set(), [location_id]
    while queue:
        cur = queue.pop()
        if cur not in found:
            found.add(cur)
            queue.extend(c["id"] for c in kids.get(cur, []))
    return found


def exists(con, location_id) -> bool:
    return con.execute("SELECT 1 FROM locations WHERE id = ?", (location_id,)).fetchone() is not None


def _item(con, location_id: int) -> dict:
    return next(loc for loc in tree(con) if loc["id"] == location_id)


def _name(data: dict) -> str:
    name = text(data, "name")
    if not name or len(name) > MAX_NAME:
        raise ApiError(400, f"A location needs a name of 1 to {MAX_NAME} characters.")
    return name


def _parent(con, data: dict):
    """data["parent_id"] as an existing location id or None."""
    pid = whole(data, "parent_id")
    if pid is not None and not exists(con, pid):
        raise ApiError(400, "No such parent location.")
    return pid


def create(con, data: dict) -> dict:
    name, parent_id, order = _name(data), _parent(con, data), whole(data, "sort_order", 0)
    lid = con.execute("INSERT INTO locations (parent_id, name, sort_order) VALUES (?, ?, ?)",
                      (parent_id, name, order)).lastrowid
    return _item(con, lid)


def update(con, location_id: int, data: dict) -> dict:
    """Rename, reorder or move. A move under the location itself or any of its descendants is 400, so a
    location is never its own ancestor."""
    row = con.execute("SELECT * FROM locations WHERE id = ?", (location_id,)).fetchone()
    if row is None:
        raise ApiError(404, "No such location.")
    name = _name(data) if "name" in data else row["name"]
    order = whole(data, "sort_order", row["sort_order"]) if "sort_order" in data else row["sort_order"]
    parent_id = row["parent_id"]
    if "parent_id" in data:
        parent_id = _parent(con, data)
        if parent_id is not None and parent_id in descendant_ids(con, location_id):
            raise ApiError(400, "A location cannot be inside itself or one of its own sub-locations.")
    con.execute("UPDATE locations SET name = ?, parent_id = ?, sort_order = ? WHERE id = ?",
                (name, parent_id, order, location_id))
    return _item(con, location_id)


def delete(con, location_id: int) -> None:
    """The location and its sub-locations go. 409 when any gift sits in any of them: move it first."""
    if not exists(con, location_id):
        raise ApiError(404, "No such location.")
    ids = sorted(descendant_ids(con, location_id))
    marks = ",".join("?" * len(ids))
    if con.execute(f"SELECT 1 FROM gifts WHERE location_id IN ({marks}) LIMIT 1", ids).fetchone():
        raise ApiError(409, "Something is hidden there. Move it first.")
    with db.transaction(con):
        con.execute("DELETE FROM locations WHERE id = ?", (location_id,))  # ON DELETE CASCADE takes the rest


def set_gift_location(con, gift_id: int, location_id) -> None:
    """Hide the gift in a location, or unplace it with None. 400 for a location that does not exist. Does not look
    at the lock: routes check visibility of the gift first."""
    if location_id is not None:
        if isinstance(location_id, bool) or not isinstance(location_id, int):
            raise ApiError(400, "location_id must be a whole number or null.")
        if not exists(con, location_id):
            raise ApiError(400, "No such location.")
    con.execute("UPDATE gifts SET location_id = ? WHERE id = ?", (location_id, gift_id))
