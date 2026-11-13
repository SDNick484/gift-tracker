"""The agent API (/api/v1): what each token scope may read and write, and what a gift looks like to it. Route-free:
every function takes a sqlite3.Connection and the request's token ({id, name, scope, unlocked}, from
tokens.authenticate) and raises common.ApiError.

Privacy: a token is locked unless sees_protected() (a `full` token made with `unlocked`, in family mode). A locked
token never gets a gift with a protected recipient, a protected person, a `santa` key or a hiding place: lists filter
on gifts.PROTECTED_SQL, one-gift routes answer the same 404 as a missing gift (visible_row), and a protected person
named in a body is "No such person.", as a missing one is. No scope but `full` gets `santa`, a hiding place, givers
or created_by at all.

Writes go through the same functions as the web app (orders.update, gifts.set_status, gifts.create), so every rule
holds here too (marking ordered needs seller, ship-to and eta), and each leaves a note with the token's name
(tokens.log_write); marking ordered also sets ordered_by to it."""
import re
from dataclasses import dataclass

import addresses
import budgets
import db
import gifts
import occasions
import orders
import people
import tokens
import wishlist
from common import ApiError, is_school


@dataclass(frozen=True)
class Scope:
    read: tuple[str, ...] | None          # statuses whose gifts it may see and write; None: every status
    list_default: tuple[str, ...] | None  # what GET /api/v1/gifts lists without ?status=
    set_status: tuple[str, ...] | None    # statuses it may set; None: any on the gift's track
    can: frozenset                        # more operations: people, budgets, create


SCOPES = {
    "delivery": Scope(("decided", "ordered", "arrived"), ("decided", "ordered"), ("arrived",), frozenset()),
    "shop": Scope(("idea", "decided", "ordered", "arrived"), ("idea", "decided", "ordered"), ("ordered", "arrived"),
                  frozenset({"people", "budgets"})),
    "full": Scope(None, None, None, frozenset({"people", "budgets", "create"})),
}
assert tuple(SCOPES) == tokens.SCOPES
ALL_STATUSES = tuple(dict.fromkeys(gifts.OURS + gifts.THEIRS))
_WHOLE = re.compile(r"[0-9]{1,12}")
# Names no key the caller sent (a response never echoes a body's keys back).
ONLY_ORDER_FIELDS = f"Only order fields can be written here: {', '.join(orders.FIELDS)}."


def sees_protected(token: dict) -> bool:
    """Whether this token may see protected gifts and Santa: only a full token made unlocked, and never in school
    mode (which has no lock and nothing protected)."""
    return token["scope"] == "full" and token["unlocked"] and not is_school()


def scope_of(token: dict) -> Scope:
    return SCOPES[token["scope"]]


def refuse(token: dict) -> ApiError:
    return ApiError(403, "This token's scope does not allow that.", scope=token["scope"])


def require(token: dict, what: str) -> None:
    if what not in scope_of(token).can:
        raise refuse(token)


def me(token: dict) -> dict:
    return {"name": token["name"], "scope": token["scope"], "unlocked": sees_protected(token),
            "mode": "school" if is_school() else "family"}


# ---------------------------------------------------------------- reading
def visible_row(con, gift_id: int, token: dict):
    """The gift row, or the same 404 as a missing gift when the token may not see it (protected while locked, or a
    status outside its scope)."""
    row = con.execute("SELECT * FROM gifts WHERE id = ?", (gift_id,)).fetchone()
    read = scope_of(token).read
    if row is None or not gifts.visible(con, gift_id, unlocked=sees_protected(token)) or (read and row["status"] not in read):
        raise ApiError(404, "No such gift.")
    return row


def gift_json(con, row, token: dict) -> dict:
    recipients = [{"id": r["id"], "name": r["name"]} for r in con.execute(
        "SELECT p.id, p.name FROM gift_recipients r JOIN people p ON p.id = r.person_id WHERE r.gift_id = ? ORDER BY p.id",
        (row["id"],))]
    ship = con.execute("SELECT label FROM addresses WHERE id = ?", (row["ship_to_id"],)).fetchone() if row["ship_to_id"] else None
    order = {**orders.to_json(con, row), "ship_to": ship["label"] if ship else None}
    if token["scope"] == "full":
        out = {**gifts.to_json(con, row, unlocked=sees_protected(token)), "recipients": recipients, **order}
        if is_school():
            for key in ("santa", "location_id", "location_path"):
                out.pop(key, None)
        return out
    out = {"id": row["id"], "occasion_id": row["occasion_id"], "title": row["title"], "status": row["status"],
           "store": row["store"], "recipients": recipients, **order}
    if token["scope"] == "shop":
        out.update(url=row["url"], price_cents=row["price_cents"], notes=row["notes"])
    return out


def _whole(raw: str | None, name: str) -> int | None:
    if raw is None or raw == "":
        return None
    if not _WHOLE.fullmatch(raw):
        raise ApiError(400, f"{name} must be a whole number.")
    return int(raw)


def _statuses(token: dict, raw: str | None) -> tuple[str, ...] | None:
    scope = scope_of(token)
    if raw is None or raw == "":
        return scope.list_default
    allowed = scope.read or ALL_STATUSES
    asked = tuple(dict.fromkeys(s.strip() for s in raw.split(",") if s.strip()))
    if not asked or any(s not in allowed for s in asked):
        raise ApiError(400, f"status must be among: {', '.join(allowed)}.")
    return asked


def list_gifts(con, token: dict, args) -> list[dict]:
    """GET /api/v1/gifts?occasion_id=&status=a,b&person_id=. The occasion defaults to the current one (none: []);
    status to the scope's default list; person_id keeps the gifts that person receives."""
    occasion_id = _whole(args.get("occasion_id"), "occasion_id")
    if occasion_id is None:
        current = occasions.current(con)
        if current is None:
            return []
        occasion_id = current["id"]
    else:
        occasions.get(con, occasion_id)
    statuses = _statuses(token, args.get("status"))
    person_id = _whole(args.get("person_id"), "person_id")
    sql = f"SELECT g.* FROM gifts g WHERE g.occasion_id = ? AND (? OR NOT {gifts.PROTECTED_SQL})"
    params: list = [occasion_id, int(sees_protected(token))]
    if statuses is not None:
        sql += f" AND g.status IN ({','.join('?' * len(statuses))})"
        params += statuses
    if person_id is not None:
        sql += " AND EXISTS (SELECT 1 FROM gift_recipients x WHERE x.gift_id = g.id AND x.person_id = ?)"
        params.append(person_id)
    return [gift_json(con, r, token) for r in con.execute(sql + " ORDER BY g.id", params).fetchall()]


def occasion_list(con) -> list[dict]:
    """Occasions without their per-person plans (those name protected people and their budgets)."""
    return [{k: o[k] for k in ("id", "name", "kind", "date", "archived", "current")} for o in occasions.list_all(con)]


def address_list(con) -> list[dict]:
    return addresses.list_all(con)


def people_list(con, token: dict) -> list[dict]:
    """Active people, owners marked; protected people only for a token that sees them."""
    require(token, "people")
    out = []
    for p in people.list_people(con):
        if p["protected"] and not sees_protected(token):
            continue
        item = {"id": p["id"], "name": p["name"], "group_id": p["group_id"], "household": p["household"], "owner": p["is_user"]}
        if is_school():
            item["role"] = p["role"]
        out.append(item)
    return out


def budget_list(con, occasion_id: int, token: dict) -> dict:
    """Each person's plan, budget and money in one occasion, from budgets.summary with the token's view of the lock.
    No headline (a gift title from outside the scope's statuses) and no colors."""
    require(token, "budgets")
    s = budgets.summary(con, occasion_id, unlocked=sees_protected(token))
    keys = ("id", "name", "plan", "budget_cents", "spent_cents", "planned_cents", "gift_count")
    rows = [{**{k: p[k] for k in keys}, "group": g["name"]} for g in s["groups"] for p in g["people"]]
    if sees_protected(token):
        rows += [{**{k: p[k] for k in keys}, "group": None, "protected": True} for p in s["protected"]["people"]]
    return {"occasion": s["occasion"], "days_until": s["days_until"], "people": rows}


# ---------------------------------------------------------------- writing
def update_order(con, gift_id: int, data: dict, token: dict) -> None:
    """Order fields only (orders.FIELDS); any other key is 400 before anything is written."""
    visible_row(con, gift_id, token)
    if not data or any(k not in orders.FIELDS for k in data):
        raise ApiError(400, ONLY_ORDER_FIELDS)
    with db.transaction(con):
        orders.update(con, gift_id, data)
        tokens.log_write(con, gift_id, token, "order: " + ", ".join(k for k in orders.FIELDS if k in data))


def set_status(con, gift_id: int, data: dict, token: dict) -> None:
    """{status, ...order fields}. A status outside the scope is 403 (after the 404 for a gift it may not see)."""
    visible_row(con, gift_id, token)
    status = data.get("status")
    if not isinstance(status, str):
        raise ApiError(400, "status must be text.")
    allowed = scope_of(token).set_status
    if allowed is not None and status not in allowed:
        raise refuse(token)
    order = {k: v for k, v in data.items() if k != "status"}
    if any(k not in orders.FIELDS for k in order):
        raise ApiError(400, ONLY_ORDER_FIELDS)
    wishlist.refuse_agent_flip(con, gift_id, status)  # a token never writes to the wishlist
    gifts.set_status(con, gift_id, status, order, actor=token["name"])
    tokens.log_write(con, gift_id, token, f"status: {status}")


def create_gift(con, data: dict, token: dict) -> int:
    """A new gift, for `full` tokens: the web app's POST /api/gifts body; occasion_id defaults to the current one."""
    require(token, "create")
    if is_school() and "santa" in data:
        raise ApiError(400, "Santa is not used in school mode.")
    if data.get("exchange_id") is not None:
        raise ApiError(400, "Exchanges are not available to agents.")
    if "santa" in data and data["santa"] is not None and not sees_protected(token):
        raise refuse(token)
    if not sees_protected(token):
        hidden = people.protected_ids(con)
        for key in ("recipient_ids", "giver_ids"):
            ids = data.get(key)
            if isinstance(ids, list) and hidden & {i for i in ids if isinstance(i, int) and not isinstance(i, bool)}:
                raise ApiError(400, "No such person.")  # to a locked token a protected person does not exist
    if data.get("occasion_id") is None:
        current = occasions.current(con)
        data = {**data, "occasion_id": current["id"] if current else None}
    gid = gifts.create(con, {"email": f"agent:{token['name']}", "name": token["name"]}, data)
    tokens.log_write(con, gid, token, "created")
    return gid
