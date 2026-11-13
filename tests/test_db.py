import sqlite3

import pytest

import db


def test_migrate_creates_every_table(con):
    names = {r["name"] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"meta", "groups", "people", "users", "occasions", "occasion_people", "gifts", "gift_recipients", "gift_givers"} <= names
    assert con.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()[0] == str(db.SCHEMA_VERSION)


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
    assert c.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()[0] == str(db.SCHEMA_VERSION)
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


# ---------------------------------------------------------------- version 3: locations, addresses, order fields
GIFT_V3_COLUMNS = ("seller", "order_number", "ship_to_id", "ship_elsewhere_ok", "eta", "arrived_on", "ordered_by", "location_id")


def _v2_database(path):
    """A database exactly as version 2 left it: a gift for one person, with a detail and a done flag."""
    c = db.connect(str(path))
    for stmt in db._statements(db.SCHEMA):
        c.execute(stmt)
    db.MIGRATIONS[2](c)
    c.execute("INSERT INTO meta VALUES ('schema_version', '2')")
    c.execute("INSERT INTO people (id, name, role) VALUES (1, 'Ms. Avery', 'homeroom')")
    c.execute("INSERT INTO occasions (id, name, kind, date) VALUES (1, 'Christmas 2025', 'christmas', '2025-12-19')")
    c.execute("INSERT INTO gifts (id, occasion_id, title, status, santa, store, created_by, created_at, updated_at) "
              "VALUES (1, 1, 'Card', 'given', 1, 'Shop', 'a', 0, 0)")
    c.execute("INSERT INTO gift_recipients (gift_id, person_id, detail, done) VALUES (1, 1, '$35', 1)")
    return c


def test_a_v2_database_migrates_to_v3_keeping_its_rows(tmp_path):
    c = _v2_database(tmp_path / "t.db")
    db.migrate(c)
    assert db.SCHEMA_VERSION >= 3
    assert c.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()[0] == str(db.SCHEMA_VERSION)
    row = c.execute(f"SELECT title, status, santa, store, {', '.join(GIFT_V3_COLUMNS)} FROM gifts").fetchone()
    assert tuple(row) == ("Card", "given", 1, "Shop", None, None, None, 0, None, None, None, None)
    assert [tuple(r) for r in c.execute("SELECT gift_id, person_id, detail, done FROM gift_recipients")] == [(1, 1, "$35", 1)]
    assert tuple(c.execute("SELECT name, role FROM people").fetchone()) == ("Ms. Avery", "homeroom")
    assert [tuple(r) for r in c.execute("SELECT label, is_home FROM addresses")] == [("Home", 1)]
    assert c.execute("SELECT COUNT(*) FROM locations").fetchone()[0] == 0
    db.migrate(c)  # safe to run again: still one home address, one version row
    assert c.execute("SELECT COUNT(*) FROM addresses").fetchone()[0] == 1
    assert c.execute("SELECT COUNT(*) FROM meta WHERE key='schema_version'").fetchone()[0] == 1
    c.close()


def _shape(conn, table):
    return [(r["name"], r["type"], r["notnull"], r["dflt_value"]) for r in conn.execute(f"PRAGMA table_info({table})")]


def test_a_fresh_database_has_the_v3_tables_and_columns(con):
    assert [r[0] for r in con.execute("SELECT name FROM pragma_table_info('locations')")] == ["id", "parent_id", "name", "sort_order"]
    assert [r[0] for r in con.execute("SELECT name FROM pragma_table_info('addresses')")] == ["id", "label", "is_home"]
    cols = {name: (type_, notnull, default) for name, type_, notnull, default in _shape(con, "gifts")}
    assert all(c in cols for c in GIFT_V3_COLUMNS)
    assert cols["ship_elsewhere_ok"] == ("INTEGER", 1, "0")
    assert cols["eta"] == ("TEXT", 0, None)
    assert [tuple(r) for r in con.execute("SELECT label, is_home FROM addresses")] == [("Home", 1)]


def test_the_migrated_tables_match_a_fresh_database(tmp_path, con):
    c = _v2_database(tmp_path / "t.db")
    db.migrate(c)
    for table in ("gifts", "locations", "addresses"):
        assert _shape(c, table) == _shape(con, table), table
    c.close()


def test_a_second_home_address_is_refused_by_the_database(con):
    with pytest.raises(sqlite3.IntegrityError):
        con.execute("INSERT INTO addresses (label, is_home) VALUES ('Other', 1)")
    con.execute("INSERT INTO addresses (label, is_home) VALUES ('Other', 0)")
    con.execute("INSERT INTO addresses (label, is_home) VALUES ('Another', 0)")


def _gift_in(con, location_id):
    con.execute("INSERT INTO occasions (id, name, kind, date) VALUES (1, 'X', 'christmas', '2026-12-25')")
    con.execute("INSERT INTO gifts (id, occasion_id, title, status, location_id, created_by, created_at, updated_at) "
                "VALUES (1, 1, 'Camera', 'arrived', ?, 'a', 0, 0)", (location_id,))


def test_deleting_a_location_unplaces_its_gifts_and_keeps_them(con):
    con.execute("INSERT INTO locations (id, name) VALUES (1, 'Garage')")
    _gift_in(con, 1)
    con.execute("DELETE FROM locations WHERE id = 1")
    assert [tuple(r) for r in con.execute("SELECT title, location_id FROM gifts")] == [("Camera", None)]


def test_deleting_a_location_deletes_its_sub_locations(con):
    con.execute("INSERT INTO locations (id, parent_id, name) VALUES (1, NULL, 'Garage'), (2, 1, 'Top shelf'), (3, 2, 'Blue bin')")
    con.execute("DELETE FROM locations WHERE id = 1")
    assert con.execute("SELECT COUNT(*) FROM locations").fetchone()[0] == 0


# ---------------------------------------------------------------- version 4: api tokens and agent notes
def _v3_database(path):
    """A database exactly as version 3 left it: an ordered gift with its order fields."""
    c = db.connect(str(path))
    for stmt in db._statements(db.SCHEMA):
        c.execute(stmt)
    db.MIGRATIONS[2](c)
    db.MIGRATIONS[3](c)
    c.execute("INSERT INTO meta VALUES ('schema_version', '3')")
    c.execute("INSERT INTO people (id, name) VALUES (1, 'Jordan')")
    c.execute("INSERT INTO occasions (id, name, kind, date) VALUES (1, 'Christmas 2026', 'christmas', '2026-12-25')")
    c.execute("INSERT INTO gifts (id, occasion_id, title, status, seller, ship_to_id, eta, ordered_by, created_by, "
              "created_at, updated_at) VALUES (1, 1, 'Card', 'ordered', 'Shop', 1, '2026-12-09', 'Alex', 'a', 0, 0)")
    c.execute("INSERT INTO gift_recipients (gift_id, person_id) VALUES (1, 1)")
    return c


def test_a_v3_database_migrates_to_v4_keeping_its_rows(tmp_path):
    c = _v3_database(tmp_path / "t.db")
    db.migrate(c)
    assert c.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()[0] == str(db.SCHEMA_VERSION)
    assert db.SCHEMA_VERSION >= 4
    assert tuple(c.execute("SELECT title, status, seller, ship_to_id, eta, ordered_by FROM gifts").fetchone()) == \
        ("Card", "ordered", "Shop", 1, "2026-12-09", "Alex")
    assert [tuple(r) for r in c.execute("SELECT label, is_home FROM addresses")] == [("Home", 1)]
    assert c.execute("SELECT COUNT(*) FROM api_tokens").fetchone()[0] == 0
    assert c.execute("SELECT COUNT(*) FROM gift_agent_log").fetchone()[0] == 0
    db.migrate(c)  # again: nothing changes
    assert c.execute("SELECT COUNT(*) FROM meta WHERE key='schema_version'").fetchone()[0] == 1
    assert c.execute("SELECT COUNT(*) FROM gifts").fetchone()[0] == 1
    c.close()


def test_a_fresh_database_has_the_v4_tables(con):
    assert [r[0] for r in con.execute("SELECT name FROM pragma_table_info('api_tokens')")] == \
        ["id", "name", "scope", "token_hash", "unlocked", "created_at", "last_used_at", "revoked_at"]
    assert [r[0] for r in con.execute("SELECT name FROM pragma_table_info('gift_agent_log')")] == \
        ["id", "gift_id", "token_id", "at", "action"]
    cols = {name: (type_, notnull, default) for name, type_, notnull, default in _shape(con, "api_tokens")}
    assert cols["unlocked"] == ("INTEGER", 1, "0")
    assert cols["last_used_at"] == ("INTEGER", 0, None) and cols["revoked_at"] == ("INTEGER", 0, None)


def test_the_migrated_v4_tables_match_a_fresh_database(tmp_path, con):
    c = _v3_database(tmp_path / "t.db")
    db.migrate(c)
    for table in ("api_tokens", "gift_agent_log"):
        assert _shape(c, table) == _shape(con, table), table
    c.close()


def _token(con, id_, name, scope="delivery", token_hash=None):
    con.execute("INSERT INTO api_tokens (id, name, scope, token_hash, created_at) VALUES (?, ?, ?, ?, 0)",
                (id_, name, scope, token_hash or f"hash-{id_}"))


def test_token_names_hashes_and_scopes_are_constrained(con):
    _token(con, 1, "Shipping")
    with pytest.raises(sqlite3.IntegrityError):
        _token(con, 2, "Shipping")                       # a name is taken for good
    with pytest.raises(sqlite3.IntegrityError):
        _token(con, 3, "Other", token_hash="hash-1")     # a hash is unique
    with pytest.raises(sqlite3.IntegrityError):
        _token(con, 4, "Admin", scope="admin")
    assert [tuple(r) for r in con.execute("SELECT id, name, scope, unlocked FROM api_tokens")] == [(1, "Shipping", "delivery", 0)]


def test_deleting_a_gift_deletes_its_agent_notes(con):
    _token(con, 1, "Shipping")
    _gift_in(con, None)
    con.execute("INSERT INTO gift_agent_log (gift_id, token_id, at, action) VALUES (1, 1, 0, 'status: arrived')")
    with pytest.raises(sqlite3.IntegrityError):
        con.execute("INSERT INTO gift_agent_log (gift_id, token_id, at, action) VALUES (1, 99, 0, 'x')")
    con.execute("DELETE FROM gifts WHERE id = 1")
    assert con.execute("SELECT COUNT(*) FROM gift_agent_log").fetchone()[0] == 0
    assert con.execute("SELECT COUNT(*) FROM api_tokens").fetchone()[0] == 1


# ---------------------------------------------------------------- version 5: gift exchanges
def _v4_database(path):
    """A database exactly as version 4 left it: an ordered gift."""
    c = db.connect(str(path))
    for stmt in db._statements(db.SCHEMA):
        c.execute(stmt)
    for version in (2, 3, 4):
        db.MIGRATIONS[version](c)
    c.execute("INSERT INTO meta VALUES ('schema_version', '4')")
    c.execute("INSERT INTO people (id, name) VALUES (1, 'Jordan')")
    c.execute("INSERT INTO occasions (id, name, kind, date) VALUES (1, 'Christmas 2026', 'christmas', '2026-12-25')")
    c.execute("INSERT INTO gifts (id, occasion_id, title, status, seller, created_by, created_at, updated_at) "
              "VALUES (1, 1, 'Card', 'ordered', 'Shop', 'a', 0, 0)")
    c.execute("INSERT INTO gift_recipients (gift_id, person_id) VALUES (1, 1)")
    return c


def test_a_v4_database_migrates_to_v5_keeping_its_rows(tmp_path):
    c = _v4_database(tmp_path / "t.db")
    db.migrate(c)
    assert db.SCHEMA_VERSION >= 5
    assert c.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()[0] == str(db.SCHEMA_VERSION)
    assert tuple(c.execute("SELECT title, status, seller, exchange_id FROM gifts").fetchone()) == ("Card", "ordered", "Shop", None)
    assert [tuple(r) for r in c.execute("SELECT gift_id, person_id FROM gift_recipients")] == [(1, 1)]
    assert c.execute("SELECT COUNT(*) FROM exchanges").fetchone()[0] == 0
    assert c.execute("SELECT COUNT(*) FROM exchange_draws").fetchone()[0] == 0
    db.migrate(c)  # again: nothing changes
    assert c.execute("SELECT COUNT(*) FROM meta WHERE key='schema_version'").fetchone()[0] == 1
    assert c.execute("SELECT COUNT(*) FROM gifts").fetchone()[0] == 1
    c.close()


def test_a_fresh_database_has_the_v5_tables(con):
    assert [r[0] for r in con.execute("SELECT name FROM pragma_table_info('exchanges')")] == \
        ["id", "occasion_id", "name", "budget_cents", "date"]
    assert [r[0] for r in con.execute("SELECT name FROM pragma_table_info('exchange_draws')")] == \
        ["exchange_id", "drawer_id", "recipient_id"]
    cols = {name: (type_, notnull, default) for name, type_, notnull, default in _shape(con, "gifts")}
    assert cols["exchange_id"] == ("INTEGER", 0, None)


def test_the_v5_migrated_tables_match_a_fresh_database(tmp_path, con):
    c = _v4_database(tmp_path / "t.db")
    db.migrate(c)
    for table in ("gifts", "exchanges", "exchange_draws"):
        assert _shape(c, table) == _shape(con, table), table
    c.close()


def _exchange_world(con):
    con.execute("INSERT INTO people (id, name) VALUES (101, 'Casey'), (102, 'Jordan')")
    con.execute("INSERT INTO occasions (id, name, kind, date) VALUES (1, 'X', 'christmas', '2026-12-25')")
    con.execute("INSERT INTO exchanges (id, occasion_id, name, budget_cents) VALUES (1, 1, 'Secret Santa', 2500)")
    con.execute("INSERT INTO exchange_draws (exchange_id, drawer_id, recipient_id) VALUES (1, 101, 102)")


def test_a_drawer_has_one_draw_per_exchange(con):
    _exchange_world(con)
    with pytest.raises(sqlite3.IntegrityError):
        con.execute("INSERT INTO exchange_draws (exchange_id, drawer_id, recipient_id) VALUES (1, 101, 102)")


def test_deleting_an_exchange_deletes_its_draws_but_not_while_a_gift_uses_it(con):
    _exchange_world(con)
    con.execute("INSERT INTO gifts (id, occasion_id, title, status, exchange_id, created_by, created_at, updated_at) "
                "VALUES (1, 1, 'Mug', 'idea', 1, 'a', 0, 0)")
    with pytest.raises(sqlite3.IntegrityError):
        con.execute("DELETE FROM exchanges WHERE id = 1")
    con.execute("DELETE FROM gifts WHERE id = 1")
    con.execute("DELETE FROM exchanges WHERE id = 1")
    assert con.execute("SELECT COUNT(*) FROM exchange_draws").fetchone()[0] == 0


def test_deleting_an_occasion_deletes_its_exchanges_draws_and_gifts(con):
    _exchange_world(con)
    con.execute("INSERT INTO gifts (id, occasion_id, title, status, exchange_id, created_by, created_at, updated_at) "
                "VALUES (1, 1, 'Mug', 'idea', 1, 'a', 0, 0)")
    con.execute("DELETE FROM occasions WHERE id = 1")
    for table in ("exchanges", "exchange_draws", "gifts"):
        assert con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0, table
