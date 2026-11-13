"""Test setup. settings.py reads the environment once at import, so set it before importing the app,
and give every test a fresh database."""
import os
import shutil
import sys
import tempfile
import time
import types

import pytest

DATA = tempfile.mkdtemp(prefix="gift-tracker-test-")
os.environ.update(
    DATA_DIR=DATA,
    ALLOWED_EMAILS="alex@example.com,sam@example.com",
    USERS="alex@example.com=Alex,sam@example.com=Sam",
    CF_TEAM_DOMAIN="family.cloudflareaccess.com",
    CF_AUD="aud-test",
    DEV_EMAIL="",  # never inherit a developer's shell setting: it would admit loopback requests
)
for name in ("TRACKER_PORT", "APP_NAME", "LOCK_IDLE_SECONDS"):
    os.environ.pop(name, None)  # test the defaults
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import jwt  # noqa: E402
from cryptography.hazmat.primitives.asymmetric import rsa  # noqa: E402

import app as tracker  # noqa: E402
import db  # noqa: E402
import identity  # noqa: E402
import settings  # noqa: E402

KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
OTHER_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)

# Stand in for Cloudflare's JWKS endpoint: always hand back the test public key.
identity._jwk_client = types.SimpleNamespace(
    get_signing_key_from_jwt=lambda _t: types.SimpleNamespace(key=KEY.public_key()))

LAN_IP = "192.0.2.50"     # RFC 5737
ORIGIN = "http://localhost"  # the Flask test client's host


def token(email, key=KEY, aud="aud-test", iss="https://family.cloudflareaccess.com", exp=3600, **claims):
    """A Cloudflare Access JWT. exp is seconds from now; exp=None leaves the claim out."""
    payload = {"email": email, "aud": aud, "iss": iss, "iat": int(time.time()), **claims}
    if exp is not None:
        payload["exp"] = int(time.time()) + exp
    return jwt.encode(payload, key, algorithm="RS256")


def auth(email="alex@example.com", **kw):
    """Headers of a request that came through Cloudflare Access signed in as `email`."""
    return {"Cf-Ray": "test", "Cf-Access-Authenticated-User-Email": email,
            "Cf-Access-Jwt-Assertion": token(email, **kw)}


def mut(email="alex@example.com", **kw):
    """auth() plus what every mutation needs: the CSRF header, a same-origin Origin and a JSON content type."""
    return {**auth(email, **kw), "X-Tracker": "1", "Origin": ORIGIN, "Content-Type": "application/json"}


@pytest.fixture(autouse=True)
def fresh_db():
    """Every test starts from an empty, migrated database."""
    for name in os.listdir(DATA):
        path = os.path.join(DATA, name)
        shutil.rmtree(path) if os.path.isdir(path) else os.remove(path)
    tracker.init_db()


@pytest.fixture
def client():
    return tracker.app.test_client()


@pytest.fixture
def con():
    c = db.connect(settings.DB_PATH)
    yield c
    c.close()


# ---------------------------------------------------------------- helpers that drive the API
# Plain functions, imported by the tests that need them (`from conftest import mk_person`).
def _ok(r, status):
    assert r.status_code == status, r.get_json()
    return r.get_json()


def mk_person(client, name, **fields):
    """Create a person through the API; returns the id."""
    return _ok(client.post("/api/people", json={"name": name, **fields}, headers=mut()), 201)["id"]


def mk_occasion(client, name="Christmas 2026", kind="christmas", date="2026-12-25", **fields):
    return _ok(client.post("/api/occasions", json={"name": name, "kind": kind, "date": date, **fields},
                           headers=mut()), 201)["id"]


def put_plan(client, occasion_id, person_id, plan="individual", budget_cents=None):
    return _ok(client.put(f"/api/occasions/{occasion_id}/people/{person_id}",
                          json={"plan": plan, "budget_cents": budget_cents}, headers=mut()), 200)


def mk_gift(client, occasion_id, title, to, frm=(), **fields):
    """Create a gift through the API; `to` and `frm` are lists of person ids. Returns the id."""
    body = {"occasion_id": occasion_id, "title": title, "recipient_ids": list(to), "giver_ids": list(frm), **fields}
    return _ok(client.post("/api/gifts", json=body, headers=mut()), 201)["id"]


def con_rows(sql, *params):
    """Rows of a query against the test database, as plain tuples."""
    c = db.connect(settings.DB_PATH)
    try:
        return [tuple(r) for r in c.execute(sql, params)]
    finally:
        c.close()
