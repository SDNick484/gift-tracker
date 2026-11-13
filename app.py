"""Gift Tracker: Flask + SQLite. This module holds the Flask object, the request hooks and every route;
route-free logic lives in sibling modules that take a sqlite3.Connection."""
import os
from urllib.parse import urlparse

from flask import Flask, g, jsonify, request
from werkzeug.exceptions import HTTPException

import db as database
import identity
import occasions
import people
import settings
from common import ApiError, json_body

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
    return jsonify(error=e.message), e.status


@app.errorhandler(HTTPException)
def http_error(e):
    return jsonify(error=e.name), e.code


# ---------------------------------------------------------------- routes
@app.get("/healthz")
def healthz():
    return jsonify(ok=True)


@app.get("/api/me")
def me():
    return jsonify(email=g.user["email"], name=g.user["name"], app_name=settings.APP_NAME)


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
    return jsonify(people.update_person(db(), person_id, json_body()))


@app.delete("/api/people/<int:person_id>")
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


init_db()

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=settings.PORT)
