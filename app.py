"""Gift Tracker: Flask + SQLite. This module holds the Flask object, the request hooks and every route;
route-free logic lives in sibling modules that take a sqlite3.Connection."""
import functools
import html
import json
import os
from urllib.parse import urlparse

from flask import Flask, abort, g, jsonify, request
from werkzeug.exceptions import HTTPException

import addresses
import agent
import boards
import budgets
import db as database
import gifts
import identity
import lists
import lock
import locations
import occasions
import people
import school
import settings
import tokens
from common import ApiError, is_school, json_body, locked

app = Flask(__name__)
app.json.sort_keys = False
app.config["MAX_CONTENT_LENGTH"] = 1024 * 1024  # bodies are small JSON objects

OPEN_PATHS = {"/healthz"}           # the only paths that answer without an identity
AGENT_API = "/api/v1"               # bearer tokens only: see agent_guard()
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
    if request.path == AGENT_API or request.path.startswith(AGENT_API + "/"):
        return agent_guard()
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


def agent_guard():
    """/api/v1 admits a live bearer token and nothing else: no Cloudflare identity, no cookie, no CSRF header (a
    browser never sends a bearer header on its own). Presenting nothing at all is the 403 every anonymous request
    gets; presenting anything that is not a live token (a Cloudflare identity, a bad or revoked token) is 401."""
    token = tokens.authenticate(db(), request.headers.get("Authorization"))
    if token is None:
        if "Authorization" not in request.headers and not any(h in request.headers for h in identity.CF_HEADERS):
            raise ApiError(403, "Forbidden")
        raise ApiError(401, "Unauthorized")
    g.token = token
    g.unlocked = agent.sees_protected(token)  # set before any view runs: the unlock cookie is never read here
    if request.method not in SAFE_METHODS and not request.is_json:
        raise ApiError(400, "Bad request.")


def unlocked() -> bool:
    """Whether this request is past the idle lock: it may see and change gifts with a protected recipient, and
    Santa. Worked out once per request; the lock routes set g.unlocked themselves."""
    if "unlocked" not in g:
        g.unlocked = is_school() or lock.is_unlocked(request, g.user["email"])  # school mode has no lock
    return g.unlocked


def unlock_required(view):
    """423 {"error": "Locked", "locked": true} unless the request is unlocked."""
    @functools.wraps(view)
    def wrapper(*args, **kwargs):
        if not unlocked():
            raise locked()
        return view(*args, **kwargs)
    return wrapper


# ---- modes. A route that exists in one mode only answers the generic 404 in the other (the body of a path that does
# not exist), so the API of a family instance has no school routes and a school instance has no lock or boards.
# The wrapper carries `mode_only` so tests can find every such view through app.url_map.
def _mode_only(mode, wrong_mode):
    def decorate(view):
        @functools.wraps(view)
        def wrapper(*args, **kwargs):
            if wrong_mode():
                abort(404)
            return view(*args, **kwargs)
        wrapper.mode_only = mode
        return wrapper
    return decorate


family_only = _mode_only("family", is_school)
school_only = _mode_only("school", lambda: not is_school())


@app.after_request
def slide_unlock(resp):
    """Re-issue the unlock cookie on every response to an unlocked request (the idle window slides), and clear a
    cookie that no longer unlocks (expired, another user's, or after POST /api/lock/lock). School mode has no
    lock and no cookie."""
    if "user" in g and not is_school():
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
    resp = jsonify(error=e.message, **e.extra)
    if e.status == 401:
        resp.headers["WWW-Authenticate"] = 'Bearer realm="api"'
    return resp, e.status


@app.errorhandler(HTTPException)
def http_error(e):
    return jsonify(error=e.name), e.code


# ---------------------------------------------------------------- routes
@app.get("/healthz")
def healthz():
    return jsonify(ok=True)


@app.get("/")
def index():
    """The single page, with APP_NAME as its title and its home-screen name (the only two places the page names
    itself). Everything else is static/js. Read per request: the file is small and the setting is fixed per process."""
    with open(os.path.join(app.static_folder, "index.html"), encoding="utf-8") as f:
        page = f.read().replace("__APP_NAME__", html.escape(settings.APP_NAME, quote=True))
    return app.response_class(page, mimetype="text/html")


@app.get("/manifest.json")
def manifest():
    """static/manifest.json with name and short_name set to APP_NAME. Behind the identity guard like everything
    else, so index.html asks for it with crossorigin="use-credentials"."""
    with open(os.path.join(app.static_folder, "manifest.json"), encoding="utf-8") as f:
        data = {**json.load(f), "name": settings.APP_NAME, "short_name": settings.APP_NAME}
    return app.response_class(json.dumps(data), mimetype="application/manifest+json")


@app.get("/api/me")
def me():
    mode = {"mode": "school"} if is_school() else {}  # a family instance's answer has no mode key
    return jsonify(email=g.user["email"], name=g.user["name"], person_id=people.person_id_of(db(), g.user["email"]),
                   app_name=settings.APP_NAME, has_pin=lock.has_pin(db(), g.user["email"]), unlocked=unlocked(),
                   lock_idle_seconds=settings.LOCK_IDLE_SECONDS, **mode)


# -- the idle lock
@app.post("/api/lock/pin")
@family_only
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
@family_only
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
@family_only
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
    out = gifts.to_json(db(), row, unlocked=unlocked())
    if is_school():
        out.pop("santa", None)  # school mode has no Santa, whatever the column says
        out.pop("location_id", None)  # nor hiding places
        out.pop("location_path", None)
    return out


def visible_gift(gift_id):
    """The gift row, or the same 404 as a missing gift when it is protected and this request is locked."""
    if not gifts.visible(db(), gift_id, unlocked=unlocked()):
        raise ApiError(404, "No such gift.")
    return gifts.get(db(), gift_id)


def refuse_locked_write(data: dict) -> None:
    if is_school():
        if "santa" in data:
            raise ApiError(400, "Santa is not used in school mode.")
        if "location_id" in data:
            raise ApiError(400, "Hiding places are not used in school mode.")
        return  # nothing is locked in school mode
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
    gifts.update(db(), gift_id, data, actor=g.user["name"])
    return jsonify(gift_json(visible_gift(gift_id)))


@app.delete("/api/gifts/<int:gift_id>")
def gifts_delete(gift_id):
    visible_gift(gift_id)
    gifts.delete(db(), gift_id)
    return jsonify(ok=True)


@app.post("/api/gifts/<int:gift_id>/status")
def gifts_status(gift_id):
    visible_gift(gift_id)
    data = json_body()
    status = data.get("status")
    if not isinstance(status, str):
        raise ApiError(400, "status must be text.")
    gifts.set_status(db(), gift_id, status, data, actor=g.user["name"])  # the body may carry order fields
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


# -- school mode: the staff-by-gift matrix. These exist only when MODE=school (school_only: 404 otherwise).
@app.get("/api/occasions/<int:occasion_id>/matrix")
@school_only
def school_matrix(occasion_id):
    return jsonify(school.matrix(db(), occasion_id, unlocked=unlocked()))


@app.put("/api/gifts/<int:gift_id>/recipients/<int:person_id>")
@school_only
def school_cell_set(gift_id, person_id):
    """Add the person to the gift (201) or update their cell (200): {detail?, done?}."""
    visible_gift(gift_id)
    out, created = school.set_cell(db(), gift_id, person_id, json_body())
    return jsonify(out), 201 if created else 200


@app.delete("/api/gifts/<int:gift_id>/recipients/<int:person_id>")
@school_only
def school_cell_remove(gift_id, person_id):
    visible_gift(gift_id)
    return jsonify(school.remove_cell(db(), gift_id, person_id))


@app.post("/api/gifts/<int:gift_id>/recipients")
@school_only
def school_role_add(gift_id):
    """{role}: everyone with that role becomes a recipient."""
    visible_gift(gift_id)
    return jsonify(school.add_role(db(), gift_id, json_body()))


# -- a protected person's board
@app.get("/api/occasions/<int:occasion_id>/board/<int:person_id>")
@family_only
@unlock_required
def person_board(occasion_id, person_id):
    return jsonify(boards.board(db(), occasion_id, person_id))


@app.post("/api/gifts/<int:gift_id>/assign")
@family_only
@unlock_required
def gifts_assign(gift_id):
    """{giver_ids, expected_giver_ids}: 409 when the givers are no longer the ones the caller saw."""
    visible_gift(gift_id)
    giver_ids, expected = boards.parse_assign(json_body())
    boards.assign(db(), gift_id, giver_ids, expected)
    return jsonify(gift_json(visible_gift(gift_id)))


# -- after purchase: ship-to addresses (both modes) and hiding places (family only: school mode has none).
# Deleting either needs the unlock: "an order ships there" or "something is hidden there" (409) against "deleted"
# would tell a locked caller that a hidden gift uses it. Neither list says which gifts use what.
@app.get("/api/addresses")
def addresses_list():
    return jsonify(addresses.list_all(db()))


@app.post("/api/addresses")
def addresses_create():
    return jsonify(addresses.create(db(), json_body())), 201


@app.patch("/api/addresses/<int:address_id>")
def addresses_update(address_id):
    return jsonify(addresses.update(db(), address_id, json_body()))


@app.delete("/api/addresses/<int:address_id>")
@unlock_required
def addresses_delete(address_id):
    addresses.delete(db(), address_id)
    return jsonify(ok=True)


@app.get("/api/locations")
@family_only
def locations_list():
    return jsonify(locations.tree(db()))


@app.post("/api/locations")
@family_only
def locations_create():
    return jsonify(locations.create(db(), json_body())), 201


@app.patch("/api/locations/<int:location_id>")
@family_only
def locations_update(location_id):
    return jsonify(locations.update(db(), location_id, json_body()))


@app.delete("/api/locations/<int:location_id>")
@family_only
@unlock_required
def locations_delete(location_id):
    locations.delete(db(), location_id)
    return jsonify(ok=True)


# -- after purchase: the working lists. All filter on the lock (gifts.PROTECTED_SQL) inside lists.py; "where" is
# family only because it is about hiding places.
@app.get("/api/occasions/<int:occasion_id>/lists/pending")
def lists_pending(occasion_id):
    return jsonify(lists.pending(db(), occasion_id, unlocked=unlocked()))


@app.get("/api/occasions/<int:occasion_id>/lists/to-wrap")
def lists_to_wrap(occasion_id):
    return jsonify(lists.to_wrap(db(), occasion_id, unlocked=unlocked()))


@app.get("/api/occasions/<int:occasion_id>/lists/where")
@family_only
def lists_where(occasion_id):
    return jsonify(lists.where(db(), occasion_id, unlocked=unlocked()))


@app.get("/api/occasions/<int:occasion_id>/lists/counts")
def lists_counts(occasion_id):
    return jsonify(lists.counts(db(), occasion_id, unlocked=unlocked()))


# -- agent tokens, managed by a signed-in owner. All three need the unlock: a full token is as strong as the PIN, and
# the list names which agents exist. A token cannot reach these (it has no Cloudflare identity).
@app.get("/api/tokens")
@unlock_required
def tokens_list():
    return jsonify(tokens.list_all(db()))


@app.post("/api/tokens")
@unlock_required
def tokens_create():
    return jsonify(tokens.create(db(), json_body())), 201


@app.delete("/api/tokens/<int:token_id>")
@unlock_required
def tokens_revoke(token_id):
    return jsonify(tokens.revoke(db(), token_id))


# -- the agent API: /api/v1, bearer tokens only (agent_guard). g.token is the caller; g.unlocked is the token's view.
# Path arguments are named `gift` and `occasion`; the logic and the scope rules are in agent.py.
@app.get("/api/v1/me")
def v1_me():
    return jsonify(agent.me(g.token))


init_db()

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=settings.PORT)
