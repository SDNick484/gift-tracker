"""Money per person for one occasion, and the occasion summary behind the home screen. Route-free: every function
takes a sqlite3.Connection.

Only gifts on the ours track count toward money: `spent` is ordered, arrived, wrapped or given; `planned` is idea
or decided. A gift with several recipients counts toward each by an even share of its price (shares()). A gift
with no price counts in gift_count only, as does every gift on the theirs track.

Privacy: with include_protected=False (summary(unlocked=False)) a gift with any protected recipient does not
exist here, so it is in no unprotected co-recipient's totals, counts or headline either, and protected people are
left out of the summary."""
from datetime import date

import common
import gifts
import occasions
import people

SPENT = ("ordered", "arrived", "wrapped", "given")
PLANNED = ("idea", "decided")


def shares(price_cents: int, recipient_ids: list[int]) -> dict[int, int]:
    """price_cents split evenly between the recipients; the remainder cents go to the lowest ids, one each."""
    ids = sorted(set(recipient_ids))
    if not ids:
        return {}
    base, extra = divmod(price_cents, len(ids))
    return {pid: base + (1 if i < extra else 0) for i, pid in enumerate(ids)}


def _gifts(con, occasion_id: int, include_protected: bool) -> list[dict]:
    """The occasion's gifts with their recipients and givers, leaving out protected gifts unless asked."""
    rows = con.execute(
        f"SELECT g.id, g.title, g.status, g.price_cents, g.updated_at FROM gifts g "
        f"WHERE g.occasion_id = ? AND (? OR NOT {gifts.PROTECTED_SQL}) ORDER BY g.id",
        (occasion_id, int(include_protected))).fetchall()
    links: dict[tuple[str, int], list[int]] = {}
    for table in ("gift_recipients", "gift_givers"):
        for r in con.execute(f"SELECT l.gift_id, l.person_id FROM {table} l JOIN gifts g ON g.id = l.gift_id "
                             "WHERE g.occasion_id = ? ORDER BY l.person_id", (occasion_id,)):
            links.setdefault((table, r["gift_id"]), []).append(r["person_id"])
    return [{**dict(r), "recipient_ids": links.get(("gift_recipients", r["id"]), []),
             "giver_ids": links.get(("gift_givers", r["id"]), [])} for r in rows]


def _totals(gift_list: list[dict], user_ids: set[int]) -> dict[int, dict]:
    out: dict[int, dict] = {}
    for gft in gift_list:
        ours = gifts.track(set(gft["giver_ids"]), user_ids) == "ours"
        split = shares(gft["price_cents"], gft["recipient_ids"]) if ours and gft["price_cents"] is not None else {}
        for pid in gft["recipient_ids"]:
            t = out.setdefault(pid, {"spent_cents": 0, "planned_cents": 0, "gift_count": 0})
            t["gift_count"] += 1
            if pid in split:
                if gft["status"] in SPENT:
                    t["spent_cents"] += split[pid]
                elif gft["status"] in PLANNED:
                    t["planned_cents"] += split[pid]
    return out


def person_totals(con, occasion_id: int, *, include_protected: bool) -> dict[int, dict]:
    """{person_id: {"spent_cents", "planned_cents", "gift_count"}} for every recipient of a counted gift."""
    return _totals(_gifts(con, occasion_id, include_protected), people.user_person_ids(con))


def summary(con, occasion_id: int, *, unlocked: bool) -> dict:
    """The home screen for one occasion. People shown: every active person who is not an owner, plus anyone with
    a plan in this occasion or a gift shown here. Protected people are only ever in the `protected` section, and
    that section is empty while locked."""
    occ = occasions.get(con, occasion_id)
    gift_list = _gifts(con, occasion_id, unlocked)
    user_ids = people.user_person_ids(con)
    totals = _totals(gift_list, user_ids)
    headline: dict[int, tuple] = {}
    for gft in gift_list:
        for pid in gft["recipient_ids"]:
            key = (gft["updated_at"], gft["id"])
            if pid not in headline or key > headline[pid][0]:
                headline[pid] = (key, gft["title"])
    plans = {r["person_id"]: r for r in con.execute(
        "SELECT person_id, plan, budget_cents FROM occasion_people WHERE occasion_id = ?", (occasion_id,))}

    def entry(p):
        plan, t = plans.get(p["id"]), totals.get(p["id"], {"spent_cents": 0, "planned_cents": 0, "gift_count": 0})
        return {"id": p["id"], "name": p["name"], "color": p["color"],
                "plan": plan["plan"] if plan else None, "budget_cents": plan["budget_cents"] if plan else None,
                **t, "headline": headline[p["id"]][1] if p["id"] in headline else None}

    shown = [p for p in people.list_people(con, include_inactive=True)
             if (p["active"] and not p["is_user"]) or p["id"] in plans or p["id"] in totals]
    groups = [{"id": g["id"], "name": g["name"], "people": []} for g in people.list_groups(con)]
    ungrouped = {"id": None, "name": "Ungrouped", "people": []}
    by_id = {g["id"]: g for g in groups}
    protected = []
    for p in shown:
        if p["protected"]:
            if unlocked:
                protected.append(entry(p))
            continue
        by_id.get(p["group_id"], ungrouped)["people"].append(entry(p))
    return {"occasion": {"id": occ["id"], "name": occ["name"], "kind": occ["kind"], "date": occ["date"],
                         "archived": bool(occ["archived"])},
            "days_until": (date.fromisoformat(occ["date"]) - date.fromisoformat(common.today())).days,
            "groups": [g for g in [*groups, ungrouped] if g["people"]],
            "protected": {"locked": not unlocked, "people": protected}}
