"""The MCP server's REST client and tools. The client needs no mcp package, so it is tested here against the real app
(through the Flask test client, and once over real HTTP); the tests that build an MCP server skip without the package,
as Matinee's do. CI installs requirements-mcp.txt, so none of them is skipped there."""
import asyncio
import json
import logging
import threading

import pytest

import app as tracker
import mcp_server
from conftest import con_rows, mk_gift, mk_protected_gift, mk_token

ORDER = {"seller": "Camera shop", "ship_to_id": 1, "eta": "2026-12-09"}


def flask_transport(client):
    def send(method, path, body, headers):
        r = client.open(path, method=method, json=body, headers=headers)
        return r.status_code, r.get_data()
    return send


def tc(client, token):
    return mcp_server.TrackerClient(api_url="http://tracker.test", token=token, transport=flask_transport(client))


@pytest.fixture
def gifts_(client, ids):
    decided = mk_gift(client, ids.o, "Camera", to=[ids.jordan], frm=[ids.alex], status="decided")
    idea = mk_gift(client, ids.o, "Scarf", to=[ids.riley], frm=[ids.alex])
    secret = mk_protected_gift(ids, title="ZZ-SECRET-GIFT", status="decided")
    return decided, idea, secret


# ---------------------------------------------------------------- the client (no mcp package needed)
def test_client_round_trips_against_the_app(client, ids, gifts_):
    decided, idea, secret = gifts_
    c = tc(client, mk_token("shop", "Buyer"))
    assert c.whoami() == {"name": "Buyer", "scope": "shop", "unlocked": False, "mode": "family"}
    assert [o["id"] for o in c.list_occasions()] == [ids.o]
    assert c.list_addresses() == [{"id": 1, "label": "Home", "is_home": True}]
    assert [g["title"] for g in c.list_gifts()] == ["Camera", "Scarf"]
    assert [g["title"] for g in c.list_gifts(status="idea", person_id=ids.riley)] == ["Scarf"]
    assert c.get_gift(decided)["recipients"] == [{"id": ids.jordan, "name": "Jordan"}]
    j = c.update_order(decided, order_number="A-1", eta="2026-12-08")
    assert (j["order_number"], j["eta"], j["status"]) == ("A-1", "2026-12-08", "decided")
    j = c.set_status(decided, "ordered", seller="Camera shop", ship_to_id=1)
    assert (j["status"], j["seller"], j["ordered_by"]) == ("ordered", "Camera shop", "Buyer")
    assert c.update_order(decided, clear=["order_number"])["order_number"] is None
    assert "Kid" not in json.dumps(c.list_people())
    assert c.get_budgets(ids.o)["occasion"]["id"] == ids.o
    assert con_rows("SELECT action FROM gift_agent_log ORDER BY id") == \
        [("order: order_number, eta",), ("status: ordered",), ("order: order_number",)]
    with pytest.raises(mcp_server.TrackerError) as e:
        c.get_gift(secret)
    assert (str(e.value), e.value.status) == ("Tracker API error (404): No such gift.", 404)


def test_client_errors_are_sentences_without_the_token(client, ids, gifts_):
    decided, _, _ = gifts_
    t = mk_token("delivery", "Shipping")
    c = tc(client, t)
    with pytest.raises(mcp_server.TrackerError) as e:
        c.set_status(decided, "ordered", **ORDER)
    assert str(e.value) == "This token's scope (delivery) does not allow that."
    with pytest.raises(mcp_server.TrackerError) as e:
        c.add_gift("Lamp", [ids.jordan])
    assert e.value.status == 403
    with pytest.raises(mcp_server.TrackerError) as e:
        tc(client, t + "x").whoami()
    assert str(e.value) == "The tracker rejected this service's token (HTTP 401). Check TRACKER_API_TOKEN."
    html = mcp_server.TrackerClient("http://x", t, transport=lambda *a: (500, b"<!DOCTYPE html><p>Traceback</p>"))
    with pytest.raises(mcp_server.TrackerError) as e:
        html.whoami()
    assert str(e.value) == "Tracker API error (500)"

    def down(*_a):
        raise ConnectionRefusedError(t)
    with pytest.raises(mcp_server.TrackerError) as e:
        mcp_server.TrackerClient("http://x", t, transport=down).whoami()
    assert str(e.value) == mcp_server.UNREACHABLE and t not in str(e.value)


def test_a_full_client_adds_gifts(client, ids):
    c = tc(client, mk_token("full", "Owner"))
    j = c.add_gift("Lamp", [ids.jordan], giver_ids=[ids.alex], price="25.00", store="Lamp shop")
    assert (j["title"], j["price_cents"], j["store"], j["status"]) == ("Lamp", 2500, "Lamp shop", "idea")
    with pytest.raises(mcp_server.TrackerError) as e:
        c.add_gift("Bike", [ids.kid1])
    assert str(e.value) == "Tracker API error (400): No such person."


def test_client_over_real_http(client, ids, caplog):
    from werkzeug.serving import make_server
    caplog.set_level(logging.DEBUG)
    t = mk_token("delivery", "Shipping")
    srv = make_server("127.0.0.1", 0, tracker.app, threaded=True)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        c = mcp_server.TrackerClient(f"http://127.0.0.1:{srv.server_port}", t)
        assert c.whoami()["name"] == "Shipping"
        g = mk_gift(client, ids.o, "Camera", to=[ids.jordan], frm=[ids.alex], status="decided")
        assert c.update_order(g, seller="Camera shop")["seller"] == "Camera shop"
        with pytest.raises(mcp_server.TrackerError) as e:
            mcp_server.TrackerClient(f"http://127.0.0.1:{srv.server_port}", "gt_nope").whoami()
        assert e.value.status == 401
    finally:
        srv.shutdown()
        srv.server_close()
    with pytest.raises(mcp_server.TrackerError) as e:
        mcp_server.TrackerClient(f"http://127.0.0.1:{srv.server_port}", t).whoami()
    assert str(e.value) == mcp_server.UNREACHABLE
    assert t not in caplog.text


def test_scope_tools_match_the_api():
    d, s, f = (set(mcp_server.SCOPE_TOOLS[k]) for k in ("delivery", "shop", "full"))
    assert d < s < f
    assert f == set(mcp_server.TOOLS) and s - d == {"list_people", "get_budgets"} and f - s == {"add_gift"}
    for name in mcp_server.TOOLS:
        assert callable(getattr(mcp_server.TrackerClient, name)), name
        assert mcp_server.TOOLS[name].__doc__, name
    assert set(mcp_server.SCOPE_TOOLS) == set(__import__("tokens").SCOPES)
    with pytest.raises(ValueError):
        mcp_server.build_server("admin")


def test_tools_use_the_default_client(client, ids, gifts_, monkeypatch):
    monkeypatch.setattr(mcp_server, "_default_client", tc(client, mk_token("delivery", "Shipping")))
    decided = gifts_[0]
    assert [g["id"] for g in mcp_server.list_gifts()] == [decided]
    assert mcp_server.update_order(decided, eta="2026-12-01")["eta"] == "2026-12-01"
    assert mcp_server.set_status(decided, "arrived", arrived_on="2026-12-02")["arrived_on"] == "2026-12-02"


# ---------------------------------------------------------------- the MCP server (needs the mcp package)
@pytest.fixture
def sdk():
    return pytest.importorskip("mcp.server.mcpserver")


@pytest.mark.parametrize("scope", ["delivery", "shop", "full"])
def test_tools_registered_per_scope(sdk, scope):
    server = mcp_server.build_server(scope)
    tools = asyncio.run(server.list_tools())
    assert {t.name for t in tools} == set(mcp_server.SCOPE_TOOLS[scope])
    hints = {t.name: t.annotations for t in tools}
    assert hints["list_gifts"].read_only_hint is True and hints["set_status"].read_only_hint is False


def test_a_tool_call_reaches_the_api(sdk, client, ids, gifts_, monkeypatch):
    monkeypatch.setattr(mcp_server, "_default_client", tc(client, mk_token("shop", "Buyer")))
    server = mcp_server.build_server("shop")
    decided = gifts_[0]
    out = asyncio.run(server.call_tool("set_status", {"gift_id": decided, "status": "ordered", **ORDER}))
    assert out.is_error is False and json.loads(out.content[0].text)["ordered_by"] == "Buyer"
    out = asyncio.run(server.call_tool("list_gifts", {"status": "ordered"}))
    assert [json.loads(c.text)["id"] for c in out.content] == [decided]   # a list comes back one item per content block
    from mcp.server.mcpserver.exceptions import ToolError
    with pytest.raises(ToolError, match="does not allow that"):
        asyncio.run(server.call_tool("set_status", {"gift_id": decided, "status": "given"}))
    with pytest.raises(ToolError, match="No such gift"):
        asyncio.run(server.call_tool("get_gift", {"gift_id": gifts_[2]}))
