"""The agent API (/api/v1): what each scope reads and writes, the audit note every write leaves, and the lock as a
token sees it. The walk over every rule is tests/test_agent_privacy.py."""
import pytest

from conftest import auth, bearer, con_rows, mk_gift, mk_occasion, mk_protected_gift, mk_token, mut, put_plan

T = 1_800_000_000  # the `clock` fixture's instant
SCOPE_403 = {"delivery": {"error": "This token's scope does not allow that.", "scope": "delivery"},
             "shop": {"error": "This token's scope does not allow that.", "scope": "shop"}}
NO_GIFT = (404, {"error": "No such gift."})
ORDER = {"seller": "Camera shop", "order_number": "1234", "ship_to_id": 1, "eta": "2026-12-09"}


def v1(client, token, path, method="GET", body=None, status=200):
    r = client.open(path, method=method, json=body, headers=bearer(token))
    assert r.status_code == status, (path, r.status_code, r.get_json())
    return r.get_json()


def answer(client, token, path, method="GET", body=None):
    r = client.open(path, method=method, json=body, headers=bearer(token))
    return r.status_code, r.get_json()


@pytest.fixture
def shelf(client, ids):
    """Gifts for Jordan from Alex in each ours-track status, and one from Casey (theirs track)."""
    out = {s: mk_gift(client, ids.o, s.title(), to=[ids.jordan], frm=[ids.alex], status=s, store=f"{s} store",
                      url="https://example.com/x", price="10.00", notes="size M")
           for s in ("idea", "decided", "given")}
    r = client.post(f"/api/gifts/{out['decided']}/status", json={"status": "ordered", **ORDER}, headers=mut())
    assert r.status_code == 200
    out["ordered"] = out.pop("decided")
    client.patch(f"/api/gifts/{out['ordered']}", json={"title": "Ordered"}, headers=mut())
    out["decided"] = mk_gift(client, ids.o, "Decided", to=[ids.jordan], frm=[ids.alex], status="decided")
    out["arrived"] = mk_gift(client, ids.o, "Arrived", to=[ids.riley], frm=[ids.alex], status="arrived")
    out["told"] = mk_gift(client, ids.o, "Told", to=[ids.jordan], frm=[ids.casey])
    return out


# ---------------------------------------------------------------- reading
def test_delivery_lists_decided_and_ordered_gifts_with_recipient_names(client, ids, shelf):
    t = mk_token("delivery", "Shipping")
    j = v1(client, t, f"/api/v1/gifts?occasion_id={ids.o}")
    assert [(g["title"], g["status"]) for g in j] == [("Ordered", "ordered"), ("Decided", "decided")]
    assert set(j[0]) == {"id", "occasion_id", "title", "status", "store", "recipients", "seller", "order_number", "ship_to_id",
                         "ship_to", "ship_elsewhere_ok", "eta", "arrived_on", "ordered_by", "ship_warning"}
    assert j[0]["recipients"] == [{"id": ids.jordan, "name": "Jordan"}]
    assert (j[0]["seller"], j[0]["order_number"], j[0]["ship_to"], j[0]["eta"], j[0]["ordered_by"]) == \
        ("Camera shop", "1234", "Home", "2026-12-09", "Alex")
    assert v1(client, t, f"/api/v1/gifts/{shelf['ordered']}") == j[0]
    assert [g["title"] for g in v1(client, t, "/api/v1/gifts?status=arrived")] == ["Arrived"]
    for status in ("idea", "given", "told"):
        assert answer(client, t, f"/api/v1/gifts/{shelf[status]}") == NO_GIFT
    assert answer(client, t, "/api/v1/gifts?status=idea")[0] == 400
    assert answer(client, t, "/api/v1/gifts?status=") == (200, j)


def test_shop_reads_ideas_with_link_price_and_notes(client, ids, shelf):
    t = mk_token("shop", "Buyer")
    j = v1(client, t, "/api/v1/gifts")
    assert [g["title"] for g in j] == ["Idea", "Ordered", "Decided"]
    assert (j[0]["url"], j[0]["price_cents"], j[0]["notes"]) == ("https://example.com/x", 1000, "size M")
    assert not {"santa", "location_id", "location_path", "giver_ids", "created_by", "track"} & set(j[0])
    assert [g["title"] for g in v1(client, t, "/api/v1/gifts?status=idea,arrived")] == ["Idea", "Arrived"]
    assert answer(client, t, "/api/v1/gifts?status=given")[0] == 400
    assert answer(client, t, f"/api/v1/gifts/{shelf['told']}") == NO_GIFT


def test_full_sees_every_status_and_the_web_apps_keys(client, ids, shelf):
    t = mk_token("full", "Owner")
    j = v1(client, t, "/api/v1/gifts")
    assert {g["title"] for g in j} == {"Idea", "Ordered", "Decided", "Given", "Arrived", "Told"}
    one = v1(client, t, f"/api/v1/gifts/{shelf['told']}")
    assert (one["track"], one["giver_ids"], one["recipients"]) == ("theirs", [ids.casey], [{"id": ids.jordan, "name": "Jordan"}])
    assert "location_path" in one and "santa" not in one          # made without `unlocked`: locked


def test_the_list_follows_occasion_person_and_bad_arguments(client, ids, shelf, freeze_today):
    t = mk_token("full", "Owner")
    later = mk_occasion(client, "Birthday", kind="birthday", date="2027-03-01")
    freeze_today("2027-01-01")                                     # now the birthday is the current occasion
    assert v1(client, t, "/api/v1/gifts") == []
    assert len(v1(client, t, f"/api/v1/gifts?occasion_id={ids.o}")) == 6
    assert [g["title"] for g in v1(client, t, f"/api/v1/gifts?occasion_id={ids.o}&person_id={ids.riley}")] == ["Arrived"]
    for q in ("occasion_id=x", "occasion_id=-1", "person_id=1.5", "status=nope"):
        assert answer(client, t, f"/api/v1/gifts?{q}")[0] == 400, q
    assert answer(client, t, "/api/v1/gifts?occasion_id=999")[0] == 404
    assert later


def test_occasions_and_addresses_name_no_person(client, ids):
    put_plan(client, ids.o, ids.kid1, budget_cents=5000)
    t = mk_token("delivery", "Shipping")
    occ = v1(client, t, "/api/v1/occasions")
    assert occ == [{"id": ids.o, "name": "Christmas 2026", "kind": "christmas", "date": "2026-12-25", "archived": False, "current": True}]
    assert v1(client, t, "/api/v1/addresses") == [{"id": 1, "label": "Home", "is_home": True}]


def test_shop_reads_people_and_budgets_of_unprotected_people(client, ids, shelf):
    for pid in (ids.jordan, ids.kid1):
        put_plan(client, ids.o, pid, budget_cents=5000)
    mk_protected_gift(ids, price=7777)
    t = mk_token("shop", "Buyer")
    names = [p["name"] for p in v1(client, t, "/api/v1/people")]
    assert names == ["Alex", "Sam", "Jordan", "Riley", "Casey"]
    b = v1(client, t, f"/api/v1/occasions/{ids.o}/budgets")
    assert b["occasion"]["id"] == ids.o and {p["id"] for p in b["people"]} == {ids.jordan, ids.riley, ids.casey}
    jordan = next(p for p in b["people"] if p["id"] == ids.jordan)
    assert jordan == {"id": ids.jordan, "name": "Jordan", "plan": "individual", "budget_cents": 5000, "spent_cents": 2000,
                      "planned_cents": 1000, "gift_count": 5, "group": "Ungrouped"}
    assert "7777" not in str(b) and "Kid" not in str(b)


# ---------------------------------------------------------------- writing
def test_delivery_records_an_order_and_an_arrival(client, ids, shelf, clock, freeze_today):
    freeze_today("2026-12-08")
    t = mk_token("delivery", "Shipping")
    g = shelf["decided"]
    j = v1(client, t, f"/api/v1/gifts/{g}/order", "PATCH", ORDER)
    assert (j["status"], j["seller"], j["order_number"], j["ship_to_id"], j["eta"], j["ordered_by"]) == \
        ("decided", "Camera shop", "1234", 1, "2026-12-09", None)
    j = v1(client, t, f"/api/v1/gifts/{g}/status", "POST", {"status": "arrived"})
    assert (j["status"], j["arrived_on"]) == ("arrived", "2026-12-08")
    assert con_rows("SELECT t.name, l.at, l.action FROM gift_agent_log l JOIN api_tokens t ON t.id = l.token_id ORDER BY l.id") == \
        [("Shipping", T, "order: seller, order_number, ship_to_id, eta"), ("Shipping", T, "status: arrived")]
    j = v1(client, t, f"/api/v1/gifts/{g}/order", "PATCH", {"arrived_on": "2026-12-07"})   # correct the date
    assert j["arrived_on"] == "2026-12-07"


def test_the_web_gift_shows_the_agent_notes(client, ids, shelf, clock):
    t = mk_token("delivery", "Shipping")
    v1(client, t, f"/api/v1/gifts/{shelf['decided']}/order", "PATCH", {"eta": "2026-12-10"})
    clock.advance(5)
    v1(client, t, f"/api/v1/gifts/{shelf['decided']}/status", "POST", {"status": "arrived"})
    j = client.get(f"/api/gifts/{shelf['decided']}", headers=auth()).get_json()
    assert j["agent_log"] == [{"at": T + 5, "token": "Shipping", "action": "status: arrived"},
                              {"at": T, "token": "Shipping", "action": "order: eta"}]
    assert client.get(f"/api/gifts/{shelf['idea']}", headers=auth()).get_json()["agent_log"] == []


def test_order_patch_takes_only_order_fields(client, ids, shelf):
    t = mk_token("full", "Owner")
    g = shelf["decided"]
    before = con_rows("SELECT * FROM gifts WHERE id = ?", g)
    assert answer(client, t, f"/api/v1/gifts/{g}/order", "PATCH", {"title": "x", "seller": "S"}) == \
        (400, {"error": "Only order fields can be written here: seller, order_number, ship_to_id, ship_elsewhere_ok, eta, arrived_on."})
    assert answer(client, t, f"/api/v1/gifts/{g}/order", "PATCH", {"santa": True})[0] == 400
    assert answer(client, t, f"/api/v1/gifts/{g}/order", "PATCH", {})[0] == 400
    assert answer(client, t, f"/api/v1/gifts/{g}/order", "PATCH", {"eta": "12/9/2026"})[0] == 400
    assert answer(client, t, f"/api/v1/gifts/{g}/status", "POST", {"status": "ordered", "title": "x"})[0] == 400
    assert con_rows("SELECT * FROM gifts WHERE id = ?", g) == before
    assert con_rows("SELECT COUNT(*) FROM gift_agent_log") == [(0,)]


def test_scope_refusals_are_403_and_change_nothing(client, ids, shelf):
    d, s = mk_token("delivery", "Shipping"), mk_token("shop", "Buyer")
    g = shelf["decided"]
    before = con_rows("SELECT * FROM gifts ORDER BY id")
    for status in ("ordered", "wrapped", "given", "idea"):
        assert answer(client, d, f"/api/v1/gifts/{g}/status", "POST", {"status": status, **ORDER}) == (403, SCOPE_403["delivery"])
    for status in ("wrapped", "given", "decided", "idea"):
        assert answer(client, s, f"/api/v1/gifts/{g}/status", "POST", {"status": status}) == (403, SCOPE_403["shop"])
    for path in ("/api/v1/people", f"/api/v1/occasions/{ids.o}/budgets"):
        assert answer(client, d, path) == (403, SCOPE_403["delivery"])
    body = {"title": "New", "recipient_ids": [ids.jordan]}
    assert answer(client, d, "/api/v1/gifts", "POST", body) == (403, SCOPE_403["delivery"])
    assert answer(client, s, "/api/v1/gifts", "POST", body) == (403, SCOPE_403["shop"])
    assert con_rows("SELECT * FROM gifts ORDER BY id") == before
    assert con_rows("SELECT COUNT(*) FROM gift_agent_log") == [(0,)]


def test_shop_marks_ordered_with_the_web_apps_rule(client, ids, shelf):
    s = mk_token("shop", "Buyer")
    g = shelf["idea"]
    assert answer(client, s, f"/api/v1/gifts/{g}/status", "POST", {"status": "ordered"}) == \
        (400, {"error": "Marking a gift ordered needs seller, ship_to_id, eta.", "missing": ["seller", "ship_to_id", "eta"]})
    assert con_rows("SELECT status, ordered_by FROM gifts WHERE id = ?", g) == [("idea", None)]
    j = v1(client, s, f"/api/v1/gifts/{g}/status", "POST", {"status": "ordered", **ORDER})
    assert (j["status"], j["seller"], j["ordered_by"]) == ("ordered", "Camera shop", "Buyer")
    assert con_rows("SELECT action FROM gift_agent_log") == [("status: ordered",)]
    theirs = shelf["told"]                                          # not in shop's statuses: 404, not 403
    assert answer(client, s, f"/api/v1/gifts/{theirs}/status", "POST", {"status": "ordered", **ORDER}) == NO_GIFT


def test_full_creates_a_gift(client, ids, clock):
    t = mk_token("full", "Owner")
    r = client.post("/api/v1/gifts", json={"title": "Scarf", "recipient_ids": [ids.jordan], "giver_ids": [ids.alex],
                                            "price": "25.00", "url": "https://example.com/scarf"}, headers=bearer(t))
    assert r.status_code == 201, r.get_json()
    j = r.get_json()
    assert (j["occasion_id"], j["title"], j["status"], j["price_cents"], j["created_by"]) == (ids.o, "Scarf", "idea", 2500, "agent:Owner")
    assert con_rows("SELECT action FROM gift_agent_log WHERE gift_id = ?", j["id"]) == [("created",)]
    assert answer(client, t, "/api/v1/gifts", "POST", {"title": ""})[0] == 400
    v1(client, t, f"/api/v1/gifts/{j['id']}/status", "POST", {"status": "given"})


def test_a_full_token_without_unlocked_is_locked(client, ids):
    secret = mk_protected_gift(ids)
    t = mk_token("full", "Owner")
    assert answer(client, t, f"/api/v1/gifts/{secret}") == NO_GIFT
    assert answer(client, t, f"/api/v1/gifts/{secret}/order", "PATCH", {"seller": "x"}) == NO_GIFT
    assert answer(client, t, f"/api/v1/gifts/{secret}/status", "POST", {"status": "given"}) == NO_GIFT
    for body in ({"title": "x", "recipient_ids": [ids.kid1]}, {"title": "x", "recipient_ids": [ids.jordan, ids.kid2]},
                 {"title": "x", "recipient_ids": [ids.jordan], "giver_ids": [ids.kid3]}):
        assert answer(client, t, "/api/v1/gifts", "POST", body) == (400, {"error": "No such person."})
    assert answer(client, t, "/api/v1/gifts", "POST", {"title": "x", "recipient_ids": [ids.jordan], "santa": False})[0] == 403
    assert "Kid" not in str(v1(client, t, "/api/v1/people"))
    assert all("santa" not in g for g in v1(client, t, "/api/v1/gifts"))
    assert con_rows("SELECT COUNT(*) FROM gifts") == [(1,)]


def test_a_full_token_made_unlocked_sees_protected_gifts_and_santa(client, ids):
    secret = mk_protected_gift(ids, santa=True)
    t = mk_token("full", "Owner", unlocked=True)
    j = v1(client, t, f"/api/v1/gifts/{secret}")
    assert (j["title"], j["santa"], j["recipients"]) == ("ZZ-SECRET-GIFT", True, [{"id": ids.kid1, "name": "Kid 1"}])
    assert [g["id"] for g in v1(client, t, "/api/v1/gifts")] == [secret]
    assert "Kid 1" in str(v1(client, t, "/api/v1/people"))
    j = client.post("/api/v1/gifts", json={"title": "Bike", "recipient_ids": [ids.kid2], "giver_ids": [ids.alex], "santa": True},
                    headers=bearer(t)).get_json()
    assert j["santa"] is True


def test_v1_404s_look_alike(client, ids, shelf):
    secret = mk_protected_gift(ids, status="decided")
    d = mk_token("delivery", "Shipping")
    for gid in (secret, shelf["idea"], 999999):
        for method, path, body in (("GET", "", None), ("PATCH", "/order", {"seller": "x"}), ("POST", "/status", {"status": "arrived"})):
            assert answer(client, d, f"/api/v1/gifts/{gid}{path}", method, body) == NO_GIFT, (gid, path)


# ---------------------------------------------------------------- the lock and the browser
def test_v1_ignores_the_unlock_cookie(client, ids, unlock):
    secret = mk_protected_gift(ids)
    unlock()                                                     # this client now holds a valid unlock cookie
    assert client.get(f"/api/gifts/{secret}", headers=auth()).status_code == 200
    t = mk_token("full", "Owner")                                # full, but not made unlocked
    r = client.get(f"/api/v1/gifts/{secret}", headers=bearer(t))
    assert (r.status_code, r.get_json()) == NO_GIFT
    assert client.get("/api/v1/me", headers=bearer(t)).get_json()["unlocked"] is False
    assert "Set-Cookie" not in client.get("/api/v1/gifts", headers=bearer(t)).headers


def test_v1_mutations_need_json_but_no_csrf_header(client, ids):
    t = mk_token("delivery", "Shipping")
    g = mk_gift(client, ids.o, "Lamp", to=[ids.jordan], frm=[ids.alex], status="decided")
    r = client.patch(f"/api/v1/gifts/{g}/order", json={"seller": "Lamp shop"}, headers=bearer(t))
    assert r.status_code == 200 and r.get_json()["seller"] == "Lamp shop"
    r = client.patch(f"/api/v1/gifts/{g}/order", data="seller=x", headers=bearer(t))
    assert (r.status_code, r.get_json()) == (400, {"error": "Bad request."})
    r = client.patch(f"/api/v1/gifts/{g}/order", json=["x"], headers=bearer(t))
    assert r.status_code == 400
    assert con_rows("SELECT seller FROM gifts") == [("Lamp shop",)]
