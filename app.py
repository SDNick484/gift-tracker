"""Gift Tracker: Flask + SQLite. This module holds the Flask object, the request hooks and every route;
route-free logic lives in sibling modules that take a sqlite3.Connection."""
import os

from flask import Flask, g, jsonify

import db as database
import settings

app = Flask(__name__)
app.json.sort_keys = False


def init_db():
    """Create DATA_DIR and bring the database up to the current schema. Runs at import, so at every start."""
    os.makedirs(settings.DATA_DIR, exist_ok=True)
    con = database.connect(settings.DB_PATH)
    try:
        database.migrate(con)
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


@app.get("/healthz")
def healthz():
    return jsonify(ok=True)


init_db()

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=settings.PORT)
