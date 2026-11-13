"""Configuration, read once from the environment at import. Nothing here has a household value as its default.

  DATA_DIR           where tracker.db lives (default ./data)
  TRACKER_PORT       port for `python3 app.py` and the installer's gunicorn bind (default 8767)
  ALLOWED_EMAILS     comma list: the only Cloudflare Access emails admitted
  USERS              email=Display name pairs, comma separated: "a@example.com=Alex,b@example.com=Sam"
  CF_TEAM_DOMAIN     e.g. myteam.cloudflareaccess.com (a pasted https:// prefix or trailing / is tolerated)
  CF_AUD             Application Audience (AUD) tag of the tracker's own Access application
  DEV_EMAIL          development only: admit loopback requests without Cloudflare headers as this email
  APP_NAME           page title and home-screen name (default Ledger)
  LOCK_IDLE_SECONDS  idle time before the protected section locks (default 300)
  MODE               family (default) or school: school makes this instance the staff-gift tracker, with no lock,
                     no Santa, no protected people and no boards (see common.is_school)
"""
import os
import re

BASE = os.path.dirname(os.path.abspath(__file__))


def _emails(raw):
    return {e.strip().lower() for e in (raw or "").split(",") if e.strip()}


def _users(raw):
    """'a@example.com=Alex, b@example.com=Sam' -> {'a@example.com': 'Alex', 'b@example.com': 'Sam'}"""
    out = {}
    for pair in (raw or "").split(","):
        email, sep, name = pair.partition("=")
        email, name = email.strip().lower(), name.strip()
        if not pair.strip():
            continue
        if not (sep and email and name):
            raise ValueError("USERS must look like a@example.com=Alex,b@example.com=Sam")
        out[email] = name
    return out


def _int(name, default, minimum):
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    if not re.fullmatch(r"\d+", raw) or int(raw) < minimum:
        raise ValueError(f"{name} must be a whole number of at least {minimum}")
    return int(raw)


DATA_DIR = os.environ.get("DATA_DIR", "").strip() or os.path.join(BASE, "data")
DB_PATH = os.path.join(DATA_DIR, "tracker.db")
PORT = _int("TRACKER_PORT", 8767, 1)
ALLOWED_EMAILS: set[str] = _emails(os.environ.get("ALLOWED_EMAILS"))
USERS: dict[str, str] = _users(os.environ.get("USERS"))
CF_TEAM_DOMAIN = re.sub(r"^https?://", "", os.environ.get("CF_TEAM_DOMAIN", "").strip()).split("/")[0]
CF_AUD = os.environ.get("CF_AUD", "").strip()
DEV_EMAIL = os.environ.get("DEV_EMAIL", "").strip().lower()
APP_NAME = os.environ.get("APP_NAME", "").strip() or "Ledger"
LOCK_IDLE_SECONDS: int = _int("LOCK_IDLE_SECONDS", 300, 1)

MODES = ("family", "school")
MODE = os.environ.get("MODE", "").strip().lower() or "family"
if MODE not in MODES:
    raise ValueError(f"MODE must be one of {', '.join(MODES)}")
