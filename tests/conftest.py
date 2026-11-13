"""Test setup. settings.py reads the environment once at import, so set it before importing the app,
and give every test a fresh database."""
import os
import shutil
import sys
import tempfile

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

import app as tracker  # noqa: E402
import db  # noqa: E402
import settings  # noqa: E402


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
