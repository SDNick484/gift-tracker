"""Gift Tracker: Flask + SQLite. This module holds the Flask object, the request hooks and every route;
route-free logic lives in sibling modules that take a sqlite3.Connection."""
import functools
import os
from urllib.parse import urlparse

from flask import Flask, g, jsonify, request
from werkzeug.exceptions import HTTPException

import budgets
import db as database
import gifts
import identity
import lock
import occasions
import people
import settings
from common import ApiError, json_body, locked

app = Flask(__name__)
app.json.sort_keys = False
app.config["MAX_CONTENT_LENGTH"] = 1024 * 1024  # bodies are small JSON objects

OPEN_PATHS = {"/healthz"}           # the only paths that answer without an identity
SAFE_METHODS = {"GET", "HEAD"}      # every other method is a mutation and needs the CSRF checks
CSP = ("default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self'; connect-src 'self'; "
       "manifest-src 'self'; object-src 'none'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'")


def init_db():
    """Create DATA_DIR and bring the database up to the current schema. Runs at import, so at every start."""
    os.makedirs(settings.DATA_DIR, exist_ok=True)
    lock.load_secret(settings.DATA_DIR)
    con = database.connect(settings.DB_PATH)
    try:
        database.migrate(con)
        people.ensure_users(con, settings.USERS)
    finally:
        con.close()


def db():
    """This request's connection, opened on first use and closed at teardown."""
    if "db" not in g:
        g.db = database.connect(settings.DB_PATH)
    return g.db


@app.teardown_appcontext
def close_db(_exc):
    con = g.pop("db", None)
    if con is not None:
        con.close()


# ---------------------------------------------------------------- guards
@app.before_request
def guard():
    """Identity on every path but /healthz (static files included, and paths that match no route), then CSRF
    checks on every mutation: X-Tracker: 1 (a cross-site page cannot send it without a preflight we never allow),
    a JSON body, and an Origin naming this host."""
    if request.path in OPEN_PATHS:
        return
    user = identity.resolve_user(request.headers, request.remote_addr or "")
    if user is None:
        raise ApiError(403, "Forbidden")
    g.user = user
    if request.method in SAFE_METHODS:
        return
    if request.headers.get("X-Tracker") != "1" or not request.is_json:
        raise ApiError(400, "Bad request.")
    origin = request.headers.get("Origin") or ""
    # netloc only: behind the tunnel the app sees http while the browser's Origin says https.
    if urlparse(origin).netloc.lower() != request.host.lower():
        raise ApiError(403, "Cross-origin request blocked.")


def unlocked() -> bool:
    """Whether this request is past the idle lock: it may see and change gifts with a protected recipient, and
    Santa. Worked out once per request; the lock routes set g.unlocked themselves."""
    if "unlocked" not in g:
        g.unlocked = lock.is_unlocked(request, g.user["email"])
    return g.unlocked


def unlock_required(view):
    """423 {"error": "Locked", "locked": true} unless the request is unlocked."""
    @functools.wraps(view)
    def wrapper(*args, **kwargs):
        if not unlocked():
            raise locked()
        return view(*args, **kwargs)
    return wrapper


@app.after_request
def slide_unlock(resp):
    """Re-issue the unlock cookie on every response to an unlocked request (the idle window slides), and clear a
    cookie that no longer unlocks (expired, another user's, or after POST /api/lock/lock)."""
    if "user" in g:
        if unlocked():
            lock.set_cookie(resp, g.user["email"])
        elif lock.COOKIE in request.cookies:
            lock.clear_cookie(resp)
    return resp


@app.after_request
def security_headers(resp):
    resp.headers["Content-Security-Policy"] = CSP
    resp.headers["Cache-Control"] = "no-store"
    resp.headers["Referrer-Policy"] = "no-referrer"
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["X-Frame-Options"] = "DENY"
    return resp


@app.errorhandler(ApiError)
def api_error(e):
    return jsonify(error=e.message, **e.extra), e.status


@app.errorhandler(HTTPException)
def http_error(e):
    return jsonify(error=e.name), e.code


# ---------------------------------------------------------------- routes
@app.get("/healthz")
def healthz():
    return jsonify(ok=True)


@app.get("/api/me")
def me():
    return jsonify(email=g.user["email"], name=g.user["name"], app_name=settings.APP_NAME,
                   has_pin=lock.has_pin(db(), g.user["email"]), unlocked=unlocked(),
                   lock_idle_seconds=settings.LOCK_IDLE_SECONDS)


# -- the idle lock
@app.post("/api/lock/pin")
def lock_set_pin():
    """Set a PIN, or change one with `current`. Does not unlock."""
    data, email = json_body(), g.user["email"]
    if not lock.valid_pin(data.get("pin")):
        raise ApiError(400, "A PIN is 4 to 8 digits.")
    if lock.has_pin(db(), email):
        current = data.get("current")
        if not isinstance(current, str) or not current:
            raise ApiError(400, "Enter your current PIN.")
        if not lock.check_pin(db(), email, current):
            raise ApiError(403, "Wrong PIN.")
    lock.set_pin(db(), email, data["pin"])
    return jsonify(ok=True)


@app.post("/api/lock/unlock")
def lock_unlock():
    pin, email = json_body().get("pin"), g.user["email"]
    if not isinstance(pin, str):
        raise ApiError(400, "Enter your PIN.")
    if not lock.has_pin(db(), email):
        raise ApiError(409, "Set a PIN first.")
    if not lock.check_pin(db(), email, pin):
        raise ApiError(403, "Wrong PIN.")
    g.unlocked = True  # slide_unlock issues the cookie
    return jsonify(ok=True, unlocked=True)


@app.post("/api/lock/lock")
def lock_lock():
    g.unlocked = False  # slide_unlock clears the cookie
    return jsonify(ok=True, unlocked=False)


# -- groups
@app.get("/api/groups")
def groups_list():
    return jsonify(people.list_groups(db()))


@app.post("/api/groups")
def groups_create():
    return jsonify(people.create_group(db(), json_body())), 201


@app.patch("/api/groups/<int:group_id>")
def groups_update(group_id):
    return jsonify(people.update_group(db(), group_id, json_body()))


@app.delete("/api/groups/<int:group_id>")
def groups_delete(group_id):
    people.delete_group(db(), group_id)
    return jsonify(ok=True)


# -- people and households
@app.get("/api/people")
def people_list():
    return jsonify(people.list_people(db(), include_inactive=request.args.get("inactive") == "1"))


@app.post("/api/people")
def people_create():
    return jsonify(people.create_person(db(), json_body())), 201


@app.patch("/api/people/<int:person_id>")
def people_update(person_id):
    """Who is protected decides what the lock hides, so while locked a protected person cannot be unprotected
    (that would show their gifts). Protecting someone only hides more, so it needs no unlock."""
    data = json_body()
    # false and null both store 0; any other non-boolean is 400 in update_person
    if "protected" in data and data["protected"] in (False, None) and not unlocked() \
            and people.get_person(db(), person_id)["protected"]:
        raise locked()
    return jsonify(people.update_person(db(), person_id, data))


@app.delete("/api/people/<int:person_id>")
@unlock_required  # "has gifts" (409) against "deleted" would tell whether a hidden gift involves this person
def people_delete(person_id):
    people.delete_person(db(), person_id)
    return jsonify(ok=True)


@app.get("/api/households")
def households_list():
    return jsonify(people.households(db()))


# -- occasions and the per-person plan
@app.get("/api/occasions")
def occasions_list():
    return jsonify(occasions.list_all(db()))


@app.post("/api/occasions")
def occasions_create():
    return jsonify(id=occasions.create_from(db(), json_body())), 201


@app.patch("/api/occasions/<int:occasion_id>")
def occasions_update(occasion_id):
    occasions.update(db(), occasion_id, json_body())
    return jsonify(ok=True)


@app.put("/api/occasions/<int:occasion_id>/people/<int:person_id>")
def occasion_plan_set(occasion_id, person_id):
    occasions.set_plan_from(db(), occasion_id, person_id, json_body())
    return jsonify(ok=True)


@app.delete("/api/occasions/<int:occasion_id>/people/<int:person_id>")
def occasion_plan_remove(occasion_id, person_id):
    occasions.remove_plan(db(), occasion_id, person_id)
    return jsonify(ok=True)


# -- gifts
# While locked, a gift with a protected recipient answers exactly like a gift that does not exist (404), a write
# may not give a gift a protected recipient or touch Santa (423), and gift JSON carries no `santa` key.
def gift_json(row):
    return gifts.to_json(db(), row, unlocked=unlocked())


def visible_gift(gift_id):
    """The gift row, or the same 404 as a missing gift when it is protected and this request is locked."""
    if not gifts.visible(db(), gift_id, unlocked=unlocked()):
        raise ApiError(404, "No such gift.")
    return gifts.get(db(), gift_id)


def refuse_locked_write(data: dict) -> None:
    if unlocked():
        return
    if isinstance(data.get("santa"), bool):  # any other value is 400 in gifts; null changes nothing
        raise locked()
    rids = data.get("recipient_ids")
    if isinstance(rids, list) and people.protected_ids(db()) & {i for i in rids if isinstance(i, int)}:
        raise locked()


@app.post("/api/gifts")
def gifts_create():
    data = json_body()
    refuse_locked_write(data)
    gid = gifts.create(db(), g.user, data)
    return jsonify(gift_json(visible_gift(gid))), 201


@app.get("/api/gifts/<int:gift_id>")
def gifts_get(gift_id):
    return jsonify(gift_json(visible_gift(gift_id)))


@app.patch("/api/gifts/<int:gift_id>")
def gifts_update(gift_id):
    visible_gift(gift_id)
    data = json_body()
    refuse_locked_write(data)
    gifts.update(db(), gift_id, data)
    return jsonify(gift_json(visible_gift(gift_id)))


@app.delete("/api/gifts/<int:gift_id>")
def gifts_delete(gift_id):
    visible_gift(gift_id)
    gifts.delete(db(), gift_id)
    return jsonify(ok=True)


@app.post("/api/gifts/<int:gift_id>/status")
def gifts_status(gift_id):
    visible_gift(gift_id)
    status = json_body().get("status")
    if not isinstance(status, str):
        raise ApiError(400, "status must be text.")
    gifts.set_status(db(), gift_id, status)
    return jsonify(gift_json(visible_gift(gift_id)))


@app.get("/api/occasions/<int:occasion_id>/people/<int:person_id>/gifts")
def person_gifts(occasion_id, person_id):
    return jsonify([gift_json(r) for r in gifts.list_for_person(db(), occasion_id, person_id, unlocked=unlocked())])


@app.get("/api/people/<int:person_id>/history")
def person_history(person_id):
    rows = gifts.history(db(), person_id, unlocked=unlocked())
    return jsonify(person=people.get_person(db(), person_id),
                   gifts=[{**gift_json(r), "occasion": {"id": r["occasion_id"], "name": r["occasion_name"],
                                                         "date": r["occasion_date"]}} for r in rows])


# -- budgets
@app.get("/api/occasions/<int:occasion_id>/summary")
def occasion_summary(occasion_id):
    return jsonify(budgets.summary(db(), occasion_id, unlocked=unlocked()))


init_db()

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=settings.PORT)
