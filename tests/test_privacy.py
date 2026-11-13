"""While locked, a gift with any protected recipient does not exist as far as any response is concerned, and no
response carries a Santa flag. These tests walk app.url_map, so a route added later is covered without anyone
remembering to add a test here: give its path arguments a value in ARGS and it is walked.

Fixture data: three protected gifts with distinctive markers, one of them shared with the unprotected Jordan and
from an owner with Santa set; one visible gift that also has Santa set, as a control."""
import itertools
import types

import pytest
from flask import url_for

import app as tracker
from conftest import PIN, auth, con_rows, mk_gift, mk_protected_gift, mut, set_pin

MARKERS = ("zz-secret", "santa", "38888", "38889", "77777")   # lowercased; the last three are the shared gift's price and shares


@pytest.fixture
def world(client, ids, clock):
    """The fixture data above, created at a fixed time (clock) so no timestamp can contain a marker by chance."""
    group = client.post("/api/groups", json={"name": "Side A"}, headers=mut()).get_json()["id"]
    shared = mk_protected_gift(ids, title="ZZ-SECRET-GIFT", to=[ids.kid1, ids.jordan], frm=[ids.alex, ids.sam],
                               santa=True, price=77777, status="arrived", url="https://example.com/zz-secret-url",
                               store="ZZ-SECRET-STORE", notes="ZZ-SECRET-NOTE")
    theirs = mk_protected_gift(ids, title="ZZ-SECRET-THEIRS", to=[ids.kid2], frm=[ids.casey])
    idea = mk_protected_gift(ids, title="ZZ-SECRET-IDEA", to=[ids.kid3], frm=[])
    visible = mk_gift(client, ids.o, "VISIBLE-GIFT", to=[ids.jordan], frm=[ids.alex], price="12.34")
    # Santa on the visible gift goes in directly: through the API it needs the unlock.
    import db
    import settings
    c = db.connect(settings.DB_PATH)
    c.execute("UPDATE gifts SET santa = 1 WHERE id = ?", (visible,))
    c.close()
    return types.SimpleNamespace(ids=ids, group=group, shared=shared, theirs=theirs, idea=idea, visible=visible,
                                 secret=(shared, theirs, idea))


def args(w):
    """Candidate values for every path argument name used under /api/. A new argument name must be added here."""
    i = w.ids
    return {
        "gift_id": [*w.secret, w.visible, 999999],
        "occasion_id": [i.o],
        "person_id": [i.kid1, i.kid2, i.kid3, i.jordan, i.casey, i.alex],
        "group_id": [w.group],
    }


def api_rules(methods):
    return [r for r in tracker.app.url_map.iter_rules() if r.rule.startswith("/api/") and r.methods & methods]


def urls(rule, w):
    pool = args(w)
    missing = rule.arguments - pool.keys()
    if missing:
        pytest.fail(f"{rule.rule}: add a value for {sorted(missing)} to args() in tests/test_privacy.py")
    names = sorted(rule.arguments)
    with tracker.app.test_request_context():
        for combo in itertools.product(*(pool[n] for n in names)):
            values = dict(zip(names, combo))
            yield url_for(rule.endpoint, **values), values


def leaks(text):
    low = text.lower()
    return [m for m in MARKERS if m in low]


def walk_gets(client, w, query=("", "?inactive=1")):
    """Every GET under /api/ with every argument combination; returns {url: response text}."""
    out = {}
    for rule in api_rules({"GET"}):
        for url, _ in urls(rule, w):
            for q in query:
                r = client.get(url + q, headers=auth())
                assert r.status_code < 500, (url, r.status_code)
                out[url + q] = r.get_data(as_text=True)
    return out


def protected_state():
    """Everything about protected gifts and who is protected. A locked request must leave all of it unchanged."""
    prot = ("SELECT g.id FROM gifts g WHERE EXISTS (SELECT 1 FROM gift_recipients r JOIN people p ON p.id = r.person_id "
            "WHERE r.gift_id = g.id AND p.protected = 1)")
    return (
        con_rows(f"SELECT * FROM gifts WHERE id IN ({prot}) ORDER BY id"),
        con_rows(f"SELECT * FROM gift_recipients WHERE gift_id IN ({prot}) ORDER BY gift_id, person_id"),
        con_rows(f"SELECT * FROM gift_givers WHERE gift_id IN ({prot}) ORDER BY gift_id, person_id"),
        con_rows("SELECT id FROM people WHERE protected = 1 ORDER BY id"),
    )


# ---------------------------------------------------------------- reading
def test_no_api_route_leaks_protected_data_when_locked(client, world):
    """Walk every GET rule under /api/ in app.url_map, fill path arguments from `ids`, and assert the response
    text contains neither the protected gift's title ("ZZ-SECRET-GIFT") nor the key "santa"."""
    seen = walk_gets(client, world)
    assert len({u.split("?")[0] for u in seen}) >= 9
    for url, text in seen.items():
        assert not leaks(text), (url, leaks(text))
    # The walk does reach gift data: the visible gift shows up, so the absence above means something.
    assert any("VISIBLE-GIFT" in t for t in seen.values())


def test_the_walk_would_catch_a_leak(client, world, unlock):
    """Control: unlocked, the same walk does see the protected gift and the Santa flag."""
    unlock()
    seen = walk_gets(client, world, query=("",))
    assert any("ZZ-SECRET-GIFT" in t for t in seen.values())
    assert any('"santa"' in t for t in seen.values())


def test_every_api_get_rule_has_values_for_its_arguments(client, world):
    for rule in api_rules({"GET"}) + api_rules({"POST", "PUT", "PATCH", "DELETE"}):
        list(urls(rule, world))


def test_protected_gift_404_is_the_same_as_a_missing_gift(client, world):
    for g in world.secret:
        r = client.get(f"/api/gifts/{g}", headers=auth())
        missing = client.get("/api/gifts/999999", headers=auth())
        assert (r.status_code, r.get_json()) == (missing.status_code, missing.get_json()) == (404, {"error": "No such gift."})


def test_shared_gift_is_hidden_from_the_unprotected_co_recipient(client, world):
    i = world.ids
    lst = client.get(f"/api/occasions/{i.o}/people/{i.jordan}/gifts", headers=auth()).get_json()
    assert [g["id"] for g in lst] == [world.visible]
    h = client.get(f"/api/people/{i.jordan}/history", headers=auth()).get_json()
    assert [g["id"] for g in h["gifts"]] == [world.visible]
    s = client.get(f"/api/occasions/{i.o}/summary", headers=auth()).get_json()
    jordan = next(p for g in s["groups"] for p in g["people"] if p["id"] == i.jordan)
    assert (jordan["spent_cents"], jordan["planned_cents"], jordan["gift_count"], jordan["headline"]) == (0, 1234, 1, "VISIBLE-GIFT")


def test_protected_people_lists_are_empty_and_the_summary_section_is_locked(client, world):
    i = world.ids
    for kid in (i.kid1, i.kid2, i.kid3):
        assert client.get(f"/api/occasions/{i.o}/people/{kid}/gifts", headers=auth()).get_json() == []
        assert client.get(f"/api/people/{kid}/history", headers=auth()).get_json()["gifts"] == []
    s = client.get(f"/api/occasions/{i.o}/summary", headers=auth()).get_json()
    assert s["protected"] == {"locked": True, "people": []}
    assert not {p["id"] for g in s["groups"] for p in g["people"]} & {i.kid1, i.kid2, i.kid3}


def test_people_still_lists_protected_people_while_locked(client, world):
    names = [p["name"] for p in client.get("/api/people", headers=auth()).get_json()]
    assert {"Kid 1", "Kid 2", "Kid 3"} <= set(names)


def test_an_inactive_protected_person_is_still_protected(client, world):
    i = world.ids
    assert client.patch(f"/api/people/{i.kid1}", json={"active": False}, headers=mut()).status_code == 200
    assert client.get(f"/api/gifts/{world.shared}", headers=auth()).status_code == 404
    assert not any(leaks(t) for t in walk_gets(client, world, query=("", "?inactive=1")).values())


# ---------------------------------------------------------------- writing
HOSTILE = [
    # tries to make a gift protected, or to change a protected one, or to unprotect a person
    {"title": "pwned", "name": "pwned", "status": "given", "recipient_ids": "KID", "giver_ids": "CASEY",
     "expected_giver_ids": [], "occasion_id": "O", "protected": False, "price": "1", "plan": "skip",
     "budget_cents": 1, "kind": "other", "date": "2026-12-25"},
    # tries to move a protected gift out from behind the lock
    {"title": "pwned", "recipient_ids": "JORDAN", "giver_ids": [], "protected": None, "status": "told"},
    {"santa": False, "active": False},
    {},
]


def _fill(body, i):
    sub = {"KID": [i.kid1], "CASEY": [i.casey], "JORDAN": [i.jordan], "O": i.o}
    return {k: sub.get(v, v) if isinstance(v, str) else v for k, v in body.items()}


def test_no_locked_mutation_changes_or_reveals_a_protected_gift(client, world):
    """Walk every non-GET rule under /api/ with every argument combination and several hostile bodies. Nothing
    about protected gifts or the protected flag may change, no response may leak, and a request aimed at a
    protected gift is refused as if the gift did not exist (404) or as locked (423)."""
    before = protected_state()
    sent = 0
    for rule in api_rules({"POST", "PUT", "PATCH", "DELETE"}):
        for method in sorted(rule.methods & {"POST", "PUT", "PATCH", "DELETE"}):
            for url, values in urls(rule, world):
                for body in HOSTILE:
                    r = client.open(url, method=method, json=_fill(body, world.ids), headers=mut())
                    sent += 1
                    assert r.status_code < 500, (method, url, r.status_code)
                    assert not leaks(r.get_data(as_text=True)), (method, url)
                    if values.get("gift_id") in world.secret:
                        assert r.status_code in (404, 423), (method, url, r.status_code)
                    assert protected_state() == before, (method, url, body)
    assert sent > 50
    assert not any(leaks(t) for t in walk_gets(client, world).values())


def test_each_write_to_a_protected_gift_is_404(client, world):
    for g in world.secret:
        assert client.patch(f"/api/gifts/{g}", json={"title": "x"}, headers=mut()).status_code == 404
        assert client.patch(f"/api/gifts/{g}", json={"title": ""}, headers=mut()).status_code == 404   # 404 before 400
        assert client.post(f"/api/gifts/{g}/status", json={"status": "given"}, headers=mut()).status_code == 404
        assert client.delete(f"/api/gifts/{g}", headers=mut()).status_code == 404
    assert len(con_rows("SELECT id FROM gifts")) == 4


def test_creating_a_gift_with_any_protected_recipient_is_423(client, world):
    i = world.ids
    for to in ([i.kid1], [i.jordan, i.kid2]):
        r = client.post("/api/gifts", json={"occasion_id": i.o, "title": "x", "recipient_ids": to}, headers=mut())
        assert r.status_code == 423
    assert len(con_rows("SELECT id FROM gifts")) == 4


def test_adding_a_protected_recipient_to_a_visible_gift_is_423(client, world):
    i = world.ids
    r = client.patch(f"/api/gifts/{world.visible}", json={"recipient_ids": [i.jordan, i.kid1]}, headers=mut())
    assert r.status_code == 423
    assert con_rows("SELECT person_id FROM gift_recipients WHERE gift_id = ?", world.visible) == [(i.jordan,)]


@pytest.mark.parametrize("value", [False, None])
def test_unprotecting_a_person_is_423_while_locked(client, world, value):
    """protected: null would store 0, so it is an unprotect too."""
    i = world.ids
    r = client.patch(f"/api/people/{i.kid1}", json={"protected": value}, headers=mut())
    assert (r.status_code, r.get_json()) == (423, {"error": "Locked", "locked": True})
    assert con_rows("SELECT protected FROM people WHERE id = ?", i.kid1) == [(1,)]


def test_protecting_a_person_only_hides_more_so_it_works_while_locked(client, world):
    i = world.ids
    assert client.patch(f"/api/people/{i.jordan}", json={"protected": True}, headers=mut()).status_code == 200
    assert client.get(f"/api/gifts/{world.visible}", headers=auth()).status_code == 404
    assert client.post("/api/people", json={"name": "Kid 4", "protected": True}, headers=mut()).status_code == 201


def test_restating_the_protected_flag_unchanged_is_allowed_while_locked(client, world):
    i = world.ids
    assert client.patch(f"/api/people/{i.kid1}", json={"protected": True, "color": "#123456"}, headers=mut()).status_code == 200
    assert client.patch(f"/api/people/{i.jordan}", json={"protected": False, "name": "Jordan"}, headers=mut()).status_code == 200
    assert client.post("/api/people", json={"name": "Pat", "protected": False}, headers=mut()).status_code == 201


def test_deleting_a_person_needs_the_unlock(client, world, unlock):
    """Otherwise 409 "has gifts" against 200 would tell whether a hidden gift involves them."""
    i = world.ids
    spare = client.post("/api/people", json={"name": "Spare"}, headers=mut()).get_json()["id"]
    for pid in (i.casey, i.kid3, spare):
        r = client.delete(f"/api/people/{pid}", headers=mut())
        assert (r.status_code, r.get_json()) == (423, {"error": "Locked", "locked": True}), pid
    unlock()
    assert client.delete(f"/api/people/{spare}", headers=mut()).status_code == 200
    assert client.delete(f"/api/people/{i.casey}", headers=mut()).status_code == 409


def test_unlocked_writes_to_protected_gifts_work(client, world, unlock):
    unlock()
    g = world.shared
    assert client.patch(f"/api/gifts/{g}", json={"title": "Trip"}, headers=mut()).get_json()["title"] == "Trip"
    assert client.post(f"/api/gifts/{g}/status", json={"status": "wrapped"}, headers=mut()).status_code == 200
    assert client.delete(f"/api/gifts/{g}", headers=mut()).status_code == 200


def test_a_second_owner_must_unlock_for_themselves(client, world, unlock):
    unlock("alex@example.com")
    set_pin(client, PIN, "sam@example.com")
    assert client.get(f"/api/gifts/{world.shared}", headers=auth("sam@example.com")).status_code == 404
