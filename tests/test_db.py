import sqlite3

import pytest

import db


def test_migrate_creates_every_table(con):
    names = {r["name"] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"meta", "groups", "people", "users", "occasions", "occasion_people", "gifts", "gift_recipients", "gift_givers"} <= names
    assert con.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()[0] == "2"


def test_migrate_is_idempotent(con):
    db.migrate(con); db.migrate(con)
    assert con.execute("SELECT COUNT(*) FROM meta WHERE key='schema_version'").fetchone()[0] == 1


def test_foreign_keys_enforced(con):
    with pytest.raises(sqlite3.IntegrityError):
        con.execute("INSERT INTO gifts(occasion_id,title,status,created_by,created_at,updated_at) VALUES (999,'x','idea','a',0,0)")


def test_healthz_is_open_and_bare(client):
    r = client.get("/healthz")
    assert r.status_code == 200 and r.get_json() == {"ok": True}


# ---------------------------------------------------------------- version 2: staff role, recipient detail and done
def _v1_database(path):
    """A database exactly as version 1 left it, with a gift for two people."""
    c = db.connect(str(path))
    for stmt in db._statements(db.SCHEMA):
        c.execute(stmt)
    c.execute("INSERT INTO meta VALUES ('schema_version', '1')")
    c.execute("INSERT INTO people (id, name, protected) VALUES (1, 'Ms. Avery', 0), (2, 'Mr. Diaz', 1)")
    c.execute("INSERT INTO occasions (id, name, kind, date) VALUES (1, 'Christmas 2025', 'christmas', '2025-12-19')")
    c.execute("INSERT INTO gifts (id, occasion_id, title, status, santa, created_by, created_at, updated_at) "
              "VALUES (1, 1, 'Card', 'given', 1, 'a', 0, 0)")
    c.execute("INSERT INTO gift_recipients VALUES (1, 1), (1, 2)")
    return c


def test_a_v1_database_migrates_to_v2_keeping_its_rows(tmp_path):
    c = _v1_database(tmp_path / "t.db")
    db.migrate(c)
    assert c.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()[0] == "2"
    assert [tuple(r) for r in c.execute("SELECT id, name, protected, role FROM people ORDER BY id")] == [(1, "Ms. Avery", 0, None), (2, "Mr. Diaz", 1, None)]
    rows = c.execute("SELECT gift_id, person_id, detail, done FROM gift_recipients ORDER BY person_id")
    assert [tuple(r) for r in rows] == [(1, 1, None, 0), (1, 2, None, 0)]
    assert tuple(c.execute("SELECT title, santa FROM gifts").fetchone()) == ("Card", 1)
    db.migrate(c)  # safe to run again
    assert c.execute("SELECT COUNT(*) FROM gift_recipients").fetchone()[0] == 2
    assert c.execute("SELECT COUNT(*) FROM meta WHERE key='schema_version'").fetchone()[0] == 1
    c.close()


def test_the_migrated_columns_match_a_fresh_database(tmp_path, con):
    c = _v1_database(tmp_path / "t.db")
    db.migrate(c)

    def shape(conn, table):
        return [(r["name"], r["type"], r["notnull"], r["dflt_value"]) for r in conn.execute(f"PRAGMA table_info({table})")]
    for table in ("people", "gift_recipients"):
        assert shape(c, table) == shape(con, table), table
    assert ("done", "INTEGER", 1, "0") in shape(con, "gift_recipients")
    assert ("detail", "TEXT", 0, None) in shape(con, "gift_recipients")
    assert ("role", "TEXT", 0, None) in shape(con, "people")
    c.close()


def test_a_database_newer_than_the_code_is_refused(con):
    con.execute("UPDATE meta SET value = ? WHERE key = 'schema_version'", (str(db.SCHEMA_VERSION + 1),))
    with pytest.raises(RuntimeError, match="newer than this code"):
        db.migrate(con)
