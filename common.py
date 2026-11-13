"""Request-body helpers and the error type that routes and modules raise. No routes here."""
import re
import time
from datetime import date

from flask import request

import settings


class ApiError(Exception):
    """Raised anywhere while handling a request; app.py turns it into {"error": message} with this status."""

    def __init__(self, status: int, message: str, **extra):
        super().__init__(message)
        self.status = status
        self.message = message
        self.extra = extra  # more keys for the JSON body, e.g. locked=True


def is_school() -> bool:
    """Whether this instance runs in school mode. Read from settings at call time, so a test can switch it."""
    return settings.MODE == "school"


def locked() -> ApiError:
    """The idle lock is closed: 423 {"error": "Locked", "locked": true}. The UI shows the PIN pad on it."""
    return ApiError(423, "Locked", locked=True)


def json_body() -> dict:
    """The request's JSON object. Anything else (no body, bad JSON, a list, a string) is 400."""
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        raise ApiError(400, "Expected a JSON object.")
    return data


def text(data: dict, key: str) -> str:
    """data[key] stripped; "" when missing or null; 400 when it is not a string."""
    value = data.get(key)
    if value is None:
        return ""
    if not isinstance(value, str):
        raise ApiError(400, f"{key} must be text.")
    return value.strip()


def flag(data: dict, key: str, default: bool | None = None) -> bool | None:
    """data[key] as a bool; `default` when missing or null; 400 when it is anything but true or false."""
    value = data.get(key)
    if value is None:
        return default
    if not isinstance(value, bool):
        raise ApiError(400, f"{key} must be true or false.")
    return value


def whole(data: dict, key: str, default: int | None = None) -> int | None:
    """data[key] as a whole number; `default` when missing or null; 400 for anything else (booleans included)."""
    value = data.get(key)
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, int):
        raise ApiError(400, f"{key} must be a whole number.")
    return value


def now() -> int:
    """Seconds since the epoch. Every module asks here, so tests can move time (the `clock` fixture)."""
    return int(time.time())


def today() -> str:
    """Today as YYYY-MM-DD. Every module asks here, so tests can freeze it (the `freeze_today` fixture)."""
    return date.today().isoformat()


MAX_CENTS = 10_000_000  # $100,000: well beyond any gift, and keeps sums far from overflow
_MONEY = re.compile(r"\$?((?:[0-9]{1,3}(?:,[0-9]{3})+|[0-9]+))(?:\.([0-9]{1,2}))?")


def parse_cents(value) -> int | None:
    """Money as integer cents, never through a float. Accepts a whole number of cents (int) or text such as
    "29", "29.9", "$29.99", "1,299.00". Empty text and None give None. Negative, non-numeric, more than two
    decimals, a float or a boolean, or more than MAX_CENTS is 400."""
    if value is None:
        return None
    if isinstance(value, bool) or isinstance(value, float):
        raise ApiError(400, "Price must be an amount such as 29.99.")
    if isinstance(value, int):
        cents = value
    elif isinstance(value, str):
        raw = value.strip()
        if not raw:
            return None
        m = _MONEY.fullmatch(raw)
        if not m:
            raise ApiError(400, "Price must be an amount such as 29.99.")
        dollars, frac = m.group(1).replace(",", ""), (m.group(2) or "").ljust(2, "0")
        cents = int(dollars) * 100 + int(frac)
    else:
        raise ApiError(400, "Price must be an amount such as 29.99.")
    if not 0 <= cents <= MAX_CENTS:
        raise ApiError(400, "Price is out of range.")
    return cents
