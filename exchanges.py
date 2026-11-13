"""Gift exchanges (Secret Santa, Favorite Things, a staff exchange): an occasion's exchanges, which member of this
household drew whom, and the gift for each draw. Route-free: every function takes a sqlite3.Connection.

Only this household's side is kept. A drawer is an owner or a protected person; the recipient is anyone else. A
gift for a draw is an ordinary gift whose `exchange_id` is set, whose only giver is the drawer and whose only
recipient is the person drawn (check_gift).

Privacy: a draw is protected when its drawer or its recipient is protected, judged from people.protected at read
time. While locked a protected draw does not exist: list_for_occasion leaves it out (and so does the home summary,
which uses it), set_draw and remove_draw answer 423 when the drawer or the new recipient is protected (people are
listed while locked, so that says nothing new) and 404 for a hidden draw of an unprotected drawer. The gifts linked
to a protected draw are hidden by gifts.PROTECTED_SQL. School mode has no lock and no protected person."""
import common
import db
import gifts
import occasions
import people
from common import ApiError, text, whole

MAX_NAME = 100
DRAW_PROTECTED = "(dp.protected = 1 OR rp.protected = 1)"  # over d (draw), dp (drawer) and rp (recipient)


def _name(data: dict) -> str:
    name = text(data, "name")
    if not name or len(name) > MAX_NAME:
        raise ApiError(400, f"An exchange needs a name of 1 to {MAX_NAME} characters.")
    return name


def _budget(data: dict) -> int | None:
    cents = whole(data, "budget_cents")
    if cents is not None and not 0 <= cents <= occasions.MAX_BUDGET_CENTS:
        raise ApiError(400, "budget_cents is out of range.")
    return cents


def _date(data: dict) -> str | None:
    value = data.get("date")
    if value is None or value == "":
        return None
    return occasions._date(value)


def over_budget(price_cents: int | None, budget_cents: int | None) -> bool:
    """A gift above the exchange's budget. Flagged, never refused; no price or no budget is never over."""
    return price_cents is not None and budget_cents is not None and price_cents > budget_cents


def get(con, exchange_id: int):
    row = con.execute("SELECT * FROM exchanges WHERE id = ?", (exchange_id,)).fetchone()
    if row is None:
        raise ApiError(404, "No such exchange.")
    return row


def _hides(con, person_id: int) -> bool:
    """Whether the lock hides this person's draws. School mode has no protected person."""
    return not common.is_school() and person_id in people.protected_ids(con)


# ---------------------------------------------------------------- reading
def list_for_occasion(con, occasion_id: int, *, unlocked: bool) -> list[dict]:
    """The occasion's exchanges by id, each with its draws (drawer, then recipient order) and each draw's gift.
    While locked, protected draws and their gifts are left out; the exchange itself (name, budget, date) is not
    protected data and always shows."""
    occasions.get(con, occasion_id)  # 404
    rows = con.execute("SELECT * FROM exchanges WHERE occasion_id = ? ORDER BY id", (occasion_id,)).fetchall()
    draws = con.execute(
        "SELECT d.exchange_id, d.drawer_id, d.recipient_id FROM exchange_draws d "
        "JOIN exchanges e ON e.id = d.exchange_id JOIN people dp ON dp.id = d.drawer_id "
        f"JOIN people rp ON rp.id = d.recipient_id WHERE e.occasion_id = ? AND (? OR NOT {DRAW_PROTECTED}) "
        "ORDER BY d.exchange_id, d.drawer_id, d.recipient_id", (occasion_id, int(unlocked))).fetchall()
    by_draw = {}
    for r in con.execute(
            "SELECT g.id, g.title, g.status, g.price_cents, g.exchange_id, gg.person_id AS giver, gr.person_id AS recipient "
            "FROM gifts g JOIN gift_givers gg ON gg.gift_id = g.id JOIN gift_recipients gr ON gr.gift_id = g.id "
            f"WHERE g.exchange_id IN (SELECT id FROM exchanges WHERE occasion_id = ?) AND (? OR NOT {gifts.PROTECTED_SQL}) "
            "ORDER BY g.id", (occasion_id, int(unlocked))):
        by_draw.setdefault((r["exchange_id"], r["giver"], r["recipient"]), r)
    out = []
    for e in rows:
        items = []
        for d in (d for d in draws if d["exchange_id"] == e["id"]):
            g = by_draw.get((e["id"], d["drawer_id"], d["recipient_id"]))
            items.append({"drawer_id": d["drawer_id"], "recipient_id": d["recipient_id"],
                          "gift_id": g["id"] if g else None, "gift_title": g["title"] if g else None,
                          "gift_status": g["status"] if g else None, "price_cents": g["price_cents"] if g else None,
                          "over_budget": over_budget(g["price_cents"], e["budget_cents"]) if g else False})
        out.append({"id": e["id"], "name": e["name"], "budget_cents": e["budget_cents"], "date": e["date"], "draws": items})
    return out


def one(con, exchange_id: int, *, unlocked: bool) -> dict:
    row = get(con, exchange_id)
    return next(x for x in list_for_occasion(con, row["occasion_id"], unlocked=unlocked) if x["id"] == exchange_id)


# ---------------------------------------------------------------- exchanges
def create(con, occasion_id: int, data: dict, *, unlocked: bool) -> dict:
    occasions.get(con, occasion_id)
    name, budget, date = _name(data), _budget(data), _date(data)
    eid = con.execute("INSERT INTO exchanges (occasion_id, name, budget_cents, date) VALUES (?, ?, ?, ?)",
                      (occasion_id, name, budget, date)).lastrowid
    return one(con, eid, unlocked=unlocked)


def update(con, exchange_id: int, data: dict, *, unlocked: bool) -> dict:
    get(con, exchange_id)
    fields = {}
    if "name" in data:
        fields["name"] = _name(data)
    if "budget_cents" in data:
        fields["budget_cents"] = _budget(data)
    if "date" in data:
        fields["date"] = _date(data)
    if fields:
        con.execute(f"UPDATE exchanges SET {', '.join(f'{k} = ?' for k in fields)} WHERE id = ?",
                    (*fields.values(), exchange_id))
    return one(con, exchange_id, unlocked=unlocked)


def delete(con, exchange_id: int) -> None:
    """Its draws go with it. 409 while any gift is linked: deleting would unlink the gift, and a gift from a
    protected drawer would stop being hidden. The route needs the unlock, so the 409 tells a locked caller nothing."""
    with db.transaction(con):
        get(con, exchange_id)
        if con.execute("SELECT 1 FROM gifts WHERE exchange_id = ?", (exchange_id,)).fetchone():
            raise ApiError(409, "This exchange has gifts. Delete or unlink them first.")
        con.execute("DELETE FROM exchanges WHERE id = ?", (exchange_id,))


# ---------------------------------------------------------------- draws
def _draw(con, exchange_id: int, drawer_id: int):
    return con.execute("SELECT * FROM exchange_draws WHERE exchange_id = ? AND drawer_id = ?",
                       (exchange_id, drawer_id)).fetchone()


def _has_gift(con, exchange_id: int, drawer_id: int, recipient_id: int) -> bool:
    return con.execute("SELECT 1 FROM gifts g JOIN gift_givers gg ON gg.gift_id = g.id AND gg.person_id = ? "
                       "JOIN gift_recipients gr ON gr.gift_id = g.id AND gr.person_id = ? WHERE g.exchange_id = ?",
                       (drawer_id, recipient_id, exchange_id)).fetchone() is not None


def set_draw(con, exchange_id: int, drawer_id: int, data: dict, *, unlocked: bool) -> dict:
    """The drawer drew `recipient_id`; replaces the drawer's earlier draw in this exchange. 400 unless the drawer
    is an owner or a protected person (school mode: an owner) and the recipient is someone else. 409 when the
    earlier draw already has a gift. While locked: 423 when the drawer or the recipient is protected, and when the
    drawer's earlier draw is hidden (it has a protected recipient)."""
    get(con, exchange_id)
    drawer = people.get_person(con, drawer_id)  # 404
    if drawer_id not in people.user_person_ids(con) and not (not common.is_school() and drawer["protected"]):
        raise ApiError(400, "Only a member of this household can draw.")
    recipient_id = whole(data, "recipient_id")
    if recipient_id is None:
        raise ApiError(400, "recipient_id must be a person id.")
    if recipient_id == drawer_id:
        raise ApiError(400, "Nobody draws themselves.")
    if not con.execute("SELECT 1 FROM people WHERE id = ?", (recipient_id,)).fetchone():
        raise ApiError(400, "No such person.")
    with db.transaction(con):
        before = _draw(con, exchange_id, drawer_id)
        if not unlocked and (_hides(con, drawer_id) or _hides(con, recipient_id)
                             or (before and _hides(con, before["recipient_id"]))):
            raise common.locked()
        if before and before["recipient_id"] != recipient_id and _has_gift(con, exchange_id, drawer_id, before["recipient_id"]):
            raise ApiError(409, "This draw has a gift. Delete or unlink it first.")
        con.execute("INSERT INTO exchange_draws (exchange_id, drawer_id, recipient_id) VALUES (?, ?, ?) "
                    "ON CONFLICT(exchange_id, drawer_id) DO UPDATE SET recipient_id = excluded.recipient_id",
                    (exchange_id, drawer_id, recipient_id))
    return one(con, exchange_id, unlocked=unlocked)


def remove_draw(con, exchange_id: int, drawer_id: int, *, unlocked: bool) -> dict:
    """Takes the drawer's draw out. While locked a protected draw is 423 (protected drawer) or the same 404 as a
    draw that does not exist (protected recipient). 409 when the draw has a gift."""
    get(con, exchange_id)
    people.get_person(con, drawer_id)  # 404
    with db.transaction(con):
        if not unlocked and _hides(con, drawer_id):
            raise common.locked()
        row = _draw(con, exchange_id, drawer_id)
        if row is None or (not unlocked and _hides(con, row["recipient_id"])):
            raise ApiError(404, "No such draw.")
        if _has_gift(con, exchange_id, drawer_id, row["recipient_id"]):
            raise ApiError(409, "This draw has a gift. Delete or unlink it first.")
        con.execute("DELETE FROM exchange_draws WHERE exchange_id = ? AND drawer_id = ?", (exchange_id, drawer_id))
    return one(con, exchange_id, unlocked=unlocked)
