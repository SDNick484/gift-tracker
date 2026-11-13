"""The second privacy boundary: a token below `full`, and a `full` token made without `unlocked`, is locked for good.
On /api/v1 a gift with any protected recipient does not exist, no protected person is named, and no response carries a
`santa` key. Like tests/test_privacy.py these tests walk app.url_map, so an /api/v1 route added later is walked too:
give a new path argument name values in ARGS.

Fixture data: protected gifts in every status a scope can read, with distinctive markers in the title, store, seller,
order number, notes, link and hiding place, one of them shared with the unprotected Jordan and from an owner with
Santa set; and a visible gift in each status, one with Santa set, as controls."""
import itertools
import types

import pytest
from flask import url_for

import agent
import app as tracker
import db
import settings
from conftest import bearer, con_rows, mk_gift, mk_protected_gift, mk_token, mut
from test_privacy import protected_state

MARKERS = ("zz-", "santa", "kid 1", "kid 2", "kid 3", "77777")  # lowercased
LOCKED = ("delivery", "shop", "full")                            # full made without `unlocked`
QUERIES = ["", *(f"?status={s}" for s in agent.ALL_STATUSES), "?status=" + ",".join(agent.ALL_STATUSES)]


def secret_gift(ids, status, to, **fields):
    return mk_protected_gift(ids, title=f"ZZ-SECRET-{status.upper()}", to=to, status=status, store="ZZ-STORE",
                             url="https://example.com/zz-link", notes="ZZ-NOTE", price=77777, **fields)


@pytest.fixture
def world(client, ids, clock):
    c = db.connect(settings.DB_PATH)
    place = c.execute("INSERT INTO locations (name) VALUES ('ZZ-PLACE')").lastrowid
    c.close()
    secret = [secret_gift(ids, "arrived", [ids.kid1, ids.jordan], frm=[ids.alex, ids.sam], santa=True),
              *(secret_gift(ids, s, [ids.kid2]) for s in ("idea", "decided", "ordered", "wrapped", "given"))]
    secret.append(mk_protected_gift(ids, title="ZZ-SECRET-THEIRS", to=[ids.kid3], frm=[ids.casey]))
    c = db.connect(settings.DB_PATH)
    c.execute("UPDATE gifts SET seller = 'ZZ-SELLER', order_number = 'ZZ-ORDER', ship_to_id = 1, eta = '2026-12-09', "
              "location_id = ? WHERE title LIKE 'ZZ-%'", (place,))
    visible = [mk_gift(client, ids.o, f"VISIBLE-{s.upper()}", to=[ids.jordan], frm=[ids.alex], status=s)
               for s in ("idea", "decided", "arrived")]
    c.execute("UPDATE gifts SET santa = 1 WHERE id = ?", (visible[1],))  # through the API it needs the unlock
    c.close()
    return types.SimpleNamespace(ids=ids, secret=tuple(secret), visible=tuple(visible))


def args(w):
    """Values for every path argument name used under /api/v1. A new argument name must be added here."""
    return {"gift": [*w.secret, *w.visible, 999999], "occasion": [w.ids.o, 999999]}


def v1_rules(methods):
    return [r for r in tracker.app.url_map.iter_rules() if r.rule.startswith("/api/v1/") and r.methods & methods]


def urls(rule, w):
    pool = args(w)
    missing = rule.arguments - pool.keys()
    if missing:
        pytest.fail(f"{rule.rule}: add a value for {sorted(missing)} to args() in tests/test_agent_privacy.py")
    names = sorted(rule.arguments)
    with tracker.app.test_request_context():
        for combo in itertools.product(*(pool[n] for n in names)):
            values = dict(zip(names, combo))
            yield url_for(rule.endpoint, **values), values


def leaks(text):
    low = text.lower()
    return [m for m in MARKERS if m in low]


def walk_gets(client, w, token):
    out = {}
    for rule in v1_rules({"GET"}):
        for url, _ in urls(rule, w):
            for q in QUERIES + [f"?person_id={w.ids.kid1}", f"?person_id={w.ids.jordan}"]:
                r = client.get(url + q, headers=bearer(token))
                assert r.status_code < 500, (url + q, r.status_code)
                out[url + q] = r.get_data(as_text=True)
    return out


def tokens_for(scope):
    return mk_token(scope, f"{scope} walker")


# ---------------------------------------------------------------- reading
@pytest.mark.parametrize("scope", LOCKED)
def test_no_v1_route_leaks_to_a_locked_token(client, world, scope):
    seen = walk_gets(client, world, tokens_for(scope))
    assert len({u.split("?")[0] for u in seen}) >= 8
    for url, text in seen.items():
        assert not leaks(text), (scope, url, leaks(text))
    assert any("VISIBLE-DECIDED" in t for t in seen.values())  # the walk does reach gift data


@pytest.mark.parametrize("scope", LOCKED)
def test_a_protected_gift_is_404_like_a_missing_one(client, world, scope):
    t = tokens_for(scope)
    missing = client.get("/api/v1/gifts/999999", headers=bearer(t))
    for g in world.secret:
        r = client.get(f"/api/v1/gifts/{g}", headers=bearer(t))
        assert (r.status_code, r.get_json()) == (missing.status_code, missing.get_json()) == (404, {"error": "No such gift."})


def test_the_walk_would_catch_a_leak(client, world):
    """Control: a full token made unlocked does see the protected gifts, the people and the Santa flag."""
    seen = walk_gets(client, world, mk_token("full", "Owner", unlocked=True))
    text = "\n".join(seen.values())
    assert "ZZ-SECRET-ARRIVED" in text and "ZZ-SELLER" in text and "ZZ-PLACE" in text and "Kid 1" in text
    assert '"santa"' in text


# ---------------------------------------------------------------- writing
HOSTILE = [
    {"status": "arrived"}, {"status": "given"}, {"status": "ordered", "seller": "pwned", "ship_to_id": 1, "eta": "2026-12-10"},
    {"seller": "pwned", "order_number": "pwned", "eta": "2026-12-10", "arrived_on": "2026-12-11", "ship_elsewhere_ok": True},
    {"title": "pwned", "recipient_ids": "KID", "santa": True}, {"title": "pwned", "recipient_ids": "JORDAN", "giver_ids": "KID"},
    {"title": "pwned", "recipient_ids": "KIDJORDAN", "santa": False}, {"location_id": None, "santa": False}, {},
]


def _fill(body, i):
    sub = {"KID": [i.kid1], "JORDAN": [i.jordan], "KIDJORDAN": [i.jordan, i.kid2]}
    return {k: sub.get(v, v) if isinstance(v, str) else v for k, v in body.items()}


@pytest.mark.parametrize("scope", LOCKED)
def test_no_v1_write_reaches_a_protected_gift(client, world, scope):
    t = tokens_for(scope)
    before = protected_state()
    sent = 0
    for rule in v1_rules({"POST", "PUT", "PATCH", "DELETE"}):
        for method in sorted(rule.methods & {"POST", "PUT", "PATCH", "DELETE"}):
            for url, values in urls(rule, world):
                for body in HOSTILE:
                    r = client.open(url, method=method, json=_fill(body, world.ids), headers=bearer(t))
                    sent += 1
                    assert r.status_code < 500, (method, url, r.status_code)
                    assert not leaks(r.get_data(as_text=True)), (scope, method, url, body)
                    if values.get("gift") in world.secret:
                        assert (r.status_code, r.get_json()) == (404, {"error": "No such gift."}), (method, url, body)
                    assert protected_state() == before, (scope, method, url, body)
    assert sent > 100
    assert con_rows("SELECT COUNT(*) FROM gift_agent_log l JOIN gifts g ON g.id = l.gift_id WHERE g.title LIKE 'ZZ-%'") == [(0,)]
    assert not any(leaks(t) for t in walk_gets(client, world, t).values())


def test_every_v1_rule_is_walked(client, world):
    rules = v1_rules({"GET", "POST", "PUT", "PATCH", "DELETE"})
    assert {r.endpoint for r in rules} >= {"v1_me", "v1_occasions", "v1_addresses", "v1_people", "v1_budgets", "v1_gifts",
                                           "v1_gift_create", "v1_gift", "v1_gift_order", "v1_gift_status"}
    for rule in rules:
        assert list(urls(rule, world))


def test_the_browser_walk_gets_nothing_from_v1(client, world):
    """tests/test_privacy.py walks /api/v1 too, with a Cloudflare identity and no token: always 401, and nothing changes."""
    before = protected_state()
    for rule in v1_rules({"GET", "POST", "PATCH"}):
        for method in sorted(rule.methods & {"GET", "POST", "PATCH"}):
            for url, _ in urls(rule, world):
                r = client.open(url, method=method, json={"status": "given"}, headers=mut())
                assert (r.status_code, r.get_json()) == (401, {"error": "Unauthorized"}), (method, url)
    assert protected_state() == before
