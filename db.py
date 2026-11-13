"""The SQLite schema, connections and migrate().

Connections run in autocommit mode (isolation_level=None): a single statement commits on its own, and anything
that must be atomic runs inside `with transaction(con):`. Every schema change adds a step to MIGRATIONS and bumps
SCHEMA_VERSION; migrate() is idempotent and runs at every start, so the host never needs a manual DB step."""
import sqlite3
from contextlib import contextmanager

SCHEMA_VERSION = 4

# Version 1. Statements are separated by ";" and contain none themselves (no triggers): see _statements().
SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS groups (id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE, sort_order INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS people (
  id INTEGER PRIMARY KEY, name TEXT NOT NULL, group_id INTEGER REFERENCES groups(id) ON DELETE SET NULL,
  household TEXT, color TEXT, protected INTEGER NOT NULL DEFAULT 0, active INTEGER NOT NULL DEFAULT 1,
  wishlist_person_id INTEGER, notes TEXT, sort_order INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS users (
  email TEXT PRIMARY KEY, person_id INTEGER NOT NULL REFERENCES people(id),
  pin_hash BLOB, pin_salt BLOB, pin_fails INTEGER NOT NULL DEFAULT 0, pin_locked_until INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS occasions (
  id INTEGER PRIMARY KEY, name TEXT NOT NULL, kind TEXT NOT NULL CHECK (kind IN ('christmas','birthday','graduation','other')),
  date TEXT NOT NULL, archived INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS occasion_people (
  occasion_id INTEGER NOT NULL REFERENCES occasions(id) ON DELETE CASCADE,
  person_id INTEGER NOT NULL REFERENCES people(id) ON DELETE CASCADE,
  plan TEXT NOT NULL DEFAULT 'individual' CHECK (plan IN ('individual','joint','exchange','skip')),
  budget_cents INTEGER, PRIMARY KEY (occasion_id, person_id));
CREATE TABLE IF NOT EXISTS gifts (
  id INTEGER PRIMARY KEY, occasion_id INTEGER NOT NULL REFERENCES occasions(id) ON DELETE CASCADE,
  title TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('idea','decided','ordered','arrived','wrapped','given','told','confirmed')),
  santa INTEGER NOT NULL DEFAULT 0, url TEXT, store TEXT, price_cents INTEGER, notes TEXT,
  wishlist_item_id INTEGER, told_at TEXT, created_by TEXT NOT NULL, created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS gift_recipients (gift_id INTEGER NOT NULL REFERENCES gifts(id) ON DELETE CASCADE,
  person_id INTEGER NOT NULL REFERENCES people(id), PRIMARY KEY (gift_id, person_id));
CREATE TABLE IF NOT EXISTS gift_givers (gift_id INTEGER NOT NULL REFERENCES gifts(id) ON DELETE CASCADE,
  person_id INTEGER NOT NULL REFERENCES people(id), PRIMARY KEY (gift_id, person_id));
CREATE INDEX IF NOT EXISTS gifts_occasion ON gifts(occasion_id);
CREATE INDEX IF NOT EXISTS gift_recipients_person ON gift_recipients(person_id);
"""


def _statements(script):
    # Not executescript(): it commits any open transaction first, which would split a migration in two.
    return [s.strip() for s in script.split(";") if s.strip()]


def connect(path):
    con = sqlite3.connect(path, timeout=10, isolation_level=None)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    con.execute("PRAGMA busy_timeout = 10000")
    con.execute("PRAGMA journal_mode = WAL")
    return con


@contextmanager
def transaction(con, mode="IMMEDIATE"):
    """BEGIN IMMEDIATE ... COMMIT, or ROLLBACK if the block raises."""
    con.execute(f"BEGIN {mode}")
    try:
        yield con
    except BaseException:
        con.execute("ROLLBACK")
        raise
    con.execute("COMMIT")


def _version(con):
    if not con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='meta'").fetchone():
        return 0
    row = con.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
    return int(row[0]) if row else 0


def _to_v1(con):
    for stmt in _statements(SCHEMA):
        con.execute(stmt)


MIGRATIONS = {1: _to_v1}  # version -> step that brings version-1 up to it


# Version 2 (school mode): a staff role, and a detail and a done flag on each recipient of a group gift. Columns
# only, so existing rows keep their data and take the defaults; family instances simply never fill them.
def _to_v2(con):
    con.execute("ALTER TABLE people ADD COLUMN role TEXT")
    con.execute("ALTER TABLE gift_recipients ADD COLUMN detail TEXT")
    con.execute("ALTER TABLE gift_recipients ADD COLUMN done INTEGER NOT NULL DEFAULT 0")


MIGRATIONS[2] = _to_v2


# Version 3 (after purchase): the location tree for hiding places, the ship-to addresses (exactly one is home: a
# partial unique index allows at most one, and the migration seeds it), and the order fields and hiding place on a
# gift. Columns only on gifts, so existing rows keep their data and take the defaults. A column with a REFERENCES
# clause must default to NULL, which these do.
def _to_v3(con):
    con.execute("CREATE TABLE locations (id INTEGER PRIMARY KEY, parent_id INTEGER REFERENCES locations(id) "
                "ON DELETE CASCADE, name TEXT NOT NULL, sort_order INTEGER NOT NULL DEFAULT 0)")
    con.execute("CREATE INDEX locations_parent ON locations(parent_id)")
    con.execute("CREATE TABLE addresses (id INTEGER PRIMARY KEY, label TEXT NOT NULL, is_home INTEGER NOT NULL DEFAULT 0)")
    con.execute("CREATE UNIQUE INDEX addresses_one_home ON addresses(is_home) WHERE is_home = 1")
    con.execute("INSERT INTO addresses (label, is_home) VALUES ('Home', 1)")
    for column in ("seller TEXT", "order_number TEXT", "ship_to_id INTEGER REFERENCES addresses(id)",
                   "ship_elsewhere_ok INTEGER NOT NULL DEFAULT 0", "eta TEXT", "arrived_on TEXT", "ordered_by TEXT",
                   "location_id INTEGER REFERENCES locations(id) ON DELETE SET NULL"):
        con.execute(f"ALTER TABLE gifts ADD COLUMN {column}")


MIGRATIONS[3] = _to_v3


# Version 4 (agent API): scoped API tokens, of which only a SHA-256 hash is stored, and the note every write by a token
# leaves on the gift. A token's name stays taken after it is revoked, so the notes never name two tokens alike.
# `unlocked` marks a `full` token made to see protected gifts (agent.sees_protected).
def _to_v4(con):
    con.execute("CREATE TABLE api_tokens (id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE, "
                "scope TEXT NOT NULL CHECK (scope IN ('delivery','shop','full')), token_hash TEXT NOT NULL UNIQUE, "
                "unlocked INTEGER NOT NULL DEFAULT 0, created_at INTEGER NOT NULL, last_used_at INTEGER, revoked_at INTEGER)")
    con.execute("CREATE TABLE gift_agent_log (id INTEGER PRIMARY KEY, "
                "gift_id INTEGER NOT NULL REFERENCES gifts(id) ON DELETE CASCADE, "
                "token_id INTEGER NOT NULL REFERENCES api_tokens(id), at INTEGER NOT NULL, action TEXT NOT NULL)")
    con.execute("CREATE INDEX gift_agent_log_gift ON gift_agent_log(gift_id)")


MIGRATIONS[4] = _to_v4


def migrate(con):
    """Bring the database up to SCHEMA_VERSION in one transaction. Safe to run any number of times, and from
    several processes at once (BEGIN IMMEDIATE serializes them)."""
    with transaction(con):
        current = _version(con)
        if current > SCHEMA_VERSION:
            raise RuntimeError(f"database schema {current} is newer than this code ({SCHEMA_VERSION})")
        for version in range(current + 1, SCHEMA_VERSION + 1):
            MIGRATIONS[version](con)
        con.execute("INSERT INTO meta (key, value) VALUES ('schema_version', ?) "
                    "ON CONFLICT(key) DO UPDATE SET value = excluded.value", (str(SCHEMA_VERSION),))
