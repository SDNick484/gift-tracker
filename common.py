"""Request-body helpers and the error type that routes and modules raise. No routes here."""
from datetime import date

from flask import request


class ApiError(Exception):
    """Raised anywhere while handling a request; app.py turns it into {"error": message} with this status."""

    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


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


def today() -> str:
    """Today as YYYY-MM-DD. Every module asks here, so tests can freeze it (the `freeze_today` fixture)."""
    return date.today().isoformat()
