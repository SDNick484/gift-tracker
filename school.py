"""School mode: the staff-by-gift matrix, and the per-recipient detail and done flag behind it. Route-free: every
function takes a sqlite3.Connection and raises common.ApiError on bad input; app.py holds the routes and wraps them
in school_only, so none of this is reachable on a family instance.

A school gift is an ordinary gift with many recipients. Each recipient row (gift_recipients) carries a free-text
`detail` ("$35 · coffee shop") and a `done` flag. A gift always keeps at least one recipient, as everywhere else.
Nothing is protected in school mode, so there is no lock filtering here beyond the `unlocked` argument that
matrix() passes on to the shared PROTECTED_SQL filter."""
import common
import db
import gifts
import occasions
import people
from common import ApiError, flag, text

MAX_DETAIL = 200
LAST_RECIPIENT = "A gift needs at least one recipient. Delete the gift instead."


def _counts(con, gift_id: int) -> dict:
    """recipient_count and done_count of one column."""
    row = con.execute("SELECT COUNT(*) AS n, COALESCE(SUM(done), 0) AS done FROM gift_recipients WHERE gift_id = ?",
                      (gift_id,)).fetchone()
    return {"recipient_count": row["n"], "done_count": row["done"]}


def _touch(con, gift_id: int) -> None:
    con.execute("UPDATE gifts SET updated_at = ? WHERE id = ?", (common.now(), gift_id))


# ---------------------------------------------------------------- the matrix
def matrix(con, occasion_id: int, *, unlocked: bool) -> dict:
    """One occasion as a grid. `gifts` are the columns (by id), `staff` the rows (by sort_order, id) and `cells`
    the ticks (by gift, then person). Rows: everyone in the occasion with a plan other than skip, plus anyone who
    receives one of its gifts, skipped or not. A column's counts are over all its recipients."""
    occ = occasions.get(con, occasion_id)
    columns = con.execute(
        "SELECT g.id, g.title, g.status, COUNT(r.person_id) AS recipient_count, COALESCE(SUM(r.done), 0) AS done_count "
        f"FROM gifts g LEFT JOIN gift_recipients r ON r.gift_id = g.id WHERE g.occasion_id = ? AND (? OR NOT {gifts.PROTECTED_SQL}) "
        "GROUP BY g.id ORDER BY g.id", (occasion_id, int(unlocked))).fetchall()
    shown = {c["id"] for c in columns}
    cells = [r for r in con.execute(
        "SELECT r.gift_id, r.person_id, r.detail, r.done FROM gift_recipients r JOIN gifts g ON g.id = r.gift_id "
        "WHERE g.occasion_id = ? ORDER BY r.gift_id, r.person_id", (occasion_id,)) if r["gift_id"] in shown]
    plans = {r["person_id"]: r["plan"] for r in con.execute("SELECT person_id, plan FROM occasion_people WHERE occasion_id = ?", (occasion_id,))}
    wanted = {pid for pid, plan in plans.items() if plan != "skip"} | {c["person_id"] for c in cells}
    rows = [p for p in people.list_people(con, include_inactive=True) if p["id"] in wanted]
    return {"occasion": {"id": occ["id"], "name": occ["name"], "kind": occ["kind"], "date": occ["date"], "archived": bool(occ["archived"])},
            "gifts": [{"id": c["id"], "title": c["title"], "status": c["status"], "recipient_count": c["recipient_count"],
                       "done_count": c["done_count"]} for c in columns],
            "staff": [{"id": p["id"], "name": p["name"], "role": p.get("role"), "color": p["color"], "active": p["active"],
                       "plan": plans.get(p["id"])} for p in rows],
            "cells": [{"gift_id": c["gift_id"], "person_id": c["person_id"], "detail": c["detail"], "done": bool(c["done"])}
                      for c in cells]}


# ---------------------------------------------------------------- one cell
def _detail(data: dict) -> str | None:
    value = text(data, "detail")
    if len(value) > MAX_DETAIL:
        raise ApiError(400, f"detail can be at most {MAX_DETAIL} characters.")
    return value or None


def set_cell(con, gift_id: int, person_id: int, data: dict) -> tuple[dict, bool]:
    """Add the person as a recipient of the gift, or update their row. Only the keys present in `data` change:
    `detail` (text; empty or null clears it) and `done` (true or false; null leaves it). A new row starts with no
    detail and not done unless the body says otherwise. Returns the cell with the column's counts, and whether the
    row was created."""
    gifts.get(con, gift_id)  # 404
    people.get_person(con, person_id)  # 404
    detail = _detail(data) if "detail" in data else None
    done = flag(data, "done")
    with db.transaction(con):
        row = con.execute("SELECT detail, done FROM gift_recipients WHERE gift_id = ? AND person_id = ?", (gift_id, person_id)).fetchone()
        created = row is None
        if created:
            con.execute("INSERT INTO gift_recipients (gift_id, person_id, detail, done) VALUES (?, ?, ?, ?)",
                        (gift_id, person_id, detail, int(bool(done))))
        else:
            new_detail = detail if "detail" in data else row["detail"]
            new_done = row["done"] if done is None else int(done)
            con.execute("UPDATE gift_recipients SET detail = ?, done = ? WHERE gift_id = ? AND person_id = ?",
                        (new_detail, new_done, gift_id, person_id))
        _touch(con, gift_id)
        now = con.execute("SELECT detail, done FROM gift_recipients WHERE gift_id = ? AND person_id = ?", (gift_id, person_id)).fetchone()
        counts = _counts(con, gift_id)
    return {"gift_id": gift_id, "person_id": person_id, "detail": now["detail"], "done": bool(now["done"]), **counts}, created


def remove_cell(con, gift_id: int, person_id: int) -> dict:
    """Take the person off the gift. 404 when they are not a recipient; 409 when they are the only one."""
    gifts.get(con, gift_id)  # 404
    with db.transaction(con):
        if not con.execute("SELECT 1 FROM gift_recipients WHERE gift_id = ? AND person_id = ?", (gift_id, person_id)).fetchone():
            raise ApiError(404, "That person is not a recipient of this gift.")
        if _counts(con, gift_id)["recipient_count"] <= 1:
            raise ApiError(409, LAST_RECIPIENT)
        con.execute("DELETE FROM gift_recipients WHERE gift_id = ? AND person_id = ?", (gift_id, person_id))
        _touch(con, gift_id)
        counts = _counts(con, gift_id)
    return {"gift_id": gift_id, "person_id": person_id, **counts}


def add_role(con, gift_id: int, data: dict) -> dict:
    """Make everyone with this role (active people only, compared ignoring case) a recipient of the gift. People
    who already are keep their detail and done flag. A role nobody has adds nobody."""
    gifts.get(con, gift_id)  # 404
    role = text(data, "role")
    if not role or len(role) > people.MAX_ROLE:
        raise ApiError(400, f"role must be 1 to {people.MAX_ROLE} characters.")
    with db.transaction(con):
        have = {r[0] for r in con.execute("SELECT person_id FROM gift_recipients WHERE gift_id = ?", (gift_id,))}
        added = [r["id"] for r in con.execute("SELECT id FROM people WHERE active = 1 AND lower(role) = lower(?) ORDER BY id", (role,))
                 if r["id"] not in have]
        con.executemany("INSERT INTO gift_recipients (gift_id, person_id) VALUES (?, ?)", [(gift_id, pid) for pid in added])
        if added:
            _touch(con, gift_id)
        counts = _counts(con, gift_id)
    return {"added": added, **counts}
