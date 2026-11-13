"""Order fields on a gift, and the ship-to check. Route-free: every function takes a sqlite3.Connection and raises
common.ApiError on bad input. No lock logic here: callers (app.py, gifts.py, lists.py) have already decided the gift
is visible to this request.

Fields: seller, order_number, ship_to_id (an address), ship_elsewhere_ok, eta and arrived_on (YYYY-MM-DD), and
ordered_by (who placed the order: an owner's name, or an agent token's name; set by gifts.set_status, not by update).

`update` is the one function that writes the six order fields from a request-shaped dict. The status route and
PATCH go through it (via gifts), and so does the agent API.

Marking a gift ordered needs seller, ship_to_id and eta (require_ordered). `ship_warning` is computed on every read,
never stored: true when the gift ships to an address that is not home, unless ship_elsewhere_ok says it is meant to."""
import re
from datetime import date

import addresses
import common
from common import ApiError, flag, text

FIELDS = ("seller", "order_number", "ship_to_id", "ship_elsewhere_ok", "eta", "arrived_on")
REQUIRED_FOR_ORDERED = ("seller", "ship_to_id", "eta")
LIMITS = {"seller": 200, "order_number": 100}
_DAY = re.compile(r"\d{4}-\d{2}-\d{2}")


def _day(data: dict, key: str) -> str | None:
    value = text(data, key)
    if not value:
        return None
    try:
        if not _DAY.fullmatch(value):
            raise ValueError
        date.fromisoformat(value)
    except ValueError:
        raise ApiError(400, f"{key} must be a real date like 2026-12-09.") from None
    return value


def parse(con, data: dict) -> dict:
    """The order fields present in `data`, validated, as {column: value}. Empty text and null clear a field. Raises
    400 before anything is written."""
    out: dict = {}
    for key, limit in LIMITS.items():
        if key in data:
            value = text(data, key)
            if len(value) > limit:
                raise ApiError(400, f"{key} is too long.")
            out[key] = value or None
    if "ship_to_id" in data:
        sid = data["ship_to_id"]
        if sid is not None and (isinstance(sid, bool) or not isinstance(sid, int) or not addresses.exists(con, sid)):
            raise ApiError(400, "ship_to_id must be an existing address or null.")
        out["ship_to_id"] = sid
    if "ship_elsewhere_ok" in data:
        out["ship_elsewhere_ok"] = int(bool(flag(data, "ship_elsewhere_ok")))
    for key in ("eta", "arrived_on"):
        if key in data:
            out[key] = _day(data, key)
    return out


def write(con, gift_id: int, fields: dict) -> None:
    """Store already-parsed fields (and touch updated_at). Runs inside the caller's transaction, if it has one."""
    if fields:
        fields = {**fields, "updated_at": common.now()}
        con.execute(f"UPDATE gifts SET {', '.join(f'{k} = ?' for k in fields)} WHERE id = ?", (*fields.values(), gift_id))


def update(con, gift_id: int, data: dict) -> None:
    """Validate and store whichever of FIELDS `data` has; other keys are ignored. 404 for a missing gift."""
    if not con.execute("SELECT 1 FROM gifts WHERE id = ?", (gift_id,)).fetchone():
        raise ApiError(404, "No such gift.")
    write(con, gift_id, parse(con, data))


def require_ordered(merged: dict) -> None:
    """`merged` holds the gift's seller, ship_to_id and eta as they would be after the change. 400 naming what is
    missing, with `missing` in the body."""
    missing = [k for k in REQUIRED_FOR_ORDERED if not merged.get(k)]
    if missing:
        raise ApiError(400, f"Marking a gift ordered needs {', '.join(missing)}.", missing=missing)


def ship_warning(con, row) -> bool:
    if row["ship_to_id"] is None or row["ship_elsewhere_ok"]:
        return False
    home = con.execute("SELECT is_home FROM addresses WHERE id = ?", (row["ship_to_id"],)).fetchone()
    return home is not None and not home["is_home"]


def to_json(con, row) -> dict:
    return {"seller": row["seller"], "order_number": row["order_number"], "ship_to_id": row["ship_to_id"],
            "ship_elsewhere_ok": bool(row["ship_elsewhere_ok"]), "eta": row["eta"], "arrived_on": row["arrived_on"],
            "ordered_by": row["ordered_by"], "ship_warning": ship_warning(con, row)}
