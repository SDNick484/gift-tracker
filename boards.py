"""A protected person's board for one occasion: unassigned wishes, gifts grouped by who gives them, the
Santa/us/others split and the side-by-side total for every protected person; the person's ideas still open for them;
assignment with a conflict check; and a giver's list as copyable text. Route-free: every function takes a
sqlite3.Connection.

Everything here shows protected gifts, so app.py serves it only behind @unlock_required.

Assignment: two owners may assign the same wish from two phones at once. assign() reads the current givers and
writes the new ones inside one BEGIN IMMEDIATE transaction, and refuses with 409 unless the givers are still the
ones the caller saw (expected_giver_ids and expected_giver_name), so the second of two simultaneous assignments fails
instead of winning."""
import budgets
import db
import gifts
import ideas
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


def parse_assign(data: dict) -> tuple[list[int], list[int], str | None, str | None]:
    """giver_ids, expected_giver_ids, giver_name and expected_giver_name from a request body. The id lists are
    required lists of whole numbers; the names are optional typed giver names (gifts.parse_giver_name), None when
    absent."""
    names = [gifts.giver_name_of(data), gifts.parse_giver_name(data.get("expected_giver_name"), "expected_giver_name")]
    return (_id_list(data, "giver_ids"), _id_list(data, "expected_giver_ids"),
            None if names[0] is gifts.ABSENT else names[0], names[1])


def assign(con, gift_id: int, giver_ids: list[int], expected_giver_ids: list[int], after_write=None, *,
           giver_name: str | None = None, expected_giver_name: str | None = None) -> None:
    """Replace the gift's givers and typed name, but only if they are still `expected_giver_ids` (compared as sets)
    and `expected_giver_name`; otherwise 409. Giving it to someone else moves it to the theirs track (told, told_at
    today) as gifts._set_givers does. No giver ids and no name unassigns it: status idea, told_at cleared, Santa
    dropped; on a gift taken from an idea that is 400 (send it back instead). A gift for an exchange takes no typed
    name (400).

    `after_write()`, when given, runs inside the transaction after the local write (wishlist.assign writes the
    reservation there); if it raises, nothing is written."""
    ids = gifts._person_ids(con, {"giver_ids": list(giver_ids)}, "giver_ids")  # 400 for a missing person
    with db.transaction(con):
        row = gifts.get(con, gift_id)  # 404
        current = {r[0] for r in con.execute("SELECT person_id FROM gift_givers WHERE gift_id = ?", (gift_id,))}
        if current != set(expected_giver_ids) or row["giver_name"] != expected_giver_name:
            raise ApiError(409, CONFLICT)
        if giver_name and row["exchange_id"] is not None:
            raise ApiError(400, gifts.EXCHANGE_NAME)
        gifts.refuse_no_giver(con, gift_id, ids, giver_name)  # a gift taken from an idea goes back instead
        # Inside this transaction: gifts.set_givers would open its own. An unchanged name is simply kept.
        gifts._set_givers(con, gift_id, ids, **({} if giver_name == row["giver_name"] else {"giver_name": giver_name}))
        if not ids and not giver_name:
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


def _label(giver_ids: list[int], households: dict[frozenset, str], names: dict[int, str], giver_name=None) -> str:
    """A household's label when the givers are exactly that household, otherwise their names joined by " & "; then
    the typed name, if any, as one more part."""
    people_part = (households.get(frozenset(giver_ids)) or " & ".join(names[i] for i in giver_ids)) if giver_ids else ""
    return " & ".join(p for p in (people_part, giver_name) if p)


def _evenness(con, occasion_id: int, user_ids: set[int]) -> list[dict]:
    """For every protected person in the occasion (active, or with a plan or a gift in it): the sum of their even
    shares of ours-track priced gifts, any status."""
    totals: dict[int, int] = {}
    for gft in budgets._gifts(con, occasion_id, include_protected=True):
        if gft["price_cents"] is None or gifts.track(set(gft["giver_ids"]), user_ids, gft["giver_name"]) != "ours":
            continue
        for pid, cents in budgets.shares(gft["price_cents"], gft["recipient_ids"]).items():
            totals[pid] = totals.get(pid, 0) + cents
    planned = {r[0] for r in con.execute("SELECT person_id FROM occasion_people WHERE occasion_id = ?", (occasion_id,))}
    return [{"person_id": p["id"], "name": p["name"], "cents": totals.get(p["id"], 0)}
            for p in people.list_people(con, include_inactive=True)
            if p["protected"] and (p["active"] or p["id"] in planned or p["id"] in totals)]


def board(con, occasion_id: int, person_id: int) -> dict:
    """The board for a protected person (404 for anyone else). Giver groups: one per distinct giver set (the
    people and the typed name), other givers first ordered by label, then groups with an owner among the givers
    ("Us"), also by label. A group appears only if it has gifts. A gift with no giver and no name is unassigned.
    `ideas` are the person's ideas whose copy for them is open or held (idea JSON plus that `copy_state`); a taken
    copy is its gift, already on the board."""
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
        gj = {**gifts.to_json(con, row, unlocked=True), "from_idea": gifts.from_idea(con, row["id"])}
        gids, name = givers_of[row["id"]], row["giver_name"]
        if not gids and not name:
            unassigned.append(gj)
            continue
        is_us = gifts.track(set(gids), user_ids, name) == "ours"
        split["santa" if is_us and row["santa"] else "us" if is_us else "others"] += 1
        key = (tuple(gids), name)
        if key not in groups:
            groups[key] = {"label": _label(gids, households, names, name), "giver_ids": list(gids), "is_us": is_us,
                           "gifts": []}
        groups[key]["gifts"].append(gj)
    ordered = [groups[k] for k in sorted(groups, key=lambda k: (groups[k]["is_us"], groups[k]["label"].casefold(),
                                                                list(k[0]), k[1] or ""))]
    for grp in ordered:
        grp["list_text"] = list_text(grp["label"], grp["gifts"])
    return {"person": {"id": person["id"], "name": person["name"], "color": person["color"]},
            "unassigned": unassigned, "givers": ordered, "split": split,
            "evenness": _evenness(con, occasion_id, user_ids), "ideas": _open_ideas(con, person_id)}


def _open_ideas(con, person_id: int) -> list[dict]:
    out = []
    for idea in ideas.list_json(con, unlocked=True, person_id=person_id):  # the board is behind the unlock
        state = next(p["state"] for p in idea["people"] if p["person_id"] == person_id)
        if state != "taken":
            out.append({**idea, "copy_state": state})
    return out
