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
  WISHLIST_URL       the Family Wishlist's base URL on the LAN, e.g. http://192.0.2.10:8090 (http or https; a path
                     prefix is kept). Unset: the wishlist link is off (wishlist.py)
  WISHLIST_TOKEN     the wishlist's FEED_TOKEN, sent as a bearer token; required when WISHLIST_URL is set
"""
import os
import re
from urllib.parse import urlsplit

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


# ---- the wishlist link (wishlist.py). Checked here so a bad value stops the app at start-up, before it serves anything.
_PRINTABLE = re.compile(r"[\x21-\x7e]+")  # visible ASCII: no space, no control character (so no header injection)
_URL_PATH = re.compile(r"[A-Za-z0-9._~/-]*")


def wishlist_url(raw):
    """'' for unset; otherwise scheme://host[:port][/prefix] with no trailing slash. Only http and https, a host, no
    user name or password (they would end up in logs), no query and no fragment; anything else is a ValueError."""
    raw = (raw or "").strip()
    if not raw:
        return ""
    bad = ValueError("WISHLIST_URL must be an http:// or https:// address such as http://192.0.2.10:8090 "
                     "(an optional path, no user name, query or fragment)")
    if not _PRINTABLE.fullmatch(raw) or "?" in raw or "#" in raw or "@" in raw:
        raise bad
    u = urlsplit(raw)
    try:
        _ = u.port  # raises ValueError for a port that is not a number in range
    except ValueError:
        raise bad from None
    if u.scheme not in ("http", "https") or not u.hostname or not _URL_PATH.fullmatch(u.path):
        raise bad
    return f"{u.scheme}://{u.netloc}{u.path.rstrip('/')}"


WISHLIST_URL = wishlist_url(os.environ.get("WISHLIST_URL"))
WISHLIST_TOKEN = os.environ.get("WISHLIST_TOKEN", "").strip()
if WISHLIST_URL and not _PRINTABLE.fullmatch(WISHLIST_TOKEN):
    raise ValueError("WISHLIST_TOKEN must be set when WISHLIST_URL is set (the wishlist's FEED_TOKEN: visible characters, no spaces)")
