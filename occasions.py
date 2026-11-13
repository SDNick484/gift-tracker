"""Occasions (Christmas 2026, a birthday, ...), the per-person plan for each, and carry-forward from the
previous occasion. Route-free: every function takes a sqlite3.Connection."""
import re
from datetime import date

import common
import db
from common import ApiError, flag, text, whole

KINDS = ("christmas", "birthday", "graduation", "other")
PLANS = ("individual", "joint", "exchange", "skip")
MAX_BUDGET_CENTS = 10_000_000


def _date(value) -> str:
    if not (isinstance(value, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", value)):
        raise ApiError(400, "date must look like 2026-12-25.")
    try:
        date.fromisoformat(value)
    except ValueError:
        raise ApiError(400, "date must be a real calendar date.") from None
    return value


def _name(data: dict) -> str:
    name = text(data, "name")
    if not name or len(name) > 100:
        raise ApiError(400, "An occasion needs a name of 1 to 100 characters.")
    return name


def _budget(data: dict) -> int | None:
    cents = whole(data, "budget_cents")
    if cents is not None and not 0 <= cents <= MAX_BUDGET_CENTS:
        raise ApiError(400, "budget_cents is out of range.")
    return cents


def get(con, occasion_id: int):
    row = con.execute("SELECT * FROM occasions WHERE id = ?", (occasion_id,)).fetchone()
    if row is None:
        raise ApiError(404, "No such occasion.")
    return row


def create(con, name: str, kind: str, date: str, copy_from: int | None) -> int:
    """A new occasion. With copy_from, the plan and budget of every person who is still active are copied from
    that occasion; no gifts are. Without it the occasion starts with no people."""
    if kind not in KINDS:
        raise ApiError(400, f"kind must be one of {', '.join(KINDS)}.")
    date = _date(date)
    with db.transaction(con):
        if copy_from is not None:
            get(con, copy_from)
        oid = con.execute("INSERT INTO occasions (name, kind, date) VALUES (?, ?, ?)", (name, kind, date)).lastrowid
        if copy_from is not None:
            con.execute(
                "INSERT INTO occasion_people (occasion_id, person_id, plan, budget_cents) "
                "SELECT ?, op.person_id, op.plan, op.budget_cents FROM occasion_people op "
                "JOIN people p ON p.id = op.person_id WHERE op.occasion_id = ? AND p.active = 1",
                (oid, copy_from))
    return oid


def create_from(con, data: dict) -> int:
    """Validate a POST body and create."""
    copy_from = whole(data, "copy_from")
    kind = data.get("kind")
    if not isinstance(kind, str):
        raise ApiError(400, f"kind must be one of {', '.join(KINDS)}.")
    return create(con, _name(data), kind, data.get("date"), copy_from)


def update(con, occasion_id: int, data: dict) -> None:
    get(con, occasion_id)
    fields = {}
    if "name" in data:
        fields["name"] = _name(data)
    if "date" in data:
        fields["date"] = _date(data["date"])
    if "archived" in data:
        fields["archived"] = int(flag(data, "archived", False))
    if fields:
        con.execute(f"UPDATE occasions SET {', '.join(f'{k} = ?' for k in fields)} WHERE id = ?",
                    (*fields.values(), occasion_id))


def set_plan(con, occasion_id: int, person_id: int, plan: str, budget_cents: int | None) -> None:
    if plan not in PLANS:
        raise ApiError(400, f"plan must be one of {', '.join(PLANS)}.")
    if budget_cents is not None and not 0 <= budget_cents <= MAX_BUDGET_CENTS:
        raise ApiError(400, "budget_cents is out of range.")
    get(con, occasion_id)
    if not con.execute("SELECT 1 FROM people WHERE id = ?", (person_id,)).fetchone():
        raise ApiError(404, "No such person.")
    con.execute("INSERT INTO occasion_people (occasion_id, person_id, plan, budget_cents) VALUES (?, ?, ?, ?) "
                "ON CONFLICT(occasion_id, person_id) DO UPDATE SET plan = excluded.plan, budget_cents = excluded.budget_cents",
                (occasion_id, person_id, plan, budget_cents))


def set_plan_from(con, occasion_id: int, person_id: int, data: dict) -> None:
    plan = data.get("plan")
    if not isinstance(plan, str):
        raise ApiError(400, f"plan must be one of {', '.join(PLANS)}.")
    set_plan(con, occasion_id, person_id, plan, _budget(data))


def remove_plan(con, occasion_id: int, person_id: int) -> None:
    if not con.execute("DELETE FROM occasion_people WHERE occasion_id = ? AND person_id = ?",
                       (occasion_id, person_id)).rowcount:
        raise ApiError(404, "That person is not in this occasion.")


def current(con):
    """The unarchived occasion with the nearest date on or after today, else the latest unarchived one."""
    row = con.execute("SELECT * FROM occasions WHERE archived = 0 AND date >= ? ORDER BY date, id LIMIT 1",
                      (common.today(),)).fetchone()
    return row or con.execute("SELECT * FROM occasions WHERE archived = 0 ORDER BY date DESC, id DESC LIMIT 1").fetchone()


def list_all(con) -> list[dict]:
    """Every occasion, newest date first, each with its people's plans; `current` marks the one `current()` picks."""
    cur = current(con)
    plans: dict[int, list] = {}
    for r in con.execute("SELECT * FROM occasion_people ORDER BY person_id"):
        plans.setdefault(r["occasion_id"], []).append(
            {"person_id": r["person_id"], "plan": r["plan"], "budget_cents": r["budget_cents"]})
    return [{"id": r["id"], "name": r["name"], "kind": r["kind"], "date": r["date"], "archived": bool(r["archived"]),
             "current": cur is not None and cur["id"] == r["id"], "people": plans.get(r["id"], [])}
            for r in con.execute("SELECT * FROM occasions ORDER BY date DESC, id DESC")]
