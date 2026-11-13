"""Ship-to addresses for the ship-to check. An address is a label such as "Home" or "Grandparents' house", never a
street address. Exactly one is home (db.py seeds it and a unique index allows no second). Route-free: every function
takes a sqlite3.Connection and raises common.ApiError on bad input."""
import db
from common import ApiError, flag, text

MAX_LABEL = 100


def _json(row) -> dict:
    return {"id": row["id"], "label": row["label"], "is_home": bool(row["is_home"])}


def _row(con, address_id: int):
    row = con.execute("SELECT * FROM addresses WHERE id = ?", (address_id,)).fetchone()
    if row is None:
        raise ApiError(404, "No such address.")
    return row


def exists(con, address_id) -> bool:
    return con.execute("SELECT 1 FROM addresses WHERE id = ?", (address_id,)).fetchone() is not None


def list_all(con) -> list[dict]:
    """Home first, then by label."""
    return [_json(r) for r in con.execute("SELECT * FROM addresses ORDER BY is_home DESC, lower(label), id")]


def _label(data: dict) -> str:
    label = text(data, "label")
    if not label or len(label) > MAX_LABEL:
        raise ApiError(400, f"An address needs a label of 1 to {MAX_LABEL} characters.")
    return label


def create(con, data: dict) -> dict:
    label = _label(data)
    home = flag(data, "is_home", False)
    with db.transaction(con):
        if home:
            con.execute("UPDATE addresses SET is_home = 0")
        aid = con.execute("INSERT INTO addresses (label, is_home) VALUES (?, ?)", (label, int(home))).lastrowid
    return _json(_row(con, aid))


def update(con, address_id: int, data: dict) -> dict:
    """Rename, or make this address home (the old home stops being home). Unflagging the only home is 400:
    make another address home instead."""
    row = _row(con, address_id)
    label = _label(data) if "label" in data else None
    home = flag(data, "is_home")
    if home is False and row["is_home"]:
        raise ApiError(400, "There must be one home address. Make another address home instead.")
    with db.transaction(con):
        if label is not None:
            con.execute("UPDATE addresses SET label = ? WHERE id = ?", (label, address_id))
        if home:
            con.execute("UPDATE addresses SET is_home = 0")
            con.execute("UPDATE addresses SET is_home = 1 WHERE id = ?", (address_id,))
    return _json(_row(con, address_id))


def delete(con, address_id: int) -> None:
    """409 for the home address and for one any gift ships to (app.py requires the unlock first: the second
    answer would otherwise tell a locked caller that a hidden gift ships there)."""
    row = _row(con, address_id)
    if row["is_home"]:
        raise ApiError(409, "The home address cannot be deleted. Make another address home first.")
    if con.execute("SELECT 1 FROM gifts WHERE ship_to_id = ? LIMIT 1", (address_id,)).fetchone():
        raise ApiError(409, "An order ships to this address.")
    con.execute("DELETE FROM addresses WHERE id = ?", (address_id,))
