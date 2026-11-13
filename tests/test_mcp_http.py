"""The remote (Streamable HTTP) MCP service: mcp_http.py and the vendored mcp_remote.py, end to end against the real app
served on loopback. Skipped without the mcp package (the app's own venv); CI installs it and refuses skips here."""
import hashlib
import json
import logging
import threading
import time
from pathlib import Path

import pytest

pytest.importorskip("mcp.server.mcpserver")

import jwt  # noqa: E402
from cryptography.hazmat.primitives.asymmetric import rsa  # noqa: E402
from werkzeug.serving import make_server  # noqa: E402

import app as tracker  # noqa: E402
import mcp_http  # noqa: E402
import mcp_remote  # noqa: E402
import mcp_server  # noqa: E402
from conftest import con_rows, mk_gift, mk_token  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
UPSTREAM_SHA256 = "cb14c336003cf48afe1aee5bead964adf2c8ca3c23627eb166d07ef3a4dc6c84"
UPSTREAM_COMMIT = "3ccb5f1e1139abb708c113f6663e64eb953cb702"
AUD = "aud-tag-tracker-mcp"
TEAM = "family.cloudflareaccess.com"
EMAIL = "alex@example.com"
HOST = "mcp.example.com"
PATH = "/tracker/mcp"


def test_mcp_remote_copy_is_unmodified():
    lines = (ROOT / "mcp_remote.py").read_text(encoding="utf-8").split("\n")
    assert UPSTREAM_COMMIT in lines[0]
    body = list(lines)
    while body and body[0].startswith("#"):
        body.pop(0)
    assert hashlib.sha256("\n".join(body).encode("utf-8")).hexdigest() == UPSTREAM_SHA256


# ------------------------------------------------------------------ harness: the real app on loopback
@pytest.fixture
def live():
    srv = make_server("127.0.0.1", 0, tracker.app, threaded=True)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    state = {"up": True}

    def stop():
        if state["up"]:
            srv.shutdown()
            srv.server_close()
            state["up"] = False
    yield type("Live", (), {"url": f"http://127.0.0.1:{srv.server_port}", "stop": staticmethod(stop)})
    stop()


@pytest.fixture(scope="module")
def signing_key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture(scope="module")
def other_key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture
def assertion(signing_key):
    def make(key=None, **over):
        now = int(time.time())
        claims = {"iss": f"https://{TEAM}", "aud": [AUD], "iat": now, "exp": now + 300, "email": EMAIL, "sub": "u1"}
        claims.update(over)
        for k in [k for k, v in claims.items() if v is None]:
            del claims[k]
        return jwt.encode(claims, key or signing_key, algorithm="RS256", headers={"kid": "k1"})
    return make


def service_env(url, token):
    return {"TRACKER_URL": url, "TRACKER_API_TOKEN": token, "MCP_HTTP_BIND": "127.0.0.1", "MCP_HTTP_PORT": "8779",
            "MCP_HTTP_PATH": PATH, "MCP_PUBLIC_HOSTS": HOST, "CF_ACCESS_TEAM_DOMAIN": TEAM, "CF_ACCESS_AUD": AUD,
            "MCP_ALLOWED_EMAILS": EMAIL}


@pytest.fixture
def service(live, monkeypatch, signing_key):
    """(env, token, make_client): a delivery token, the tools pointed at the live app the way main() points them."""
    from starlette.testclient import TestClient
    token = mk_token("delivery", "Shipping")
    env = service_env(live.url, token)
    monkeypatch.setattr(mcp_server, "_default_client", mcp_http.tracker_client(env))

    async def resolver(_tok):
        return signing_key.public_key()

    def make(scope="delivery"):
        policy = mcp_remote.AccessPolicy(team_domain=TEAM, audiences=(AUD,), emails=frozenset({EMAIL}))
        app = mcp_http.build_http_app(env, scope, verifier=mcp_remote.AccessVerifier(policy, key_resolver=resolver))
        return TestClient(app, base_url=f"http://{HOST}")
    return env, token, make


def rpc(client, method, params=None, *, token=None, sid=None, host=None, id_=1):
    headers = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
    if token:
        headers["Cf-Access-Jwt-Assertion"] = token
    if sid:
        headers["Mcp-Session-Id"] = sid
    if host:
        headers["Host"] = host
    body = {"jsonrpc": "2.0", "method": method, **({"id": id_} if id_ is not None else {}), **({"params": params} if params is not None else {})}
    return client.post(PATH, headers=headers, json=body)


def payload(resp):
    for line in resp.text.splitlines():
        if line.startswith("data:"):
            return json.loads(line[5:])
    return resp.json()


def open_session(client, tok):
    r = rpc(client, "initialize", {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "1"}}, token=tok)
    assert r.status_code == 200, r.text
    sid = r.headers["mcp-session-id"]
    assert rpc(client, "notifications/initialized", token=tok, sid=sid, id_=None).status_code == 202
    return sid


def call(client, tok, sid, name, args, id_=3):
    return payload(rpc(client, "tools/call", {"name": name, "arguments": args}, token=tok, sid=sid, id_=id_))


# ------------------------------------------------------------------ Access, Host, health
REJECTS = {
    "no_header": lambda a, other: None,
    "garbage": lambda a, other: "not-a-jwt",
    "wrong_aud": lambda a, other: a(aud=["some-other-app"]),
    "expired": lambda a, other: a(exp=int(time.time()) - 600, iat=int(time.time()) - 900),
    "other_key": lambda a, other: a(key=other),
    "email_not_allowed": lambda a, other: a(email="jordan@example.com"),
}


def test_rejected_requests_get_403_and_never_reach_the_tracker(service, assertion, other_key):
    _, _, make = service
    with make() as c:
        for case, forge in REJECTS.items():
            r = rpc(c, "tools/call", {"name": "list_gifts", "arguments": {}}, token=forge(assertion, other_key))
            assert (r.status_code, r.json()) == (403, {"error": "forbidden"}), case
    assert con_rows("SELECT last_used_at FROM api_tokens") == [(None,)]


def test_a_valid_assertion_lists_the_scopes_tools_and_calls_the_api(service, assertion, client, ids):
    _, _, make = service
    g = mk_gift(client, ids.o, "Camera", to=[ids.jordan], frm=[ids.alex], status="decided")
    with make() as c:
        tok = assertion()
        sid = open_session(c, tok)
        msg = payload(rpc(c, "tools/list", {}, token=tok, sid=sid, id_=2))
        assert {t["name"] for t in msg["result"]["tools"]} == set(mcp_server.SCOPE_TOOLS["delivery"])
        msg = call(c, tok, sid, "update_order", {"gift_id": g, "seller": "Camera shop", "eta": "2026-12-09"})
        assert msg["result"]["isError"] is False
        msg = call(c, tok, sid, "set_status", {"gift_id": g, "status": "ordered"}, id_=4)
        assert msg["result"]["isError"] is True
        assert msg["result"]["content"][0]["text"] == "Error executing tool set_status: This token's scope (delivery) does not allow that."
        msg = call(c, tok, sid, "add_gift", {"title": "x", "recipient_ids": [ids.jordan]}, id_=5)
        assert msg["result"]["isError"] is True and "Unknown tool" in msg["result"]["content"][0]["text"]
    assert con_rows("SELECT seller, eta FROM gifts WHERE id = ?", g) == [("Camera shop", "2026-12-09")]
    assert con_rows("SELECT action FROM gift_agent_log") == [("order: seller, eta",)]


def test_a_full_service_has_add_gift(service, assertion):
    _, _, make = service
    with make("full") as c:
        tok = assertion()
        names = {t["name"] for t in payload(rpc(c, "tools/list", {}, token=tok, sid=open_session(c, tok), id_=2))["result"]["tools"]}
    assert names == set(mcp_server.SCOPE_TOOLS["full"])


def test_health_follows_the_tracker(service, live):
    _, _, make = service
    with make() as c:
        assert (c.get("/healthz").status_code, c.get("/healthz").json()) == (200, {"status": "ok"})
        live.stop()
        r = c.get("/healthz")
        assert (r.status_code, r.json()) == (503, {"status": "degraded", "error": "health check returned False"})


def test_wrong_host_is_421_and_other_paths_404(service, assertion):
    _, _, make = service
    with make() as c:
        r = rpc(c, "initialize", {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "1"}},
                token=assertion(), host="evil.example.com")
        assert (r.status_code, r.text) == (421, "Invalid Host header")
        assert c.post("/mcp", headers={"Cf-Access-Jwt-Assertion": assertion()}, json={}).status_code == 404


def test_tracker_down_is_a_sentence(service, assertion, live):
    _, token, make = service
    with make() as c:
        tok = assertion()
        sid = open_session(c, tok)
        live.stop()
        msg = call(c, tok, sid, "list_gifts", {})
    assert msg["result"]["isError"] is True
    assert msg["result"]["content"][0]["text"] == f"Error executing tool list_gifts: {mcp_server.UNREACHABLE}"


# ------------------------------------------------------------------ main() and its refusals
@pytest.fixture
def served(monkeypatch):
    calls = []
    monkeypatch.setattr(mcp_remote, "serve_http", lambda server, cfg, **kw: calls.append((server, cfg, kw)))
    monkeypatch.setattr(mcp_server, "_default_client", mcp_server._default_client)
    return calls


@pytest.mark.parametrize("value", [None, "", "  "])
def test_main_refuses_an_empty_token(served, capsys, value):
    env = service_env("http://127.0.0.1:1", value or "")
    if value is None:
        del env["TRACKER_API_TOKEN"]
    assert mcp_http.main([], env) == 2
    assert "TRACKER_API_TOKEN is empty" in capsys.readouterr().err and served == []


def test_main_refuses_a_token_the_tracker_rejects(served, capsys, live):
    t = mk_token("shop", "Buyer")
    assert mcp_http.main([], service_env(live.url, t + "x"), attempts=1) == 2
    err = capsys.readouterr().err
    assert "rejected this service's token (HTTP 401)" in err and t not in err and served == []


def test_main_gives_up_on_an_unreachable_tracker(served, capsys):
    assert mcp_http.main([], service_env("http://127.0.0.1:1", "gt_x"), attempts=2, wait=0) == 2
    assert mcp_server.UNREACHABLE in capsys.readouterr().err and served == []


@pytest.mark.parametrize("over", [{"MCP_HTTP_BIND": "192.0.2.10", "CF_ACCESS_TEAM_DOMAIN": "", "CF_ACCESS_AUD": ""},
                                  {"CF_ACCESS_TEAM_DOMAIN": "", "CF_ACCESS_AUD": ""}])
def test_main_refuses_unsafe_http_settings(served, capsys, over):
    assert mcp_http.main([], {**service_env("http://127.0.0.1:1", "gt_x"), **over}) == 2
    assert "Cloudflare Access" in capsys.readouterr().err and served == []


def test_main_serves_the_tokens_scope(served, live, caplog):
    import asyncio
    caplog.set_level(logging.DEBUG)
    t = mk_token("shop", "Buyer")
    env = {k: v for k, v in service_env("", t).items() if k != "TRACKER_URL"}
    env["TRACKER_PORT"] = live.url.rsplit(":", 1)[1]
    assert mcp_http.main([], env) == 0
    server, cfg, kw = served[0]
    assert (cfg.port, cfg.path, cfg.bind, cfg.public_hosts) == (8779, PATH, "127.0.0.1", (HOST,))
    assert {x.name for x in asyncio.run(server.list_tools())} == set(mcp_server.SCOPE_TOOLS["shop"])
    assert mcp_server._default_client.api_url == live.url and callable(kw["health_check"])
    assert t not in caplog.text


def test_settings_fall_back_like_matinee():
    assert mcp_http.tracker_url({}) == "http://127.0.0.1:8767"
    assert mcp_http.tracker_url({"TRACKER_PORT": "8768"}) == "http://127.0.0.1:8768"
    assert mcp_http.tracker_url({"TRACKER_URL": "http://127.0.0.1:9/", "TRACKER_PORT": "8768"}) == "http://127.0.0.1:9"
    env = {"CF_TEAM_DOMAIN": f"https://{TEAM}/", "CF_ACCESS_AUD": AUD}
    assert mcp_http._http_config(env).access.team_domain == TEAM
    assert mcp_http._http_config({}).path == "/tracker/mcp" and mcp_http._http_config({}).port == 8779
