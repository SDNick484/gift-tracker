import sqlite3

import pytest

import db


def test_migrate_creates_every_table(con):
    names = {r["name"] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"meta", "groups", "people", "users", "occasions", "occasion_people", "gifts", "gift_recipients", "gift_givers"} <= names
    assert con.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()[0] == "1"


def test_migrate_is_idempotent(con):
    db.migrate(con); db.migrate(con)
    assert con.execute("SELECT COUNT(*) FROM meta WHERE key='schema_version'").fetchone()[0] == 1


def test_foreign_keys_enforced(con):
    with pytest.raises(sqlite3.IntegrityError):
        con.execute("INSERT INTO gifts(occasion_id,title,status,created_by,created_at,updated_at) VALUES (999,'x','idea','a',0,0)")


def test_healthz_is_open_and_bare(client):
    r = client.get("/healthz")
    assert r.status_code == 200 and r.get_json() == {"ok": True}
