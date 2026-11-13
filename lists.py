"""The three working lists after purchase, and the counts on the home screen. Route-free: every function takes a
sqlite3.Connection and a required `unlocked`, and raises common.ApiError (404 for a missing occasion).

  pending   ordered gifts by expected date (no date last), each with `overdue`
  to_wrap   arrived gifts, grouped by recipient, with the hiding place
  where     arrived or wrapped gifts, grouped by hiding place in tree order
  counts    {"pending": n, "to_wrap": n}, in gifts

Privacy: every query filters on gifts.PROTECTED_SQL unless `unlocked`, so a gift with any protected recipient is in no
list, group or count while locked, including those of an unprotected co-recipient. An item names no Santa flag, ever.
In school mode an item has no location keys (school mode has no hiding places)."""
import common
import gifts
import locations
import occasions
import people


def _rows(con, occasion_id: int, statuses: tuple[str, ...], unlocked: bool, order: str):
    occasions.get(con, occasion_id)
    marks = ",".join("?" * len(statuses))
    return con.execute(
        "SELECT g.*, a.is_home AS ship_home FROM gifts g LEFT JOIN addresses a ON a.id = g.ship_to_id "
        f"WHERE g.occasion_id = ? AND g.status IN ({marks}) AND (? OR NOT {gifts.PROTECTED_SQL}) ORDER BY {order}",
        (occasion_id, *statuses, int(unlocked))).fetchall()


def _items(con, rows) -> list[dict]:
    if not rows:
        return []
    recipients: dict[int, list[int]] = {}
    for r in con.execute("SELECT gift_id, person_id FROM gift_recipients WHERE gift_id IN (%s) ORDER BY person_id"
                         % ",".join(str(r["id"]) for r in rows)):  # ids are integers from our own rows
        recipients.setdefault(r["gift_id"], []).append(r["person_id"])
    paths, today, school = locations.path_map(con), common.today(), common.is_school()
    out = []
    for r in rows:
        warn = r["ship_to_id"] is not None and not r["ship_home"] and not r["ship_elsewhere_ok"]
        item = {"id": r["id"], "title": r["title"], "status": r["status"], "recipient_ids": recipients.get(r["id"], []),
                "seller": r["seller"], "order_number": r["order_number"], "eta": r["eta"], "arrived_on": r["arrived_on"],
                "ship_to_id": r["ship_to_id"], "ship_warning": bool(warn),
                "overdue": r["status"] == "ordered" and r["eta"] is not None and r["eta"] < today}
        if not school:
            item["location_id"] = r["location_id"]
            item["location_path"] = paths.get(r["location_id"])
        out.append(item)
    return out


def pending(con, occasion_id: int, *, unlocked: bool) -> dict:
    """Ordered gifts, soonest expected date first; a gift with no date comes last. `overdue` is true when the date
    is before today (a gift due today is not overdue yet)."""
    rows = _rows(con, occasion_id, ("ordered",), unlocked, "(g.eta IS NULL), g.eta, g.id")
    return {"gifts": _items(con, rows)}


def to_wrap(con, occasion_id: int, *, unlocked: bool) -> dict:
    """Arrived gifts under each of their recipients, in the order people are listed; a gift for two people is in
    both groups. A person with nothing to wrap has no group."""
    items = _items(con, _rows(con, occasion_id, ("arrived",), unlocked, "g.id"))
    by_person: dict[int, list[dict]] = {}
    for item in items:
        for pid in item["recipient_ids"]:
            by_person.setdefault(pid, []).append(item)
    order = [p["id"] for p in people.list_people(con, include_inactive=True)]
    return {"groups": [{"person_id": pid, "gifts": by_person[pid]} for pid in order if pid in by_person]}


def where(con, occasion_id: int, *, unlocked: bool) -> dict:
    """Arrived or wrapped gifts grouped by hiding place, places in tree order; gifts with no place in a last group
    whose location_id and path are null. Family mode only (the route is family_only)."""
    items = _items(con, _rows(con, occasion_id, ("arrived", "wrapped"), unlocked, "g.id"))
    by_place: dict = {}
    for item in items:
        by_place.setdefault(item["location_id"], []).append(item)
    groups = [{"location_id": loc["id"], "path": loc["path"], "gifts": by_place[loc["id"]]}
              for loc in locations.tree(con) if loc["id"] in by_place]
    if None in by_place:
        groups.append({"location_id": None, "path": None, "gifts": by_place[None]})
    return {"groups": groups}


def counts(con, occasion_id: int, *, unlocked: bool) -> dict:
    return {"pending": len(_rows(con, occasion_id, ("ordered",), unlocked, "g.id")),
            "to_wrap": len(_rows(con, occasion_id, ("arrived",), unlocked, "g.id"))}
