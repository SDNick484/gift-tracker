"""API tokens for agents: made, listed and revoked in the web app by an unlocked owner, presented on /api/v1 as
`Authorization: Bearer <token>`. Route-free: every function takes a sqlite3.Connection and raises common.ApiError on
bad input.

A token is "gt_" plus 32 random bytes in URL-safe base64. It is returned once, by create(), and never stored: the
table keeps sha256(token) only, and authenticate() looks a presented token up by its hash. A revoked token keeps its
row (and its name), so the notes it left on gifts still name it.

Each token has one scope (SCOPES; what each may do is agent.SCOPES). `unlocked` lets a `full` token see protected
gifts, as an unlocked owner can; it is refused for any other scope and in school mode, which has no lock."""
import hashlib
import secrets

import common
import db
from common import ApiError, flag, is_school, text

SCOPES = ("delivery", "shop", "full")
PREFIX = "gt_"
MAX_NAME = 60


def scopes() -> tuple[str, ...]:
    """The scopes a token can have. The same in both modes: school mode changes what a scope sees, not which exist."""
    return SCOPES


def digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _json(row) -> dict:
    return {"id": row["id"], "name": row["name"], "scope": row["scope"], "unlocked": bool(row["unlocked"]),
            "created_at": row["created_at"], "last_used_at": row["last_used_at"], "revoked_at": row["revoked_at"]}


def list_all(con) -> list[dict]:
    """Every token, newest first. Never a hash, never a token."""
    return [_json(r) for r in con.execute("SELECT * FROM api_tokens ORDER BY id DESC")]


def create(con, data: dict) -> dict:
    """A new token: {name, scope, unlocked?}. Returns its row as list_all() shows it, plus `token`, the only time
    the token itself is given out."""
    name = text(data, "name")
    if not name or len(name) > MAX_NAME:
        raise ApiError(400, f"A token needs a name of 1 to {MAX_NAME} characters.")
    scope = data.get("scope")
    if scope not in SCOPES:
        raise ApiError(400, f"scope must be one of {', '.join(SCOPES)}.")
    unlocked = bool(flag(data, "unlocked", False))
    if unlocked and is_school():
        raise ApiError(400, "There is no lock in school mode.")
    if unlocked and scope != "full":
        raise ApiError(400, "Only a full token can see protected gifts.")
    token = PREFIX + secrets.token_urlsafe(32)
    with db.transaction(con):
        if con.execute("SELECT 1 FROM api_tokens WHERE name = ?", (name,)).fetchone():
            raise ApiError(409, "A token with that name exists. Names stay taken after a token is revoked.")
        tid = con.execute("INSERT INTO api_tokens (name, scope, token_hash, unlocked, created_at) VALUES (?, ?, ?, ?, ?)",
                          (name, scope, digest(token), int(unlocked), common.now())).lastrowid
    row = con.execute("SELECT * FROM api_tokens WHERE id = ?", (tid,)).fetchone()
    return {**_json(row), "token": token}


def revoke(con, token_id: int) -> dict:
    """Revoke a token for good (it keeps its row and name). Revoking twice keeps the first time."""
    row = con.execute("SELECT * FROM api_tokens WHERE id = ?", (token_id,)).fetchone()
    if row is None:
        raise ApiError(404, "No such token.")
    con.execute("UPDATE api_tokens SET revoked_at = COALESCE(revoked_at, ?) WHERE id = ?", (common.now(), token_id))
    return _json(con.execute("SELECT * FROM api_tokens WHERE id = ?", (token_id,)).fetchone())


def bearer(header: str | None) -> str | None:
    """The token in an `Authorization: Bearer <token>` header (the scheme in any case), else None."""
    scheme, _, value = (header or "").strip().partition(" ")
    value = value.strip()
    if scheme.lower() != "bearer" or not value.startswith(PREFIX) or " " in value:
        return None
    return value


def authenticate(con, header: str | None) -> dict | None:
    """{id, name, scope, unlocked} of the live token in the header, else None. Records the use."""
    token = bearer(header)
    if token is None:
        return None
    row = con.execute("SELECT * FROM api_tokens WHERE token_hash = ? AND revoked_at IS NULL", (digest(token),)).fetchone()
    if row is None:
        return None
    con.execute("UPDATE api_tokens SET last_used_at = ? WHERE id = ?", (common.now(), row["id"]))
    return {"id": row["id"], "name": row["name"], "scope": row["scope"], "unlocked": bool(row["unlocked"])}


# ---------------------------------------------------------------- the notes a token leaves on a gift
def log_write(con, gift_id: int, token: dict, action: str) -> None:
    """Record a write by `token` on the gift. Runs inside the caller's transaction, if it has one."""
    con.execute("INSERT INTO gift_agent_log (gift_id, token_id, at, action) VALUES (?, ?, ?, ?)",
                (gift_id, token["id"], common.now(), action))


def gift_log(con, gift_id: int) -> list[dict]:
    """The notes on one gift, newest first: [{at, token, action}] with the token's name."""
    return [{"at": r["at"], "token": r["name"], "action": r["action"]} for r in con.execute(
        "SELECT l.at, t.name, l.action FROM gift_agent_log l JOIN api_tokens t ON t.id = l.token_id "
        "WHERE l.gift_id = ? ORDER BY l.id DESC", (gift_id,))]
