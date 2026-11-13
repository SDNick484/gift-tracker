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
