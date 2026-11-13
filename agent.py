"""The agent API (/api/v1): what each token scope may read and write, and what a gift looks like to it. Route-free:
every function takes a sqlite3.Connection and the request's token ({id, name, scope, unlocked}, from
tokens.authenticate) and raises common.ApiError.

Privacy: a token is locked unless sees_protected() (a `full` token made with `unlocked`, in family mode). A locked
token never gets a gift with a protected recipient, a protected person, a `santa` key or a hiding place: lists filter
on gifts.PROTECTED_SQL and one-gift routes answer the same 404 as a missing gift."""
from common import is_school


def sees_protected(token: dict) -> bool:
    """Whether this token may see protected gifts and Santa: only a full token made unlocked, and never in school
    mode (which has no lock and nothing protected)."""
    return token["scope"] == "full" and token["unlocked"] and not is_school()


def me(token: dict) -> dict:
    return {"name": token["name"], "scope": token["scope"], "unlocked": sees_protected(token),
            "mode": "school" if is_school() else "family"}
