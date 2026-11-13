#!/usr/bin/env python3
"""Gift Tracker MCP server: one tool per agent API operation (/api/v1), called over REST with one agent token.

It never opens the database: everything goes through the tracker's API, so the token's scope and the lock decide what
an agent sees, exactly as for any other client. The tools registered are the ones the token's scope may use
(SCOPE_TOOLS), worked out from GET /api/v1/me at start.

  TRACKER_URL=http://127.0.0.1:8767 TRACKER_API_TOKEN=gt_... python mcp_server.py     stdio, for a local MCP client
  mcp_http.py                                                                          Streamable HTTP (the service)

The mcp package is only in the MCP service's own venv (requirements-mcp.txt); this module imports without it, so
TrackerClient is usable and testable on its own."""
import json
import logging
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from typing import Any

try:
    from mcp.server.mcpserver import MCPServer
    from mcp.server.mcpserver.exceptions import ToolError
    from mcp.types import ToolAnnotations
except ImportError:  # the Flask app's venv: the client works, build_server() returns None
    MCPServer = None
    ToolError = Exception
    ToolAnnotations = None

log = logging.getLogger("tracker.mcp")

TRACKER_URL = os.environ.get("TRACKER_URL", "").strip().rstrip("/") or "http://127.0.0.1:8767"
TRACKER_API_TOKEN = os.environ.get("TRACKER_API_TOKEN", "").strip()
TOKEN_SETTING = "TRACKER_API_TOKEN"
UNREACHABLE = "The tracker is unreachable right now. Try again in a minute."
LOOPBACK = {"127.0.0.1", "localhost", "::1"}

# (method, path, JSON body or None, headers) -> (HTTP status, raw body). Tests pass one backed by Flask's test client.
Transport = Callable[[str, str, Any, dict], tuple[int, bytes]]


class TrackerError(RuntimeError, ToolError):
    """Every failure of a tool. Its text reaches the model, so it never holds the token or an HTML page."""

    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


def urllib_transport(base_url: str, timeout: float = 15) -> Transport:
    """Plain urllib. The tracker normally listens on loopback, which must never go through a proxy."""
    host = urllib.parse.urlsplit(base_url).hostname or ""
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({})) if host in LOOPBACK else urllib.request.build_opener()

    def send(method: str, path: str, body: Any, headers: dict) -> tuple[int, bytes]:
        data = None if body is None else json.dumps(body).encode("utf-8")
        req = urllib.request.Request(base_url + path, data=data, headers=headers, method=method)
        try:
            with opener.open(req, timeout=timeout) as resp:
                return resp.status, resp.read()
        except urllib.error.HTTPError as e:
            return e.code, e.read()

    return send


class TrackerClient:
    """The agent API, one method per operation. Raises TrackerError with a sentence for the model."""

    def __init__(self, api_url: str | None = None, token: str | None = None, transport: Transport | None = None,
                 token_name: str = TOKEN_SETTING):
        self.api_url = (api_url or TRACKER_URL).rstrip("/")
        self.token = TRACKER_API_TOKEN if token is None else token
        self.token_name = token_name  # named in the 401 message, never the token itself
        self.transport = transport or urllib_transport(self.api_url)

    def _request(self, method: str, path: str, body: Any = None) -> Any:
        headers = {"Accept": "application/json", "User-Agent": "tracker-mcp/1.0", "Authorization": f"Bearer {self.token}"}
        if body is not None:
            headers["Content-Type"] = "application/json"
        try:
            status, raw = self.transport(method, path, body, headers)
        except Exception as e:  # noqa: BLE001 (any failure to talk to it means the same to the model)
            log.warning("tracker %s %s failed: %s", method, path.split("?")[0], type(e).__name__)
            raise TrackerError(UNREACHABLE) from None
        try:
            data = json.loads(raw) if raw else None
        except ValueError:
            data = None
        if status < 400:
            if data is None:
                raise TrackerError(f"Tracker API error ({status}): not JSON", status=status)
            return data
        log.warning("tracker %s %s -> HTTP %s", method, path.split("?")[0], status)
        if status == 401:
            raise TrackerError(f"The tracker rejected this service's token (HTTP 401). Check {self.token_name}.", status=401)
        detail = ""
        if isinstance(data, dict):
            if status == 403 and isinstance(data.get("scope"), str):
                raise TrackerError(f"This token's scope ({data['scope']}) does not allow that.", status=403)
            detail = str(data.get("error") or "")[:200]
        raise TrackerError(f"Tracker API error ({status})" + (f": {detail}" if detail else ""), status=status)

    @staticmethod
    def _query(**params) -> str:
        kept = {k: v for k, v in params.items() if v is not None and v != ""}
        return ("?" + urllib.parse.urlencode(kept)) if kept else ""

    # ---------------------------------------------------------------- reading
    def whoami(self) -> dict:
        return self._request("GET", "/api/v1/me")

    def list_occasions(self) -> list:
        return self._request("GET", "/api/v1/occasions")

    def list_addresses(self) -> list:
        return self._request("GET", "/api/v1/addresses")

    def list_people(self) -> list:
        return self._request("GET", "/api/v1/people")

    def list_gifts(self, occasion_id: int | None = None, status: str | None = None, person_id: int | None = None) -> list:
        return self._request("GET", "/api/v1/gifts" + self._query(occasion_id=occasion_id, status=status, person_id=person_id))

    def get_gift(self, gift_id: int) -> dict:
        return self._request("GET", f"/api/v1/gifts/{int(gift_id)}")

    def get_budgets(self, occasion_id: int) -> dict:
        return self._request("GET", f"/api/v1/occasions/{int(occasion_id)}/budgets")

    # ---------------------------------------------------------------- writing
    @staticmethod
    def _order(clear, **fields) -> dict:
        body = {k: v for k, v in fields.items() if v is not None}
        for name in clear or ():
            body[name] = None
        return body

    def update_order(self, gift_id: int, seller: str | None = None, order_number: str | None = None,
                     ship_to_id: int | None = None, ship_elsewhere_ok: bool | None = None, eta: str | None = None,
                     arrived_on: str | None = None, clear: list[str] | None = None) -> dict:
        body = self._order(clear, seller=seller, order_number=order_number, ship_to_id=ship_to_id,
                           ship_elsewhere_ok=ship_elsewhere_ok, eta=eta, arrived_on=arrived_on)
        return self._request("PATCH", f"/api/v1/gifts/{int(gift_id)}/order", body)

    def set_status(self, gift_id: int, status: str, seller: str | None = None, order_number: str | None = None,
                   ship_to_id: int | None = None, eta: str | None = None, arrived_on: str | None = None) -> dict:
        body = {"status": status, **self._order((), seller=seller, order_number=order_number, ship_to_id=ship_to_id,
                                                eta=eta, arrived_on=arrived_on)}
        return self._request("POST", f"/api/v1/gifts/{int(gift_id)}/status", body)

    def add_gift(self, title: str, recipient_ids: list[int], giver_ids: list[int] | None = None,
                 occasion_id: int | None = None, status: str | None = None, url: str | None = None,
                 store: str | None = None, price: str | None = None, notes: str | None = None) -> dict:
        body = {k: v for k, v in {"title": title, "recipient_ids": recipient_ids, "giver_ids": giver_ids,
                                   "occasion_id": occasion_id, "status": status, "url": url, "store": store,
                                   "price": price, "notes": notes}.items() if v is not None}
        return self._request("POST", "/api/v1/gifts", body)


_default_client = TrackerClient()


# ---------------------------------------------------------------- the tools (module level; they read _default_client)
def whoami() -> dict:
    """This connection's token: its name, its scope (delivery, shop or full) and whether it sees protected gifts."""
    return _default_client.whoami()


def list_occasions() -> list:
    """Occasions (Christmas 2026, a birthday, ...), newest first. `current` marks the one gifts default to."""
    return _default_client.list_occasions()


def list_addresses() -> list:
    """Ship-to addresses by label, never a street address. Exactly one is home; use its id as ship_to_id unless the
    order says it ships somewhere else."""
    return _default_client.list_addresses()


def list_people() -> list:
    """People gifts are for or from (active ones; owners are marked)."""
    return _default_client.list_people()


def list_gifts(occasion_id: int | None = None, status: str | None = None, person_id: int | None = None) -> list:
    """Gifts in one occasion (default: the current one). `status` is a comma list (idea, decided, ordered, arrived,
    ...) within what this token may read; without it, the token's usual list. `person_id` keeps one recipient's gifts.
    Each gift has its recipients, store and order fields (seller, order_number, ship_to_id, eta, arrived_on)."""
    return _default_client.list_gifts(occasion_id=occasion_id, status=status, person_id=person_id)


def get_gift(gift_id: int) -> dict:
    """One gift by id. A gift this token may not see answers exactly like one that does not exist."""
    return _default_client.get_gift(gift_id)


def get_budgets(occasion_id: int) -> dict:
    """Each person's plan, budget and money (spent, planned, in cents) in one occasion."""
    return _default_client.get_budgets(occasion_id)


def update_order(gift_id: int, seller: str | None = None, order_number: str | None = None, ship_to_id: int | None = None,
                 ship_elsewhere_ok: bool | None = None, eta: str | None = None, arrived_on: str | None = None,
                 clear: list[str] | None = None) -> dict:
    """Record order details on a gift without changing its status: seller, order_number, ship_to_id (an address id
    from list_addresses), eta and arrived_on (YYYY-MM-DD), ship_elsewhere_ok (true when shipping somewhere other than
    home is intended). Only the fields given change; `clear` names fields to empty."""
    return _default_client.update_order(gift_id, seller=seller, order_number=order_number, ship_to_id=ship_to_id,
                                        ship_elsewhere_ok=ship_elsewhere_ok, eta=eta, arrived_on=arrived_on, clear=clear)


def set_status(gift_id: int, status: str, seller: str | None = None, order_number: str | None = None,
               ship_to_id: int | None = None, eta: str | None = None, arrived_on: str | None = None) -> dict:
    """Move a gift to a status. "ordered" needs seller, ship_to_id and eta (here or already on the gift); "arrived"
    records today unless arrived_on is given. Which statuses this token may set depends on its scope."""
    return _default_client.set_status(gift_id, status, seller=seller, order_number=order_number, ship_to_id=ship_to_id,
                                      eta=eta, arrived_on=arrived_on)


def add_gift(title: str, recipient_ids: list[int], giver_ids: list[int] | None = None, occasion_id: int | None = None,
             status: str | None = None, url: str | None = None, store: str | None = None, price: str | None = None,
             notes: str | None = None) -> dict:
    """Add a gift (an idea unless `status` says otherwise) to an occasion (default: the current one). recipient_ids
    and giver_ids are person ids from list_people; price is text such as "29.99"."""
    return _default_client.add_gift(title, recipient_ids, giver_ids=giver_ids, occasion_id=occasion_id, status=status,
                                    url=url, store=store, price=price, notes=notes)


TOOLS = {f.__name__: f for f in (whoami, list_occasions, list_addresses, list_gifts, get_gift, update_order, set_status,
                                  list_people, get_budgets, add_gift)}
_DELIVERY = ("whoami", "list_occasions", "list_addresses", "list_gifts", "get_gift", "update_order", "set_status")
SCOPE_TOOLS: dict[str, tuple[str, ...]] = {
    "delivery": _DELIVERY,
    "shop": _DELIVERY + ("list_people", "get_budgets"),
    "full": _DELIVERY + ("list_people", "get_budgets", "add_gift"),
}
# (readOnlyHint, destructiveHint, idempotentHint, openWorldHint)
TOOL_HINTS = {name: (True, False, True, False) for name in TOOLS}
TOOL_HINTS.update(update_order=(False, True, True, False), set_status=(False, True, True, False),
                  add_gift=(False, False, False, False))

INSTRUCTIONS = """Gift Tracker: a family's private gift planner. This connection uses one agent token; call whoami to see its scope.
- Recording an order from a confirmation email: list_gifts (it lists gifts decided or ordered), match the gift by title, store
  and recipient, then set_status(status="ordered", seller=..., ship_to_id=..., eta=...) with ship_to_id from list_addresses
  (home unless the email ships elsewhere). If the gift is already ordered, use update_order to add the order number or a new eta.
- A delivery notice: set_status(status="arrived"), with arrived_on when the date is not today.
- Never guess a gift: if nothing matches, say so. Some gifts are hidden from this token and answer like missing ones.
- Dates are YYYY-MM-DD; money is in cents."""
_SCOPE_NOTES = {
    "delivery": "\n- This token can record order details and arrivals only; marking a gift ordered needs a shop or full token.",
    "shop": "\n- This token can also read ideas, people and budgets, and mark gifts ordered; it cannot add gifts.",
    "full": "\n- This token can also add gifts (add_gift) and set any status.",
}


def instructions_for(scope: str) -> str:
    return INSTRUCTIONS + _SCOPE_NOTES[scope]


def _annotations(name: str) -> dict:
    if ToolAnnotations is None:
        return {}
    ro, destructive, idempotent, open_world = TOOL_HINTS[name]
    return {"annotations": ToolAnnotations(read_only_hint=ro, destructive_hint=destructive, idempotent_hint=idempotent,
                                           open_world_hint=open_world)}


def build_server(scope: str):
    """An MCP server with the tools `scope` may use (SCOPE_TOOLS), or None without the mcp package. ValueError for
    an unknown scope."""
    if scope not in SCOPE_TOOLS:
        raise ValueError(f"scope must be one of {', '.join(SCOPE_TOOLS)}, not {scope!r}")
    if MCPServer is None:
        return None
    server = MCPServer("gift-tracker", instructions=instructions_for(scope))
    for name in SCOPE_TOOLS[scope]:
        server.tool(**_annotations(name))(TOOLS[name])
    return server


def main() -> int:
    """stdio. Asks the tracker which scope the token has, then serves that scope's tools."""
    if not TRACKER_API_TOKEN:
        print(f"tracker-mcp: {TOKEN_SETTING} is empty; make a token in the app (Settings > Agent tokens).", file=sys.stderr)
        return 2
    try:
        scope = _default_client.whoami()["scope"]
    except TrackerError as e:
        print(f"tracker-mcp: {e}", file=sys.stderr)
        return 2
    server = build_server(scope)
    if server is None:
        print("tracker-mcp: the MCP SDK is not installed: pip install -r requirements-mcp.txt", file=sys.stderr)
        return 2
    server.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
