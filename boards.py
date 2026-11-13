"""A protected person's board for one occasion: unassigned wishes, gifts grouped by who gives them, the
Santa/us/others split and the side-by-side total for every protected person; assignment with a conflict check;
and a giver's list as copyable text. Route-free: every function takes a sqlite3.Connection.

Everything here shows protected gifts, so app.py serves it only behind @unlock_required.

Assignment: two owners may assign the same wish from two phones at once. assign() reads the current givers and
writes the new ones inside one BEGIN IMMEDIATE transaction, and refuses with 409 unless the givers are still the
ones the caller saw (expected_giver_ids), so the second of two simultaneous assignments fails instead of winning."""
import budgets
import db
import gifts
import occasions
import people
from common import ApiError

CONFLICT = "Someone just assigned this. Reload."


def list_text(label: str, gifts: list[dict]) -> str:
    """The label, a blank line, then one line per gift: the title, and " - <url>" when there is one."""
    lines = [g["title"] + (f" - {g['url']}" if g.get("url") else "") for g in gifts]
    return "\n".join([label, "", *lines])


def _id_list(data: dict, key: str) -> list[int]:
    raw = data.get(key)
    if not isinstance(raw, list) or any(isinstance(i, bool) or not isinstance(i, int) for i in raw):
        raise ApiError(400, f"{key} must be a list of person ids.")
    return raw


def parse_assign(data: dict) -> tuple[list[int], list[int]]:
    """giver_ids and expected_giver_ids from a request body; both are required lists of whole numbers."""
    return _id_list(data, "giver_ids"), _id_list(data, "expected_giver_ids")


def assign(con, gift_id: int, giver_ids: list[int], expected_giver_ids: list[int], after_write=None) -> None:
    """Replace the gift's givers, but only if they are still `expected_giver_ids` (compared as sets); otherwise
    409. Giving it to someone else moves it to the theirs track (told, told_at today) as gifts._set_givers does.
    An empty giver_ids unassigns it: status idea, told_at cleared, Santa dropped.

    `after_write()`, when given, runs inside the transaction after the local write (wishlist.assign writes the
    reservation there); if it raises, nothing is written."""
    ids = gifts._person_ids(con, {"giver_ids": list(giver_ids)}, "giver_ids")  # 400 for a missing person
    with db.transaction(con):
        gifts.get(con, gift_id)  # 404
        current = {r[0] for r in con.execute("SELECT person_id FROM gift_givers WHERE gift_id = ?", (gift_id,))}
        if current != set(expected_giver_ids):
            raise ApiError(409, CONFLICT)
        gifts._set_givers(con, gift_id, ids)  # inside this transaction: gifts.set_givers would open its own
        if not ids:
            con.execute("UPDATE gifts SET status = 'idea', told_at = NULL WHERE id = ?", (gift_id,))
        if after_write:
            after_write()


def _protected_person(con, person_id: int) -> dict:
    p = people.get_person(con, person_id)  # 404
    if not p["protected"]:
        raise ApiError(404, "No board for this person.")
    return p


def _givers(con, gift_ids: list[int]) -> dict[int, list[int]]:
    out: dict[int, list[int]] = {gid: [] for gid in gift_ids}
    if gift_ids:
        marks = ", ".join("?" * len(gift_ids))
        for r in con.execute(f"SELECT gift_id, person_id FROM gift_givers WHERE gift_id IN ({marks}) "
                             "ORDER BY person_id", gift_ids):
            out[r["gift_id"]].append(r["person_id"])
    return out


def _label(giver_ids: list[int], households: dict[frozenset, str], names: dict[int, str]) -> str:
    """A household's label when the givers are exactly that household; otherwise their names joined by " & "."""
    return households.get(frozenset(giver_ids)) or " & ".join(names[i] for i in giver_ids)


def _evenness(con, occasion_id: int, user_ids: set[int]) -> list[dict]:
    """For every protected person in the occasion (active, or with a plan or a gift in it): the sum of their even
    shares of ours-track priced gifts, any status."""
    totals: dict[int, int] = {}
    for gft in budgets._gifts(con, occasion_id, include_protected=True):
        if gft["price_cents"] is None or gifts.track(set(gft["giver_ids"]), user_ids) != "ours":
            continue
        for pid, cents in budgets.shares(gft["price_cents"], gft["recipient_ids"]).items():
            totals[pid] = totals.get(pid, 0) + cents
    planned = {r[0] for r in con.execute("SELECT person_id FROM occasion_people WHERE occasion_id = ?", (occasion_id,))}
    return [{"person_id": p["id"], "name": p["name"], "cents": totals.get(p["id"], 0)}
            for p in people.list_people(con, include_inactive=True)
            if p["protected"] and (p["active"] or p["id"] in planned or p["id"] in totals)]


def board(con, occasion_id: int, person_id: int) -> dict:
    """The board for a protected person (404 for anyone else). Giver groups: one per distinct giver set, other
    givers first ordered by label, then groups with an owner among the givers ("Us"), also by label. A group
    appears only if it has gifts."""
    occasions.get(con, occasion_id)  # 404
    person = _protected_person(con, person_id)
    rows = con.execute("SELECT g.* FROM gifts g JOIN gift_recipients r ON r.gift_id = g.id "
                       "WHERE g.occasion_id = ? AND r.person_id = ? ORDER BY g.id", (occasion_id, person_id)).fetchall()
    givers_of = _givers(con, [r["id"] for r in rows])
    user_ids = people.user_person_ids(con)
    households = {frozenset(h["person_ids"]): h["label"] for h in people.households(con)}
    names = {r["id"]: r["name"] for r in con.execute("SELECT id, name FROM people")}

    unassigned, groups = [], {}
    split = {"santa": 0, "us": 0, "others": 0}
    for row in rows:
        gj = gifts.to_json(con, row, unlocked=True)
        gids = givers_of[row["id"]]
        if not gids:
            unassigned.append(gj)
            continue
        is_us = gifts.track(set(gids), user_ids) == "ours"
        split["santa" if is_us and row["santa"] else "us" if is_us else "others"] += 1
        key = tuple(gids)
        if key not in groups:
            groups[key] = {"label": _label(gids, households, names), "giver_ids": list(gids), "is_us": is_us,
                           "gifts": []}
        groups[key]["gifts"].append(gj)
    ordered = sorted(groups.values(), key=lambda x: (x["is_us"], x["label"].casefold(), x["giver_ids"]))
    for grp in ordered:
        grp["list_text"] = list_text(grp["label"], grp["gifts"])
    return {"person": {"id": person["id"], "name": person["name"], "color": person["color"]},
            "unassigned": unassigned, "givers": ordered, "split": split,
            "evenness": _evenness(con, occasion_id, user_ids)}
