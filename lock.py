"""The idle lock: a PIN per user, and a short-lived signed cookie that says this browser entered it recently.

While a request is locked, every gift with a protected recipient is hidden (gifts.PROTECTED_SQL, gifts.visible)
and no response carries a Santa flag. Cloudflare Access keeps strangers out; this covers a session left open on a
shared device. A user with no PIN is always locked.

- PIN: 4 to 8 ASCII digits, stored as scrypt (n=2**14, r=8, p=1) with a 16-byte salt per user.
- Five wrong PINs in a row lock unlocking out for 60 seconds (429), and while the count stays at five or more each
  further wrong PIN starts another 60 seconds. A correct PIN resets the count. Wrong `current` PINs when changing a
  PIN count too, so that route is no way around the limit.
- Cookie `gt_unlock`: itsdangerous.URLSafeTimedSerializer over {"e": email}, keyed by 32 random bytes in
  DATA_DIR/secret.key (mode 0600, made on first start). It is valid for this email only, for LOCK_IDLE_SECONDS
  after it was issued, and app.py re-issues it on every unlocked response, so the window slides with use.
Route-free: app.py holds the routes and decides when to issue or clear the cookie."""
import hashlib
import hmac
import os
import re
import secrets

from itsdangerous import BadSignature, TimestampSigner, URLSafeTimedSerializer

import common
import db
import settings
from common import ApiError

COOKIE = "gt_unlock"
SALT = "gt-unlock"          # itsdangerous salt: separates this use of the secret from any later one
PIN = re.compile(r"[0-9]{4,8}")
MAX_FAILS = 5
LOCKOUT_SECONDS = 60
SECRET_BYTES = 32
SALT_BYTES = 16

_secret: bytes | None = None


# ---------------------------------------------------------------- the secret
def load_secret(data_dir: str) -> None:
    """Read DATA_DIR/secret.key, creating it first if missing. Creation writes a private temporary file and links
    it into place, so gunicorn workers starting together all end up with the same secret."""
    global _secret
    path = os.path.join(data_dir, "secret.key")
    if not os.path.exists(path):
        tmp = f"{path}.{os.getpid()}.{secrets.token_hex(4)}"
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(secrets.token_bytes(SECRET_BYTES))
                f.flush()
                os.fsync(f.fileno())
            try:
                os.link(tmp, path)
            except FileExistsError:
                pass  # another worker won; use its secret
        finally:
            os.unlink(tmp)
    with open(path, "rb") as f:
        data = f.read()
    if len(data) < SECRET_BYTES:
        raise RuntimeError(f"{path} is shorter than {SECRET_BYTES} bytes; delete it to make a new one")
    _secret = data


class _Signer(TimestampSigner):
    def get_timestamp(self) -> int:
        return common.now()


def _serializer() -> URLSafeTimedSerializer:
    if _secret is None:
        raise RuntimeError("lock.load_secret() has not run")
    return URLSafeTimedSerializer(_secret, salt=SALT, signer=_Signer)


# ---------------------------------------------------------------- the cookie
def issue(email: str) -> str:
    return _serializer().dumps({"e": email})


def is_unlocked(request, email: str) -> bool:
    """True when the request's gt_unlock cookie verifies, names this email, and is at most LOCK_IDLE_SECONDS old
    (and not from the future)."""
    token = request.cookies.get(COOKIE)
    if not token:
        return False
    try:
        data = _serializer().loads(token, max_age=settings.LOCK_IDLE_SECONDS)
    except BadSignature:  # includes SignatureExpired
        return False
    return isinstance(data, dict) and data.get("e") == email


def set_cookie(resp, email: str) -> None:
    resp.set_cookie(COOKIE, issue(email), max_age=settings.LOCK_IDLE_SECONDS, path="/", secure=True,
                    httponly=True, samesite="Strict")


def clear_cookie(resp) -> None:
    resp.delete_cookie(COOKIE, path="/", secure=True, httponly=True, samesite="Strict")


# ---------------------------------------------------------------- the PIN
def _hash(pin: str, salt: bytes) -> bytes:
    return hashlib.scrypt(pin.encode(), salt=salt, n=2**14, r=8, p=1)


def valid_pin(pin) -> bool:
    return isinstance(pin, str) and PIN.fullmatch(pin) is not None


def has_pin(con, email: str) -> bool:
    row = con.execute("SELECT pin_hash FROM users WHERE email = ?", (email,)).fetchone()
    return row is not None and row["pin_hash"] is not None


def set_pin(con, email: str, pin: str) -> None:
    """Store a new PIN and clear the failure count. Checking `current` is the caller's job (check_pin)."""
    if not valid_pin(pin):
        raise ApiError(400, "A PIN is 4 to 8 digits.")
    salt = os.urandom(SALT_BYTES)
    if not con.execute("UPDATE users SET pin_hash = ?, pin_salt = ?, pin_fails = 0, pin_locked_until = 0 "
                       "WHERE email = ?", (_hash(pin, salt), salt, email)).rowcount:
        raise ApiError(409, "This account is not set up as a user of the app.")


def check_pin(con, email: str, pin) -> bool:
    """Whether `pin` is this user's PIN, counting failures. Raises 429 while locked out, even for the right PIN.
    False for a user with no PIN (callers answer that before asking). One transaction, so parallel guesses cannot
    slip past the count."""
    now = common.now()
    with db.transaction(con):
        row = con.execute("SELECT pin_hash, pin_salt, pin_fails, pin_locked_until FROM users WHERE email = ?",
                          (email,)).fetchone()
        if row is None or row["pin_hash"] is None:
            return False
        if row["pin_locked_until"] > now:
            raise ApiError(429, "Too many wrong PINs. Try again in a minute.")
        ok = valid_pin(pin) and hmac.compare_digest(_hash(pin, row["pin_salt"]), row["pin_hash"])
        if ok:
            con.execute("UPDATE users SET pin_fails = 0, pin_locked_until = 0 WHERE email = ?", (email,))
        else:
            fails = row["pin_fails"] + 1
            until = now + LOCKOUT_SECONDS if fails >= MAX_FAILS else row["pin_locked_until"]
            con.execute("UPDATE users SET pin_fails = ?, pin_locked_until = ? WHERE email = ?", (fails, until, email))
    return ok
