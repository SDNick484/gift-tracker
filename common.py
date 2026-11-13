"""Request-body helpers and the error type that routes and modules raise. No routes here."""
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
