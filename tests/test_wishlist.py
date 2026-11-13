"""The wishlist link (wishlist.py, docs/wishlist-feed.md): settings, the guarded transport, the cached feed, wishes on
the board, reservations written back, and what must never cross.

The network is stubbed: `wishlist._open` is the one function that touches it, and the `wl` fixture replaces it with
a scripted wishlist that records every request. Every request any test in this file makes is checked against the
allowlist as it is made (StubWishlist.open) and again, all together, by the last test in the file."""
import http.server
import json
import logging
import os
import re
import socket
import sqlite3
import subprocess
import sys
import threading
import time
from contextlib import contextmanager
from urllib.parse import urlsplit

import pytest

import db
import gifts
import settings
import wishlist
from common import ApiError
from conftest import auth, bearer, con_rows, mk_token, mut
from test_privacy import world  # noqa: F401  (a fixture: tests/test_privacy.py's data, for its walk)

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
URL = "http://192.0.2.10:8090"        # RFC 5737: never a real host
TOKEN = "wl-test-token-7f3a"
ALLOWED_KEYS = {"by", "purchased"}
PATHS = re.compile(r"/api/feed(/items/[1-9][0-9]*/(reserve|purchase))?")
REQUESTS = []                         # every request the stub received in this file (checked by the last test)
REAL_OPEN = wishlist._open


def wish(iid, name, **fields):
    """An item exactly as the feed contract lists it."""
    return {"id": iid, "name": name, "url": None, "store": None, "price": None, "occasion": None, "secret": False,
            "reserved_by": None, "reserved_by_email": None, "purchased": False, "purchased_by": None,
            "purchased_by_email": None, "archived_at": None, **fields}


def default_feed():
    return {"people": [
        {"id": 3, "name": "Kid 1", "items": [wish(11, "Lego set", url="https://example.com/lego", store="Toy Shop",
                                                  price=49.99, occasion="Christmas"),
                                             wish(12, "Paint kit", price=15)]},
        {"id": 4, "name": "Kid 2", "items": [wish(21, "Scooter", price=80)]},
    ]}


def check_request(req):
    """What every outbound request must look like, whichever test made it. `base` and `token` are the settings at the
    time of the request (the last test re-checks every request after the settings are restored)."""
    base = urlsplit(req["base"])
    assert (req["scheme"], req["host"], req["port"]) == (base.scheme, base.hostname, base.port), req
    assert req["path"].startswith(base.path) and PATHS.fullmatch(req["path"][len(base.path):]), req["path"]
    assert req["query"] == "" and "?" not in req["url"], req["url"]
    assert set(req["headers"]) <= {"Authorization", "Accept", "Content-Type"}, req["headers"]
    assert req["headers"]["Authorization"] == f"Bearer {req['token']}"
    if req["method"] == "GET":
        assert req["body"] is None and req["path"].endswith("/api/feed")
        return
    assert req["method"] == "POST"
    body = req["body"]
    assert isinstance(body, dict) and set(body) <= ALLOWED_KEYS, body
    assert body["by"] is None or (isinstance(body["by"], str) and 0 < len(body["by"]) <= 80), body
    if req["path"].endswith("/reserve"):
        assert set(body) == {"by"}, body
    else:
        assert set(body) == {"by", "purchased"} and isinstance(body["purchased"], bool) and body["by"], body
    assert "santa" not in req["raw"].decode().lower()


class StubWishlist:
    """A wishlist that answers the feed contract (docs/wishlist-feed.md) from `self.feed`, records every request,
    and can be told to be down, to answer raw bytes for the feed, or to answer a status for one path."""

    def __init__(self):
        self.feed = default_feed()
        self.requests = []
        self.down = False
        self.raw = None
        self.status = {}  # (method, path) -> status
        self.after = None  # called after each successful write (to change the world between two requests)

    def item(self, iid):
        return next(it for p in self.feed["people"] for it in p["items"] if it["id"] == iid)

    def posts(self):
        return [(r["path"], r["body"]) for r in self.requests if r["method"] == "POST"]

    def gets(self):
        return [r for r in self.requests if r["method"] == "GET"]

    def open(self, method, url, headers, body, timeout):
        u = urlsplit(url)
        req = {"method": method, "url": url, "scheme": u.scheme, "host": u.hostname, "port": u.port, "path": u.path,
               "query": u.query, "headers": dict(headers), "raw": body, "timeout": timeout,
               "body": json.loads(body) if body is not None else None, "base": settings.WISHLIST_URL,
               "token": settings.WISHLIST_TOKEN}
        REQUESTS.append(req)
        self.requests.append(req)
        check_request(req)
        assert timeout == wishlist.TIMEOUT == 5.0
        if self.down:
            raise ConnectionRefusedError()
        path = u.path[len(urlsplit(settings.WISHLIST_URL).path):]
        if (method, path) in self.status:
            return self.status[(method, path)], b'{"error": "Nope."}'
        if method == "GET":
            return 200, self.raw if self.raw is not None else json.dumps(self.feed).encode()
        out = self._write(path, req["body"])
        if out[0] == 200 and self.after:
            self.after()
        return out

    def _write(self, path, body):
        _, _, _, _, iid, action = path.split("/")
        try:
            it = self.item(int(iid))
        except StopIteration:
            return 404, b'{"error": "Not Found"}'
        conflict = 409, json.dumps({"error": f"Reserved by {it['reserved_by']}."}).encode()
        if action == "reserve":
            if body["by"] is None:
                if it["reserved_by_email"]:
                    return conflict
                it.update(reserved_by=None)
            else:
                if it["purchased"] or it["reserved_by_email"]:
                    return conflict
                it.update(reserved_by=body["by"][:80])
        elif body["purchased"]:
            if it["purchased"] or it["reserved_by_email"]:
                return conflict
            it.update(purchased=True, purchased_by=body["by"][:80], purchased_by_email=None, reserved_by=None,
                      reserved_by_email=None)
        else:
            if it["purchased_by_email"]:
                return conflict
            it.update(purchased=False, purchased_by=None, purchased_by_email=None)
        return 200, json.dumps(it).encode()


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """Without the `wl` fixture nothing may reach _open at all."""
    def refuse(*_a, **_k):
        raise AssertionError("wishlist._open called without the stub")
    monkeypatch.setattr(wishlist, "_open", refuse)


@pytest.fixture
def wl(monkeypatch, no_network):
    stub = StubWishlist()
    monkeypatch.setattr(settings, "WISHLIST_URL", URL)
    monkeypatch.setattr(settings, "WISHLIST_TOKEN", TOKEN)
    monkeypatch.setattr(wishlist, "_open", stub.open)
    return stub


def link_tab(client, person_id, tab):
    r = client.patch(f"/api/people/{person_id}", json={"wishlist_person_id": tab}, headers=mut())
    assert r.status_code == 200, r.get_json()


# ---------------------------------------------------------------- settings
def _import_settings(**env):
    full = {**os.environ, **env}
    return subprocess.run([sys.executable, "-c", "import settings; print(settings.WISHLIST_URL)"], cwd=ROOT_DIR,
                          env=full, capture_output=True, text=True)


@pytest.mark.parametrize("raw", ["ftp://192.0.2.10/", "file:///etc/passwd", "javascript:alert(1)", "192.0.2.10:8090",
                                 "http://", "https://user:pw@wishlist.example.com", "http://wishlist.example.com/?a=1",
                                 "http://wishlist.example.com/#x", "http://wishlist.example.com:99999",
                                 "http://wishlist.example.com/a b", "http://wishlist.example.com/%2e%2e"])
def test_a_bad_wishlist_url_stops_the_app_at_start_up(raw):
    r = _import_settings(WISHLIST_URL=raw, WISHLIST_TOKEN="wl-test-token")
    assert r.returncode != 0 and "WISHLIST_URL must be an http:// or https:// address" in r.stderr, r.stderr


@pytest.mark.parametrize("token", ["", "  ", "a b", "tab\there"])
def test_a_wishlist_url_needs_a_token(token):
    r = _import_settings(WISHLIST_URL="http://192.0.2.10:8090", WISHLIST_TOKEN=token)
    assert r.returncode != 0 and "WISHLIST_TOKEN must be set" in r.stderr, r.stderr


@pytest.mark.parametrize("raw,url", [("http://192.0.2.10:8090", "http://192.0.2.10:8090"),
                                     (" https://wishlist.example.com/ ", "https://wishlist.example.com"),
                                     ("http://192.0.2.10/wishlist/", "http://192.0.2.10/wishlist"),
                                     ("HTTP://192.0.2.10", "http://192.0.2.10"), ("", "")])
def test_good_wishlist_urls_are_normalized(raw, url):
    r = _import_settings(WISHLIST_URL=raw, WISHLIST_TOKEN="wl-test-token")
    assert (r.returncode, r.stdout.strip()) == (0, url), r.stderr


def test_without_a_url_the_token_is_not_required():
    r = _import_settings(WISHLIST_URL="", WISHLIST_TOKEN="")
    assert (r.returncode, r.stdout.strip()) == (0, "")


def test_the_start_up_error_never_contains_the_token():
    r = _import_settings(WISHLIST_URL="gopher://192.0.2.10", WISHLIST_TOKEN="wl-secret-in-error")
    assert r.returncode != 0 and "wl-secret-in-error" not in r.stderr + r.stdout


# ---------------------------------------------------------------- the client: transport, feed, cache
def test_off_without_a_url_and_in_school_mode(monkeypatch):
    assert wishlist.enabled() is False
    monkeypatch.setattr(settings, "WISHLIST_URL", URL)
    assert wishlist.enabled() is True
    monkeypatch.setattr(settings, "MODE", "school")
    assert wishlist.enabled() is False
    with pytest.raises(wishlist.Unreachable):
        wishlist._request("GET", wishlist.FEED_PATH)  # refused before any request: no_network would fail the test


def test_the_feed_is_fetched_from_the_one_origin_with_the_token(con, wl):
    assert wishlist.refresh(con) is True
    [req] = wl.requests
    assert (req["method"], req["scheme"], req["host"], req["port"], req["path"], req["query"]) == \
        ("GET", "http", "192.0.2.10", 8090, "/api/feed", "")
    assert req["headers"] == {"Authorization": f"Bearer {TOKEN}", "Accept": "application/json"}


def test_a_path_prefix_is_kept(con, wl, monkeypatch):
    monkeypatch.setattr(settings, "WISHLIST_URL", "https://wishlist.example.com/family")
    assert wishlist.refresh(con) is True
    [req] = wl.requests
    assert (req["scheme"], req["host"], req["port"], req["path"]) == ("https", "wishlist.example.com", None, "/family/api/feed")


def test_an_item_path_takes_only_a_positive_integer():
    assert wishlist._item_path(11, "reserve") == "/api/feed/items/11/reserve"
    for bad in (0, -1, True, "11", "11/../x", 1.0, None):
        with pytest.raises(ValueError):
            wishlist._item_path(bad, "reserve")
    with pytest.raises(ValueError):
        wishlist._item_path(11, "archive")


@pytest.mark.parametrize("raw", [b"", b"not json", b"[]", b'{"people": 5}', b'{"people": [5]}',
                                 b'{"people": [{"id": "1", "name": "A", "items": []}]}',
                                 b'{"people": [{"id": 1, "name": "A"}]}',
                                 b'{"people": [{"id": 1, "name": "A", "items": [{"id": true}]}]}',
                                 b'{"people": [{"id": 1, "name": "A", "items": [{"id": 0}]}]}',
                                 b'{"people": [{"id": 1, "name": "A", "items": [{"id": 2}, {"id": 2}]}]}',
                                 b'{"people": [{"id": 1, "name": "A", "items": [{"id": 2, "price": NaN}]}]}',
                                 b'{"people": [], "x": Infinity}', b"\xff\xfe", b"[" * 100000])
def test_a_malformed_feed_is_unreachable(raw):
    with pytest.raises(wishlist.Unreachable):
        wishlist.parse_feed(raw)


def test_fields_are_cleaned():
    feed = {"people": [{"id": 1, "name": 7, "items": [
        wish(2, 5, url="javascript:alert(1)", price=12.5, secret="yes", purchased=1, reserved_by=["x"], store="  Shop  ",
             reserved_by_email="Pat@Example.COM", archived_at=True),
        wish(3, "  Kite ", url="https://example.com/a b", price=-1),
        wish(4, "Ball", url="HTTPS://example.com/ball", price=1e12),
        wish(5, "Book", url="http://example.com/b", price="5", occasion="Birthday", archived_at=1800000000),
        wish(6, "Pen", price=19.99, purchased=True, purchased_by="Grandma Pat"),
    ]}]}
    out = wishlist.parse_feed(json.dumps(feed).encode())
    assert out["people"][0]["name"] is None
    a, b, c, d, e = out["people"][0]["items"]
    assert a == {"id": 2, "name": None, "url": None, "store": "Shop", "price_cents": 1250, "occasion": None,
                 "secret": False, "reserved_by": None, "reserved_by_email": "pat@example.com", "purchased": False,
                 "purchased_by": None, "purchased_by_email": None, "archived_at": None}
    assert (b["name"], b["url"], b["price_cents"]) == ("Kite", None, None)
    assert (c["url"], c["price_cents"]) == ("HTTPS://example.com/ball", None)
    assert (d["url"], d["price_cents"], d["occasion"], d["archived_at"]) == ("http://example.com/b", None, "Birthday", 1800000000)
    assert (e["price_cents"], e["purchased"], e["purchased_by"]) == (1999, True, "Grandma Pat")


@pytest.mark.parametrize("status", [201, 301, 302, 401, 404, 500])
def test_any_answer_but_200_is_unreachable_and_keeps_the_cache(con, wl, clock, status):
    assert wishlist.refresh(con) is True
    clock.advance(10)
    wl.status[("GET", "/api/feed")] = status
    assert wishlist.refresh(con) is False
    row = con.execute("SELECT fetched_at, failed_at FROM wishlist_cache").fetchone()
    assert tuple(row) == (clock.t - 10, clock.t)
    state = wishlist.status(wishlist._row(con), clock.t)
    assert (state["reachable"], state["age_seconds"], len(state["people"])) == (False, 10, 2)


def test_a_malformed_or_refused_feed_is_unreachable_never_a_500(con, wl):
    for raw in (b"{", b'{"people": [{"id": 1}]}', b"x" * 100):
        wl.raw = raw
        assert wishlist.refresh(con) is False
    wl.raw, wl.down = None, True
    assert wishlist.refresh(con) is False
    assert con.execute("SELECT feed, fetched_at FROM wishlist_cache").fetchone()[0] is None


def test_the_cache_keeps_its_age_and_is_refreshed_only_when_old(con, wl, clock):
    state = wishlist.current(con)
    assert (state["reachable"], state["fetched_at"], state["age_seconds"], len(wl.gets())) == (True, clock.t, 0, 1)
    clock.advance(60)
    assert wishlist.current(con)["age_seconds"] == 60 and len(wl.gets()) == 1
    clock.advance(1)
    assert wishlist.current(con)["age_seconds"] == 0 and len(wl.gets()) == 2


def test_a_failure_is_not_retried_for_thirty_seconds(con, wl, clock):
    wishlist.current(con)
    clock.advance(120)
    wl.down = True
    state = wishlist.current(con)
    assert (state["reachable"], state["age_seconds"], len(wl.requests)) == (False, 120, 2)
    clock.advance(29)
    assert wishlist.current(con)["reachable"] is False and len(wl.requests) == 2
    clock.advance(1)
    wl.down = False
    state = wishlist.current(con)
    assert (state["reachable"], state["age_seconds"], len(wl.requests)) == (True, 0, 3)


def _linked_gift(con, ids, item_id, to=None):
    gid = con.execute("INSERT INTO gifts (occasion_id, title, status, wishlist_item_id, created_by, created_at, updated_at) "
                      "VALUES (?, 'Wish', 'idea', ?, 'a', 0, 0)", (ids.o, item_id)).lastrowid
    con.execute("INSERT INTO gift_recipients (gift_id, person_id) VALUES (?, ?)", (gid, to or ids.kid1))
    return gid


def test_a_deleted_wish_unlinks_its_gift_but_a_hidden_tab_does_not(client, con, ids, wl):
    link_tab(client, ids.kid1, 3)
    link_tab(client, ids.kid2, 4)
    lego, paint, scooter = _linked_gift(con, ids, 11), _linked_gift(con, ids, 12), _linked_gift(con, ids, 21, ids.kid2)
    unlinked_person = _linked_gift(con, ids, 99, ids.kid3)   # kid3 has no tab: its link is left alone
    assert wishlist.refresh(con)
    wl.feed["people"][0]["items"] = [it for it in wl.feed["people"][0]["items"] if it["id"] != 12]  # deleted
    del wl.feed["people"][1]                                                                     # tab hidden
    assert wishlist.refresh(con)
    assert con_rows("SELECT id, wishlist_item_id FROM gifts ORDER BY id") == \
        [(lego, 11), (paint, None), (scooter, 21), (unlinked_person, 99)]
    assert con_rows("SELECT title FROM gifts WHERE id = ?", paint) == [("Wish",)]  # the gift stays, off the list


def test_the_label_is_the_household_never_santa(client, con, ids):
    label = lambda gids: wishlist.label(con, gids)  # noqa: E731
    assert label([ids.alex]) == label([ids.sam]) == label([ids.alex, ids.sam]) == "Us"
    assert label([]) is None and label(None) is None
    assert label([ids.jordan]) == "Jordan"
    for pid in (ids.jordan, ids.riley):
        client.patch(f"/api/people/{pid}", json={"household": "Riley House"}, headers=mut())
    assert label([ids.jordan]) == label([ids.jordan, ids.riley]) == "Riley House"
    assert label([ids.alex, ids.jordan]) == label([ids.jordan, ids.sam]) == "Us & Riley House"
    assert label([ids.casey, ids.jordan]) == "Riley House & Casey"
    for pid in (ids.alex, ids.sam):
        client.patch(f"/api/people/{pid}", json={"household": "The Hollows"}, headers=mut())
    assert label([ids.alex]) == label([ids.sam]) == "The Hollows"
    client.patch(f"/api/people/{ids.sam}", json={"household": "Elsewhere"}, headers=mut())
    assert label([ids.alex]) == label([ids.sam]) == "Us"   # owners in two households: the neutral label
    client.patch(f"/api/people/{ids.casey}", json={"household": "C" * 100}, headers=mut())
    assert label([ids.casey]) == "C" * 80


# ---------------------------------------------------------------- the real transport, against loopback servers
class _Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *_a):
        pass

    def do_GET(self):  # noqa: N802
        self.server.paths.append(self.path)
        self.server.respond(self)


def _serve(respond):
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    srv.paths, srv.respond = [], respond
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


@pytest.fixture
def real(monkeypatch):
    monkeypatch.setattr(wishlist, "_open", REAL_OPEN)
    monkeypatch.setattr(settings, "WISHLIST_TOKEN", TOKEN)
    servers = []

    def at(respond):
        srv = _serve(respond)
        servers.append(srv)
        monkeypatch.setattr(settings, "WISHLIST_URL", f"http://127.0.0.1:{srv.server_address[1]}")
        return srv
    yield at
    for srv in servers:
        srv.shutdown()
        srv.server_close()


def test_the_real_transport_reads_a_feed(real, con):
    def ok(h):
        assert h.headers["Authorization"] == f"Bearer {TOKEN}"
        body = json.dumps(default_feed()).encode()
        h.send_response(200)
        h.send_header("Content-Length", str(len(body)))
        h.end_headers()
        h.wfile.write(body)
    srv = real(ok)
    assert wishlist.refresh(con) is True and srv.paths == ["/api/feed"]


def test_the_real_transport_follows_no_redirect(real, con):
    def redirect(h):
        h.send_response(302)
        h.send_header("Location", "/elsewhere")
        h.send_header("Content-Length", "0")
        h.end_headers()
    srv = real(redirect)
    assert wishlist.refresh(con) is False
    assert srv.paths == ["/api/feed"]   # one request: the Location was not followed


@pytest.mark.parametrize("with_length", [True, False])
def test_the_real_transport_caps_the_size(real, con, monkeypatch, with_length):
    monkeypatch.setattr(wishlist, "MAX_BYTES", 1000)

    def big(h):
        h.send_response(200)
        if with_length:
            h.send_header("Content-Length", "1001")
        h.end_headers()
        h.wfile.write(b" " * 1001)
    real(big)
    assert wishlist.refresh(con) is False


def test_the_real_transport_gives_up_on_a_silent_server(real, con, monkeypatch):
    monkeypatch.setattr(wishlist, "TIMEOUT", 0.3)
    silent = socket.socket()
    silent.bind(("127.0.0.1", 0))
    silent.listen(1)  # accepts the connection in the kernel, never answers
    try:
        monkeypatch.setattr(settings, "WISHLIST_URL", f"http://127.0.0.1:{silent.getsockname()[1]}")
        start = time.monotonic()
        assert wishlist.refresh(con) is False
        assert time.monotonic() - start < 2
    finally:
        silent.close()


def test_the_real_transport_gives_up_on_a_slow_drip(real, con, monkeypatch):
    monkeypatch.setattr(wishlist, "TIMEOUT", 0.5)

    def drip(h):
        h.send_response(200)
        h.end_headers()
        for _ in range(30):
            try:
                h.wfile.write(b" ")
                h.wfile.flush()
            except OSError:
                return
            time.sleep(0.1)
    real(drip)
    start = time.monotonic()
    assert wishlist.refresh(con) is False
    assert time.monotonic() - start < 2


def test_the_token_is_in_no_log_line(con, wl, caplog):
    caplog.set_level(logging.DEBUG)
    wl.down = True
    wishlist.refresh(con)
    wl.down, wl.raw = False, b"{bad"
    wishlist.refresh(con)
    wl.raw = None
    wl.status[("GET", "/api/feed")] = 500
    wishlist.refresh(con)
    assert len([r for r in caplog.records if r.name == "tracker.wishlist"]) == 3
    assert TOKEN not in caplog.text and "Bearer" not in caplog.text


# ---------------------------------------------------------------- wishes on the board, aliases, the routes
WISHLIST_ROUTES = [("GET", "/api/wishlist"), ("POST", "/api/wishlist/refresh"), ("GET", "/api/wishlist/aliases"),
                   ("PUT", "/api/wishlist/aliases"), ("DELETE", "/api/wishlist/aliases/1")]


def all_routes(ids):
    return WISHLIST_ROUTES + [("POST", f"/api/occasions/{ids.o}/board/{ids.kid1}/wishes/11")]


def board(client, ids, pid=None):
    r = client.get(f"/api/occasions/{ids.o}/board/{pid or ids.kid1}", headers=auth())
    assert r.status_code == 200, r.get_json()
    return r.get_json()


def add(client, ids, item_id, pid=None):
    return client.post(f"/api/occasions/{ids.o}/board/{pid or ids.kid1}/wishes/{item_id}", json={}, headers=mut())


def test_with_the_feature_off_the_board_is_unchanged_and_the_routes_are_404(client, ids, unlock):
    unlock()
    link_tab(client, ids.kid1, 3)
    assert set(board(client, ids)) == {"person", "unassigned", "givers", "split", "evenness"}
    missing = client.get("/api/nothing-here", headers=auth()).get_json()
    for method, path in all_routes(ids):
        r = client.open(path, method=method, json={}, headers=mut())
        assert (r.status_code, r.get_json()) == (404, missing), (method, path)


def test_the_board_lists_open_wishes_of_the_linked_tab(client, ids, wl, unlock, clock):
    unlock()
    unlinked = board(client, ids)["wishlist"]
    assert unlinked == {"linked": False, "tab_id": None, "reachable": True, "fetched_at": clock.t, "age_seconds": 0,
                        "tabs": [{"id": 3, "name": "Kid 1", "person_id": None}, {"id": 4, "name": "Kid 2", "person_id": None}],
                        "open": [], "reserved": [], "unmatched": []}
    link_tab(client, ids.kid1, 3)
    wl_block = board(client, ids)["wishlist"]
    assert (wl_block["linked"], wl_block["tab_id"]) == (True, 3)
    assert wl_block["tabs"][0] == {"id": 3, "name": "Kid 1", "person_id": ids.kid1}
    assert wl_block["open"] == [
        {"id": 11, "name": "Lego set", "url": "https://example.com/lego", "store": "Toy Shop", "price_cents": 4999,
         "occasion": "Christmas", "secret": False, "reserved_by": None, "purchased": False, "purchased_by": None},
        {"id": 12, "name": "Paint kit", "url": None, "store": None, "price_cents": 1500, "occasion": None,
         "secret": False, "reserved_by": None, "purchased": False, "purchased_by": None}]
    assert len(wl.gets()) == 1  # the second board within a minute used the cache
    assert client.get("/api/wishlist", headers=auth()).get_json() == {
        "reachable": True, "fetched_at": clock.t, "age_seconds": 0, "tabs": wl_block["tabs"]}


def test_adding_a_wish_makes_a_linked_unassigned_gift_and_sends_nothing(client, ids, wl, unlock):
    unlock()
    link_tab(client, ids.kid1, 3)
    link_tab(client, ids.kid2, 4)
    board(client, ids)
    r = add(client, ids, 11)
    assert r.status_code == 201, r.get_json()
    gift = r.get_json()
    assert (gift["title"], gift["url"], gift["store"], gift["price_cents"], gift["giver_ids"], gift["recipient_ids"],
            gift["status"], gift["santa"]) == ("Lego set", "https://example.com/lego", "Toy Shop", 4999, [], [ids.kid1], "idea", False)
    assert con_rows("SELECT wishlist_item_id FROM gifts WHERE id = ?", gift["id"]) == [(11,)]
    assert wl.posts() == []
    brd = board(client, ids)
    assert [w["id"] for w in brd["wishlist"]["open"]] == [12]
    [linked] = brd["unassigned"]
    assert linked["wish"] == {"item_id": 11, "listed": True, "reserved_by": None, "purchased": False,
                              "purchased_by": None, "in_sync": True}
    assert (add(client, ids, 11).status_code, add(client, ids, 11).get_json()) == (409, {"error": "This wish is already on a board. Reload."})
    assert add(client, ids, 21).status_code == 404               # Kid 2's wish, not on Kid 1's tab
    assert add(client, ids, 999).get_json() == {"error": "No such wish."}
    assert add(client, ids, 11, pid=ids.jordan).status_code == 404  # no board for an unprotected person
    wl.item(12).update(reserved_by="Grandma Pat")
    client.post("/api/wishlist/refresh", json={}, headers=mut())
    assert add(client, ids, 12).status_code == 409
    assert con_rows("SELECT COUNT(*) FROM gifts") == [(1,)]
    assert wl.posts() == []


def test_a_reservation_made_on_the_wishlist_appears_under_the_matched_person(client, ids, wl, unlock):
    unlock()
    link_tab(client, ids.kid1, 3)
    items = wl.feed["people"][0]["items"]
    items += [wish(13, "Kite", reserved_by="Alex", reserved_by_email="alex@example.com"),
              wish(14, "Book", reserved_by="Casey C", reserved_by_email="casey@example.com"),
              wish(15, "Ball", reserved_by="  grandma   PAT "),
              wish(16, "Drum", purchased=True, purchased_by="Grandma Pat"),
              wish(17, "Yo-yo", reserved_by="Uncle Rob"),
              wish(18, "Hat", reserved_by="Uncle Rob"),
              wish(19, "Map", reserved_by="R", reserved_by_email="rob@example.com")]
    assert client.put("/api/wishlist/aliases", json={"kind": "email", "value": "Casey@Example.com", "person_id": ids.casey},
                      headers=mut()).status_code == 201
    assert client.put("/api/wishlist/aliases", json={"kind": "name", "value": "Grandma Pat", "person_id": ids.riley},
                      headers=mut()).status_code == 201
    reserved = board(client, ids)["wishlist"]["reserved"]
    assert [(w["id"], w["person_id"], w["label"]) for w in reserved] == [
        (13, ids.alex, "Alex"), (14, ids.casey, "Casey C"), (15, ids.riley, "grandma   PAT"), (16, ids.riley, "Grandma Pat"),
        (17, None, "Uncle Rob"), (18, None, "Uncle Rob"), (19, None, "R")]
    assert board(client, ids)["wishlist"]["unmatched"] == [
        {"kind": "name", "value": "uncle rob", "label": "Uncle Rob"},
        {"kind": "email", "value": "rob@example.com", "label": "R"}]


def test_mapping_a_holder_once_is_remembered(client, ids, wl, unlock):
    unlock()
    link_tab(client, ids.kid1, 3)
    wl.feed["people"][0]["items"].append(wish(17, "Yo-yo", reserved_by="Uncle Rob"))
    put = lambda body: client.put("/api/wishlist/aliases", json=body, headers=mut())  # noqa: E731
    r = put({"kind": "name", "value": "Uncle Rob", "person_id": ids.jordan})
    assert (r.status_code, r.get_json()) == (201, {"id": 1, "kind": "name", "value": "uncle rob", "person_id": ids.jordan})
    r = put({"kind": "name", "value": "UNCLE  rob", "person_id": ids.casey})
    assert (r.status_code, r.get_json()) == (200, {"id": 1, "kind": "name", "value": "uncle rob", "person_id": ids.casey})
    assert board(client, ids)["wishlist"]["unmatched"] == []
    assert board(client, ids)["wishlist"]["reserved"][0]["person_id"] == ids.casey
    assert client.get("/api/wishlist/aliases", headers=auth()).get_json() == [
        {"id": 1, "kind": "name", "value": "uncle rob", "person_id": ids.casey}]
    for bad, msg in (({"kind": "phone", "value": "x", "person_id": ids.casey}, "kind must be email or name."),
                     ({"kind": "name", "value": "  ", "person_id": ids.casey}, "value must be 1 to 254 characters for an email, 80 for a name."),
                     ({"kind": "name", "value": "x" * 81, "person_id": ids.casey}, "value must be 1 to 254 characters for an email, 80 for a name."),
                     ({"kind": "name", "value": "x", "person_id": 999999}, "No such person."),
                     ({"kind": "name", "value": "x", "person_id": True}, "person_id must be a whole number."),
                     ({"kind": "name", "value": 5, "person_id": ids.casey}, "value must be text.")):
        r = put(bad)
        assert (r.status_code, r.get_json()) == (400, {"error": msg}), bad
    assert client.delete("/api/wishlist/aliases/1", json={}, headers=mut()).status_code == 200
    r = client.delete("/api/wishlist/aliases/1", json={}, headers=mut())
    assert (r.status_code, r.get_json()) == (404, {"error": "No such alias."})
    assert board(client, ids)["wishlist"]["unmatched"] == [{"kind": "name", "value": "uncle rob", "label": "Uncle Rob"}]


def test_the_board_shows_the_cache_and_its_age_when_the_wishlist_is_down(client, ids, wl, unlock, clock):
    unlock()
    link_tab(client, ids.kid1, 3)
    board(client, ids)
    clock.advance(120)
    wl.down = True
    block = board(client, ids)["wishlist"]
    assert (block["reachable"], block["fetched_at"], block["age_seconds"]) == (False, clock.t - 120, 120)
    assert [w["id"] for w in block["open"]] == [11, 12]
    r = client.post("/api/wishlist/refresh", json={}, headers=mut())
    assert (r.status_code, r.get_json()["reachable"]) == (200, False)
    wl.down = False
    assert client.post("/api/wishlist/refresh", json={}, headers=mut()).get_json()["reachable"] is True


def test_a_wishlist_that_never_answered_gives_an_empty_board_block(client, ids, wl, unlock):
    unlock()
    link_tab(client, ids.kid1, 3)
    wl.down = True
    block = board(client, ids)["wishlist"]
    assert (block["reachable"], block["fetched_at"], block["age_seconds"], block["open"], block["tabs"]) == (False, None, None, [], [])


def test_wish_text_is_returned_as_data(client, ids, wl, unlock):
    unlock()
    link_tab(client, ids.kid1, 3)
    wl.feed["people"][0]["items"] = [wish(11, "<img src=x onerror=alert(1)>", url="javascript:alert(1)",
                                          store="</script>")]
    [w] = board(client, ids)["wishlist"]["open"]
    assert (w["name"], w["url"], w["store"]) == ("<img src=x onerror=alert(1)>", None, "</script>")
    gift = add(client, ids, 11).get_json()
    assert (gift["title"], gift["url"]) == ("<img src=x onerror=alert(1)>", None)


def test_every_wishlist_route_needs_the_unlock(client, ids, wl):
    link_tab(client, ids.kid1, 3)
    for method, path in all_routes(ids):
        r = client.open(path, method=method, json={}, headers=mut())
        assert (r.status_code, r.get_json()) == (423, {"error": "Locked", "locked": True}), (method, path)
    assert client.get(f"/api/occasions/{ids.o}/board/{ids.kid1}", headers=auth()).status_code == 423
    assert wl.requests == []
    assert con_rows("SELECT COUNT(*) FROM wishlist_cache") == [(0,)]


def test_the_privacy_walk_passes_with_the_feature_on(client, world, wl, unlock):  # noqa: F811  (the imported fixture)
    """tests/test_privacy.py's locked walk over every GET, with the link on and a feed full of its markers."""
    from test_privacy import leaks, walk_gets
    i = world.ids
    link_tab(client, i.kid1, 3)
    wl.feed["people"][0]["items"] += [wish(13, "ZZ-SECRET-WISH", reserved_by="ZZ-SECRET-RESERVER", price=388.88),
                                      wish(14, "ZZ-SECRET-SANTA-WISH", url="https://example.com/zz-secret")]
    seen = walk_gets(client, world)
    for url, body in seen.items():
        assert not leaks(body), (url, leaks(body))
    unlock()
    seen = walk_gets(client, world, query=("",))
    assert any("ZZ-SECRET-WISH" in t and "ZZ-SECRET-RESERVER" in t for t in seen.values())  # control: the walk reaches it


def test_the_wishlist_routes_are_in_the_privacy_walk():
    from test_privacy import api_rules
    walked = {r.endpoint for r in api_rules({"GET", "POST", "PUT", "PATCH", "DELETE"})}
    assert {"wishlist_overview", "wishlist_refresh", "wishlist_add_wish", "wishlist_aliases_list", "wishlist_alias_set",
            "wishlist_alias_delete"} <= walked


# ---------------------------------------------------------------- writing back
RESERVE, PURCHASE = "/api/feed/items/11/reserve", "/api/feed/items/11/purchase"


@pytest.fixture
def lego(client, ids, wl, unlock):
    """Kid 1's tab linked, the board loaded once (the cache), and the Lego wish on the board as an unassigned gift."""
    unlock()
    link_tab(client, ids.kid1, 3)
    board(client, ids)
    r = add(client, ids, 11)
    assert r.status_code == 201
    return r.get_json()["id"]


def assign(client, gift_id, givers, expected):
    return client.post(f"/api/gifts/{gift_id}/assign", json={"giver_ids": givers, "expected_giver_ids": expected}, headers=mut())


def gift(client, gift_id):
    return client.get(f"/api/gifts/{gift_id}", headers=auth()).get_json()


def local(gift_id):
    """Everything local about the gift that a failed write must leave alone."""
    return (con_rows("SELECT status, santa, told_at, wishlist_item_id FROM gifts WHERE id = ?", gift_id),
            con_rows("SELECT person_id FROM gift_givers WHERE gift_id = ? ORDER BY person_id", gift_id))


def status(client, gift_id, value):
    return client.post(f"/api/gifts/{gift_id}/status", json={"status": value}, headers=mut())


def test_assigning_reserves_under_the_household_label(client, ids, wl, lego):
    for pid in (ids.jordan, ids.riley):
        client.patch(f"/api/people/{pid}", json={"household": "Riley House"}, headers=mut())
    r = assign(client, lego, [ids.jordan], [])
    assert r.status_code == 200 and r.get_json()["status"] == "told"
    assert wl.posts() == [(RESERVE, {"by": "Riley House"})]
    assert wl.item(11)["reserved_by"] == "Riley House"
    [grp] = board(client, ids)["givers"]
    assert grp["gifts"][0]["wish"] == {"item_id": 11, "listed": True, "reserved_by": "Riley House", "purchased": False,
                                       "purchased_by": None, "in_sync": True}
    assert assign(client, lego, [ids.riley], [ids.jordan]).status_code == 200  # same household: nothing to send
    assert len(wl.posts()) == 1
    assert assign(client, lego, [ids.casey], [ids.riley]).status_code == 200
    assert wl.posts()[-1] == (RESERVE, {"by": "Casey"})


def test_the_owners_label_is_identical_with_and_without_santa(client, ids, wl, unlock):
    """Review Focus 1: one owner, the other, both, Santa or not: always the household label, and Santa sends nothing."""
    unlock()
    link_tab(client, ids.kid1, 3)
    wl.feed["people"][0]["items"] += [wish(13, "Kite"), wish(14, "Drum")]
    board(client, ids)
    a, b, c = (add(client, ids, i).get_json()["id"] for i in (11, 12, 13))
    assert assign(client, a, [ids.alex], []).status_code == 200                                  # Alex, no Santa
    r = client.patch(f"/api/gifts/{b}", json={"giver_ids": [ids.sam], "santa": True}, headers=mut())  # Sam, Santa
    assert r.status_code == 200 and r.get_json()["santa"] is True
    r = client.patch(f"/api/gifts/{c}", json={"giver_ids": [ids.alex, ids.sam], "santa": True}, headers=mut())  # both, Santa
    assert r.status_code == 200 and r.get_json()["santa"] is True
    bodies = [body for _path, body in wl.posts()]
    assert bodies == [{"by": "Us"}, {"by": "Us"}, {"by": "Us"}]
    sent = len(wl.requests)
    for value in (True, False, True):
        assert client.patch(f"/api/gifts/{a}", json={"santa": value}, headers=mut()).status_code == 200
    assert assign(client, a, [ids.sam], [ids.alex]).status_code == 200            # Alex to Sam: same label
    assert assign(client, a, [ids.alex, ids.sam], [ids.sam]).status_code == 200    # to both: same label
    assert len(wl.requests) == sent
    assert {wl.item(i)["reserved_by"] for i in (11, 12, 13)} == {"Us"}
    assert all("santa" not in json.dumps(r["body"]).lower() for r in wl.requests if r["body"])


def test_unassigning_releases(client, ids, wl, lego):
    assign(client, lego, [ids.jordan], [])
    r = assign(client, lego, [], [ids.jordan])
    assert r.status_code == 200 and r.get_json()["status"] == "idea"
    assert wl.posts()[-1] == (RESERVE, {"by": None})
    assert wl.item(11)["reserved_by"] is None


def test_a_409_from_the_wishlist_is_409_changes_nothing_and_refreshes(client, ids, wl, lego):
    wl.item(11).update(reserved_by="Pat", reserved_by_email="pat@example.com")  # the cache still says open
    before, gets = local(lego), len(wl.gets())
    r = assign(client, lego, [ids.jordan], [])
    assert (r.status_code, r.get_json()) == (409, {"error": "Someone else has this on the wishlist. Reload."})
    assert local(lego) == before
    assert len(wl.gets()) == gets + 1  # refetched
    assert board(client, ids)["unassigned"][0]["wish"]["reserved_by"] == "Pat"


def test_unreachable_is_503_and_changes_nothing(client, ids, wl, clock, lego):
    before = local(lego)
    wl.down = True
    r = assign(client, lego, [ids.jordan], [])
    assert (r.status_code, r.get_json()) == (503, {"error": "The wishlist cannot be reached. Nothing was changed."})
    assert local(lego) == before
    assert con_rows("SELECT failed_at FROM wishlist_cache") == [(clock.t,)]
    assert board(client, ids)["wishlist"]["reachable"] is False
    # The reserve may have arrived with only its answer lost, so the release was tried too; nothing is queued.
    assert wl.posts() == [(RESERVE, {"by": "Jordan"}), (RESERVE, {"by": None})]
    wl.down = False
    assert assign(client, lego, [ids.jordan], []).status_code == 200


def test_a_removed_wish_is_409_and_becomes_off_list(client, ids, wl, lego):
    wl.feed["people"][0]["items"] = [it for it in wl.feed["people"][0]["items"] if it["id"] != 11]
    before = local(lego)
    r = assign(client, lego, [ids.jordan], [])
    assert (r.status_code, r.get_json()) == (409, {"error": "This wish is no longer on the wishlist. Reload."})
    assert local(lego)[1] == before[1]
    assert con_rows("SELECT wishlist_item_id, title FROM gifts WHERE id = ?", lego) == [(None, "Lego set")]  # off the list
    sent = len(wl.requests)
    assert assign(client, lego, [ids.jordan], []).status_code == 200
    assert len(wl.requests) == sent  # an off-list gift is the tracker's alone


def test_a_local_failure_after_a_reserve_releases_it(client, ids, wl, lego, monkeypatch):
    """Review Focus 2: the reserve was accepted, then the local side failed (here the cache write after it)."""
    before = local(lego)

    def broken(*_a, **_k):
        raise sqlite3.OperationalError("disk I/O error")
    monkeypatch.setattr(wishlist, "_patch_cache", broken)
    r = assign(client, lego, [ids.jordan], [])
    assert r.status_code == 500
    assert wl.posts() == [(RESERVE, {"by": "Jordan"}), (RESERVE, {"by": None})]
    assert wl.item(11)["reserved_by"] is None
    assert local(lego) == before


def test_a_local_failure_after_a_relabel_restores_the_old_label(client, ids, wl, lego, monkeypatch):
    assign(client, lego, [ids.jordan], [])
    before = local(lego)

    @contextmanager
    def failing_commit(con, mode="IMMEDIATE"):
        con.execute(f"BEGIN {mode}")
        try:
            yield con
        except BaseException:
            con.execute("ROLLBACK")
            raise
        con.execute("ROLLBACK")
        raise sqlite3.OperationalError("database or disk is full")
    monkeypatch.setattr(db, "transaction", failing_commit)
    r = assign(client, lego, [ids.casey], [ids.jordan])
    assert r.status_code == 500
    assert wl.posts()[-2:] == [(RESERVE, {"by": "Casey"}), (RESERVE, {"by": "Jordan"})]
    assert wl.item(11)["reserved_by"] == "Jordan"
    assert local(lego) == before


def test_a_failure_on_the_second_write_takes_back_the_first(client, ids, wl, lego):
    """A confirmed gift reassigned to us: un-purchase, then reserve. The reserve is refused, so the purchase comes back."""
    assign(client, lego, [ids.jordan], [])
    status(client, lego, "confirmed")
    before = local(lego)

    def snatch():
        if wl.item(11)["purchased"] is False and wl.item(11)["reserved_by"] is None:
            wl.item(11).update(reserved_by="Pat", reserved_by_email="pat@example.com")
    wl.after = snatch
    r = assign(client, lego, [ids.alex], [ids.jordan])
    assert r.status_code == 409
    assert wl.posts()[-3:] == [(PURCHASE, {"by": "Jordan", "purchased": False}), (RESERVE, {"by": "Us"}),
                               (PURCHASE, {"by": "Jordan", "purchased": True})]
    assert local(lego) == before


def test_two_owners_assigning_at_once_make_one_request(client, ids, wl, lego, monkeypatch):
    real = gifts._set_givers

    def slow(con, gift_id, giver_ids):
        time.sleep(0.3)
        real(con, gift_id, giver_ids)
    monkeypatch.setattr(gifts, "_set_givers", slow)
    start, results = threading.Barrier(2), {}

    def run(name, givers):
        con = db.connect(settings.DB_PATH)
        try:
            start.wait()
            wishlist.assign(con, lego, givers, [])
            results[name] = "ok"
        except ApiError as e:
            results[name] = e.status
        finally:
            con.close()
    threads = [threading.Thread(target=run, args=("a", [ids.jordan])), threading.Thread(target=run, args=("b", [ids.casey]))]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(results.values(), key=str) == [409, "ok"]
    winner = "Jordan" if results["a"] == "ok" else "Casey"
    assert wl.posts() == [(RESERVE, {"by": winner})]
    assert wl.item(11)["reserved_by"] == winner


def test_confirmed_marks_purchased_and_told_undoes_it(client, ids, wl, lego):
    assign(client, lego, [ids.jordan], [])
    assert status(client, lego, "confirmed").status_code == 200
    assert wl.posts()[-1] == (PURCHASE, {"by": "Jordan", "purchased": True})
    assert (wl.item(11)["purchased"], wl.item(11)["purchased_by"], wl.item(11)["reserved_by"]) == (True, "Jordan", None)
    sent = len(wl.posts())
    assert status(client, lego, "given").status_code == 200   # still purchased: nothing to send
    assert len(wl.posts()) == sent
    assert status(client, lego, "told").status_code == 200
    assert wl.posts()[sent:] == [(PURCHASE, {"by": "Jordan", "purchased": False}), (RESERVE, {"by": "Jordan"})]
    assert (wl.item(11)["purchased"], wl.item(11)["reserved_by"]) == (False, "Jordan")
    assert board(client, ids)["givers"][0]["gifts"][0]["wish"]["in_sync"] is True


def test_reassigning_a_purchased_wish_moves_the_purchase(client, ids, wl, lego):
    assign(client, lego, [ids.jordan], [])
    status(client, lego, "confirmed")
    sent = len(wl.posts())
    assert assign(client, lego, [ids.casey], [ids.jordan]).get_json()["status"] == "confirmed"
    assert wl.posts()[sent:] == [(PURCHASE, {"by": "Jordan", "purchased": False}), (PURCHASE, {"by": "Casey", "purchased": True})]
    sent = len(wl.posts())
    assert assign(client, lego, [ids.alex], [ids.casey]).get_json()["status"] == "decided"
    assert wl.posts()[sent:] == [(PURCHASE, {"by": "Casey", "purchased": False}), (RESERVE, {"by": "Us"})]


def test_ours_track_status_and_other_fields_never_send(client, ids, wl, lego):
    assign(client, lego, [ids.alex], [])
    sent = len(wl.requests)
    for value in ("decided", "arrived", "wrapped", "given", "idea"):
        assert status(client, lego, value).status_code == 200, value
    for body in ({"santa": True}, {"title": "Lego castle", "price": "60", "notes": "Hide it", "store": "Elsewhere"},
                 {"recipient_ids": [ids.kid1, ids.kid2]}, {"santa": False}):
        assert client.patch(f"/api/gifts/{lego}", json=body, headers=mut()).status_code == 200, body
    assert len(wl.requests) == sent


def test_patch_keeps_the_wishlist_in_step(client, ids, wl, lego):
    r = client.patch(f"/api/gifts/{lego}", json={"giver_ids": [ids.casey]}, headers=mut())
    assert r.status_code == 200
    assert wl.posts() == [(RESERVE, {"by": "Casey"})]
    r = client.patch(f"/api/gifts/{lego}", json={"status": "confirmed"}, headers=mut())
    assert r.status_code == 200 and wl.posts()[-1] == (PURCHASE, {"by": "Casey", "purchased": True})
    wl.down = True
    r = client.patch(f"/api/gifts/{lego}", json={"giver_ids": [ids.jordan], "title": "Changed"}, headers=mut())
    assert r.status_code == 503
    assert (gift(client, lego)["giver_ids"], gift(client, lego)["title"]) == ([ids.casey], "Lego set")


def test_deleting_a_reserved_gift_releases_it(client, ids, wl, lego, unlock):
    assign(client, lego, [ids.jordan], [])
    wl.down = True
    r = client.delete(f"/api/gifts/{lego}", json={}, headers=mut())
    assert r.status_code == 503 and con_rows("SELECT COUNT(*) FROM gifts") == [(1,)]
    wl.down = False
    assert client.delete(f"/api/gifts/{lego}", json={}, headers=mut()).status_code == 200
    assert wl.posts()[-1] == (RESERVE, {"by": None}) and wl.item(11)["reserved_by"] is None
    assert con_rows("SELECT COUNT(*) FROM gifts") == [(0,)]
    # A purchase stays when its gift is deleted, and an unassigned gift has nothing to release.
    board(client, ids)
    a = add(client, ids, 11).get_json()["id"]
    b = add(client, ids, 12).get_json()["id"]
    assign(client, a, [ids.jordan], [])
    status(client, a, "confirmed")
    sent = len(wl.posts())
    assert client.delete(f"/api/gifts/{a}", json={}, headers=mut()).status_code == 200
    assert client.delete(f"/api/gifts/{b}", json={}, headers=mut()).status_code == 200
    assert len(wl.posts()) == sent and wl.item(11)["purchased"] is True


def test_a_shoppers_reservation_is_never_ours_to_change(client, ids, wl, lego):
    assign(client, lego, [ids.jordan], [])
    wl.item(11).update(reserved_by="Pat", reserved_by_email="pat@example.com")  # an editor moved it on the wishlist
    client.post("/api/wishlist/refresh", json={}, headers=mut())
    assert board(client, ids)["givers"][0]["gifts"][0]["wish"]["in_sync"] is False
    sent = len(wl.posts())
    assert assign(client, lego, [], [ids.jordan]).status_code == 200  # unassigning ours: Pat's reservation is left alone
    assert len(wl.posts()) == sent
    r = assign(client, lego, [ids.casey], [])
    assert r.status_code == 409 and wl.item(11)["reserved_by"] == "Pat"
    wl.item(11).update(reserved_by=None, reserved_by_email=None, purchased=True, purchased_by="Pat",
                       purchased_by_email="pat@example.com")
    client.post("/api/wishlist/refresh", json={}, headers=mut())
    sent = len(wl.posts())
    assert assign(client, lego, [ids.casey], []).status_code == 409   # bought by a shopper: refused without a request
    assert len(wl.posts()) == sent


def test_v1_never_writes_to_the_wishlist(client, ids, wl, lego):
    assign(client, lego, [ids.jordan], [])
    t = mk_token("full", unlocked=True)
    sent = len(wl.requests)
    r = client.post(f"/api/v1/gifts/{lego}/status", json={"status": "confirmed"}, headers=bearer(t))
    assert (r.status_code, r.get_json()) == (409, {"error": "This gift is on the wishlist: change its status in the app."})
    assert gift(client, lego)["status"] == "told"
    assert client.post(f"/api/v1/gifts/{lego}/status", json={"status": "told"}, headers=bearer(t)).status_code == 200
    for scope in ("delivery", "shop"):
        low = mk_token(scope)
        assert client.get(f"/api/v1/gifts/{lego}", headers=bearer(low)).status_code == 404
        assert client.post(f"/api/v1/gifts/{lego}/status", json={"status": "confirmed"}, headers=bearer(low)).status_code in (403, 404)
    assert len(wl.requests) == sent
    full = client.get(f"/api/v1/gifts/{lego}", headers=bearer(t)).get_json()
    assert not {"wish", "wishlist_item_id", "wishlist"} & set(full)


def test_with_the_feature_off_a_linked_gift_is_local_only(client, ids, wl, lego, monkeypatch):
    monkeypatch.setattr(settings, "WISHLIST_URL", "")
    sent = len(wl.requests)
    assert assign(client, lego, [ids.jordan], []).status_code == 200
    assert status(client, lego, "confirmed").status_code == 200
    assert client.delete(f"/api/gifts/{lego}", json={}, headers=mut()).status_code == 200
    assert len(wl.requests) == sent



# ---------------------------------------------------------------- keep last: every request this file made
FORBIDDEN = ("santa", "status", "price", "location", "seller", "order", "ship", "eta", "arrived", "note", "agent",
             "token", "giver", "recipient", "gift", "occasion", "budget")


def test_zz_every_request_of_this_file_was_allowed():
    """Review Focus 1 over the whole file (this test runs last, in file order): every request the stub received went
    to the configured origin on a fixed path with no query, carried the configured token and no other credential,
    and had a body of `by` and `purchased` only, with no word of the tracker's private fields anywhere in it."""
    assert len(REQUESTS) >= 100
    posts = [r for r in REQUESTS if r["method"] == "POST"]
    assert {(r["path"].rsplit("/", 1)[1], r["body"].get("purchased")) for r in posts} >= \
        {("reserve", None), ("purchase", True), ("purchase", False)}
    assert any(r["body"] == {"by": None} for r in posts)
    for r in REQUESTS:
        check_request(r)
        assert r["token"] == TOKEN and r["headers"]["Authorization"] == f"Bearer {TOKEN}"
        if r["raw"] is not None:
            assert set(r["body"]) <= ALLOWED_KEYS
            assert type(r["body"]["by"]) in (str, type(None)) and type(r["body"].get("purchased", False)) is bool
            keys_text = " ".join(r["body"]).lower()
            assert not any(word in keys_text for word in FORBIDDEN), r["body"]
            assert "santa" not in r["raw"].decode().lower()
