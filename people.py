"""Groups, people, households and the seeded users. Route-free: every function takes a sqlite3.Connection and
raises common.ApiError on bad input."""
import re
import sqlite3

import db
from common import ApiError, flag, text, whole

COLOR = re.compile(r"#[0-9a-fA-F]{6}")
US = "Us"  # the group and household the owners are seeded into


# ---------------------------------------------------------------- start-up
def ensure_users(con, users: dict[str, str]) -> None:
    """For each configured user that has no `users` row: a person (group and household "Us") and the row.
    Idempotent. A user whose row exists keeps their person, whatever it has been renamed to."""
    with db.transaction(con):
        con.execute("INSERT OR IGNORE INTO groups (name) VALUES (?)", (US,))
        group_id = con.execute("SELECT id FROM groups WHERE name = ?", (US,)).fetchone()["id"]
        for email, name in users.items():
            if con.execute("SELECT 1 FROM users WHERE email = ?", (email,)).fetchone():
                continue
            pid = con.execute("INSERT INTO people (name, group_id, household) VALUES (?, ?, ?)",
                              (name, group_id, US)).lastrowid
            con.execute("INSERT INTO users (email, person_id) VALUES (?, ?)", (email, pid))


def user_person_ids(con) -> set[int]:
    return {r["person_id"] for r in con.execute("SELECT person_id FROM users")}


# ---------------------------------------------------------------- groups
def _group(row) -> dict:
    return {"id": row["id"], "name": row["name"], "sort_order": row["sort_order"]}


def list_groups(con) -> list[dict]:
    return [_group(r) for r in con.execute("SELECT * FROM groups ORDER BY sort_order, id")]


def _group_fields(data: dict, partial: bool) -> dict:
    out = {}
    if not partial or "name" in data:
        name = text(data, "name")
        if not name or len(name) > 100:
            raise ApiError(400, "A group needs a name of 1 to 100 characters.")
        out["name"] = name
    if "sort_order" in data:
        out["sort_order"] = whole(data, "sort_order", 0)
    return out


def _write_group(con, sql: str, params: tuple):
    try:
        return con.execute(sql, params)
    except sqlite3.IntegrityError:
        raise ApiError(409, "A group with that name already exists.") from None


def create_group(con, data: dict) -> dict:
    f = _group_fields(data, partial=False)
    cur = _write_group(con, "INSERT INTO groups (name, sort_order) VALUES (?, ?)", (f["name"], f.get("sort_order", 0)))
    return _group(con.execute("SELECT * FROM groups WHERE id = ?", (cur.lastrowid,)).fetchone())


def update_group(con, group_id: int, data: dict) -> dict:
    if not con.execute("SELECT 1 FROM groups WHERE id = ?", (group_id,)).fetchone():
        raise ApiError(404, "No such group.")
    f = _group_fields(data, partial=True)
    if f:
        _write_group(con, f"UPDATE groups SET {', '.join(f'{k} = ?' for k in f)} WHERE id = ?", (*f.values(), group_id))
    return _group(con.execute("SELECT * FROM groups WHERE id = ?", (group_id,)).fetchone())


def delete_group(con, group_id: int) -> None:
    """People in the group are kept and become ungrouped (ON DELETE SET NULL)."""
    if not con.execute("DELETE FROM groups WHERE id = ?", (group_id,)).rowcount:
        raise ApiError(404, "No such group.")


# ---------------------------------------------------------------- people
def list_people(con, include_inactive: bool = False) -> list[dict]:
    where = "" if include_inactive else "WHERE p.active = 1"
    rows = con.execute(
        f"SELECT p.*, EXISTS (SELECT 1 FROM users u WHERE u.person_id = p.id) AS is_user FROM people p {where} "
        "ORDER BY p.sort_order, p.id")
    return [_person(r) for r in rows]


def get_person(con, person_id: int) -> dict:
    row = con.execute(
        "SELECT p.*, EXISTS (SELECT 1 FROM users u WHERE u.person_id = p.id) AS is_user FROM people p WHERE p.id = ?",
        (person_id,)).fetchone()
    if row is None:
        raise ApiError(404, "No such person.")
    return _person(row)


def _person(row) -> dict:
    return {"id": row["id"], "name": row["name"], "group_id": row["group_id"], "household": row["household"],
            "color": row["color"], "protected": bool(row["protected"]), "active": bool(row["active"]),
            "sort_order": row["sort_order"], "is_user": bool(row["is_user"]),
            "wishlist_person_id": row["wishlist_person_id"], "notes": row["notes"]}


def _person_fields(con, data: dict, partial: bool) -> dict:
    out = {}
    if not partial or "name" in data:
        name = text(data, "name")
        if not name or len(name) > 100:
            raise ApiError(400, "A person needs a name of 1 to 100 characters.")
        out["name"] = name
    if "group_id" in data:
        gid = whole(data, "group_id")
        if gid is not None and not con.execute("SELECT 1 FROM groups WHERE id = ?", (gid,)).fetchone():
            raise ApiError(400, "No such group.")
        out["group_id"] = gid
    if "household" in data:
        household = text(data, "household")
        if len(household) > 100:
            raise ApiError(400, "household is too long.")
        out["household"] = household or None
    if "color" in data:
        color = data["color"]
        if color is not None and not (isinstance(color, str) and COLOR.fullmatch(color)):
            raise ApiError(400, "color must look like #1a2b3c.")
        out["color"] = color
    for key in ("protected", "active"):
        if key in data:
            out[key] = int(flag(data, key, key == "active"))
    if "wishlist_person_id" in data:
        out["wishlist_person_id"] = whole(data, "wishlist_person_id")
    if "notes" in data:
        notes = text(data, "notes")
        if len(notes) > 2000:
            raise ApiError(400, "notes is too long.")
        out["notes"] = notes or None
    if "sort_order" in data:
        out["sort_order"] = whole(data, "sort_order", 0)
    return out


def create_person(con, data: dict) -> dict:
    f = _person_fields(con, data, partial=False)
    cur = con.execute(f"INSERT INTO people ({', '.join(f)}) VALUES ({', '.join('?' * len(f))})", tuple(f.values()))
    return get_person(con, cur.lastrowid)


def update_person(con, person_id: int, data: dict) -> dict:
    get_person(con, person_id)  # 404
    f = _person_fields(con, data, partial=True)
    if f:
        con.execute(f"UPDATE people SET {', '.join(f'{k} = ?' for k in f)} WHERE id = ?", (*f.values(), person_id))
    return get_person(con, person_id)


def delete_person(con, person_id: int) -> None:
    """Refused for an owner's own person and for anyone a gift refers to: deactivate them instead, which keeps
    their gifts and history and drops them from new occasions."""
    with db.transaction(con):
        get_person(con, person_id)  # 404
        if person_id in user_person_ids(con):
            raise ApiError(409, "This person is a user of the app and cannot be deleted.")
        used = con.execute("SELECT 1 FROM gift_recipients WHERE person_id = ? UNION ALL "
                           "SELECT 1 FROM gift_givers WHERE person_id = ?", (person_id, person_id)).fetchone()
        if used:
            raise ApiError(409, "This person has gifts. Deactivate them instead.")
        con.execute("DELETE FROM people WHERE id = ?", (person_id,))


def households(con) -> list[dict]:
    """Active people by household label, in order of first appearance. A person with no household is their own
    entry, labelled by name."""
    out: dict[tuple, dict] = {}
    for p in list_people(con):
        key = ("h", p["household"]) if p["household"] else ("p", p["id"])
        entry = out.setdefault(key, {"label": p["household"] or p["name"], "person_ids": []})
        entry["person_ids"].append(p["id"])
    return list(out.values())
