"""Gift exchanges: the exchanges of an occasion, who in this household drew whom, the gift for each draw, the over-budget
flag, carry-forward, and above all the lock: a draw whose drawer or recipient is protected, and any gift linked to
it, does not exist while locked. School mode has exchanges but no lock and no protected drawer."""
import types

import pytest
import test_privacy
from conftest import auth, bearer, con_rows, mk_gift, mk_occasion, mk_token, mut

import agent
import exchanges
import gifts

LOCKED = {"error": "Locked", "locked": True}


def post(client, url, body=None):
    return client.post(url, json=body or {}, headers=mut())


def put_draw(client, ex, drawer, recipient, **kw):
    return client.put(f"/api/exchanges/{ex}/draws/{drawer}", json={"recipient_id": recipient}, headers=mut(**kw))


def mk_exchange(client, ids, name="Secret Santa", budget=2500, date="2026-12-20", occasion=None):
    r = post(client, f"/api/occasions/{occasion or ids.o}/exchanges", {"name": name, "budget_cents": budget, "date": date})
    assert r.status_code == 201, r.get_json()
    return r.get_json()["id"]


def listing(client, ids, occasion=None, **kw):
    return client.get(f"/api/occasions/{occasion or ids.o}/exchanges", headers=auth(**kw)).get_json()


def draws_of(client, ids, ex):
    return {(d["drawer_id"], d["recipient_id"]) for e in listing(client, ids) if e["id"] == ex for d in e["draws"]}


# ---------------------------------------------------------------- exchanges
def test_create_and_list_an_exchange(client, ids):
    r = post(client, f"/api/occasions/{ids.o}/exchanges", {"name": " Secret Santa ", "budget_cents": 2500, "date": "2026-12-20"})
    want = {"id": 1, "name": "Secret Santa", "budget_cents": 2500, "date": "2026-12-20", "draws": []}
    assert (r.status_code, r.get_json()) == (201, want)
    assert listing(client, ids) == [want]
    bare = post(client, f"/api/occasions/{ids.o}/exchanges", {"name": "Favorite Things"}).get_json()
    assert (bare["budget_cents"], bare["date"]) == (None, None)


@pytest.mark.parametrize("body", [
    {}, {"name": ""}, {"name": "x" * 101}, {"name": 5},
    {"name": "A", "budget_cents": -1}, {"name": "A", "budget_cents": 1.5}, {"name": "A", "budget_cents": True},
    {"name": "A", "budget_cents": "5"}, {"name": "A", "budget_cents": 10_000_001},
    {"name": "A", "date": "2026-13-01"}, {"name": "A", "date": 5}, {"name": "A", "date": "12/20"},
])
def test_bad_exchange_bodies_are_400(client, ids, body):
    assert post(client, f"/api/occasions/{ids.o}/exchanges", body).status_code == 400
    assert con_rows("SELECT id FROM exchanges") == []


def test_exchange_routes_answer_404_for_what_is_missing(client, ids):
    assert post(client, "/api/occasions/999999/exchanges", {"name": "A"}).status_code == 404
    assert client.get("/api/occasions/999999/exchanges", headers=auth()).status_code == 404
    assert client.patch("/api/exchanges/999999", json={"name": "A"}, headers=mut()).status_code == 404
    assert put_draw(client, 999999, ids.alex, ids.jordan).status_code == 404


def test_update_an_exchange(client, ids):
    ex = mk_exchange(client, ids)
    r = client.patch(f"/api/exchanges/{ex}", json={"name": "Office", "budget_cents": None, "date": ""}, headers=mut())
    assert (r.status_code, r.get_json()) == (200, {"id": ex, "name": "Office", "budget_cents": None, "date": None, "draws": []})
    assert client.patch(f"/api/exchanges/{ex}", json={"name": ""}, headers=mut()).status_code == 400
    assert client.patch(f"/api/exchanges/{ex}", json={"budget_cents": -5}, headers=mut()).status_code == 400
    assert con_rows("SELECT name FROM exchanges") == [("Office",)]


# ---------------------------------------------------------------- draws
def test_a_draw_is_an_owner_or_a_protected_person(client, ids, unlock):
    unlock()
    ex = mk_exchange(client, ids)
    assert put_draw(client, ex, ids.alex, ids.jordan).status_code == 200
    assert put_draw(client, ex, ids.kid1, ids.riley).status_code == 200
    assert put_draw(client, ex, ids.jordan, ids.riley).status_code == 400            # not in this household
    assert put_draw(client, ex, 999999, ids.riley).status_code == 404
    assert put_draw(client, ex, ids.alex, 999999).status_code == 400
    assert put_draw(client, ex, ids.alex, ids.alex).status_code == 400
    assert client.put(f"/api/exchanges/{ex}/draws/{ids.alex}", json={}, headers=mut()).status_code == 400
    assert client.put(f"/api/exchanges/{ex}/draws/{ids.alex}", json={"recipient_id": "3"}, headers=mut()).status_code == 400
    assert con_rows("SELECT drawer_id, recipient_id FROM exchange_draws ORDER BY drawer_id") == \
        [(ids.alex, ids.jordan), (ids.kid1, ids.riley)]


def test_a_draw_can_be_replaced_and_removed(client, ids):
    ex = mk_exchange(client, ids)
    put_draw(client, ex, ids.alex, ids.jordan)
    r = put_draw(client, ex, ids.alex, ids.riley)
    assert r.get_json()["draws"] == [{"drawer_id": ids.alex, "recipient_id": ids.riley, "gift_id": None, "gift_title": None,
                                      "gift_status": None, "price_cents": None, "over_budget": False}]
    assert client.delete(f"/api/exchanges/{ex}/draws/{ids.alex}", json={}, headers=mut()).get_json()["draws"] == []
    assert client.delete(f"/api/exchanges/{ex}/draws/{ids.alex}", json={}, headers=mut()).status_code == 404


def test_a_locked_request_sees_no_protected_draw(client, ids, unlock):
    ex = mk_exchange(client, ids)
    unlock()
    put_draw(client, ex, ids.alex, ids.jordan)
    put_draw(client, ex, ids.sam, ids.kid1)       # protected recipient
    put_draw(client, ex, ids.kid2, ids.riley)     # protected drawer
    assert draws_of(client, ids, ex) == {(ids.alex, ids.jordan), (ids.sam, ids.kid1), (ids.kid2, ids.riley)}
    client.post("/api/lock/lock", json={}, headers=mut())
    assert draws_of(client, ids, ex) == {(ids.alex, ids.jordan)}
    assert [e["name"] for e in listing(client, ids)] == ["Secret Santa"]      # the exchange itself still shows


def test_a_person_protected_later_hides_their_draws_at_once(client, ids):
    ex = mk_exchange(client, ids)
    put_draw(client, ex, ids.alex, ids.jordan)
    assert draws_of(client, ids, ex) == {(ids.alex, ids.jordan)}
    assert client.patch(f"/api/people/{ids.jordan}", json={"protected": True}, headers=mut()).status_code == 200
    assert draws_of(client, ids, ex) == set()


def test_a_protected_draw_is_immutable_while_locked(client, ids, unlock):
    ex = mk_exchange(client, ids)
    unlock()
    put_draw(client, ex, ids.sam, ids.kid1)       # hidden by its recipient
    put_draw(client, ex, ids.kid2, ids.riley)     # hidden by its drawer
    client.post("/api/lock/lock", json={}, headers=mut())
    before = con_rows("SELECT * FROM exchange_draws ORDER BY drawer_id")
    for r in (put_draw(client, ex, ids.kid2, ids.jordan),         # protected drawer
              put_draw(client, ex, ids.alex, ids.kid1),           # protected recipient
              put_draw(client, ex, ids.sam, ids.jordan),          # would replace a hidden draw
              client.delete(f"/api/exchanges/{ex}/draws/{ids.kid2}", json={}, headers=mut())):
        assert (r.status_code, r.get_json()) == (423, LOCKED)
    hidden = client.delete(f"/api/exchanges/{ex}/draws/{ids.sam}", json={}, headers=mut())
    missing = client.delete(f"/api/exchanges/{ex}/draws/{ids.alex}", json={}, headers=mut())
    assert (hidden.status_code, hidden.get_json()) == (missing.status_code, missing.get_json()) == (404, {"error": "No such draw."})
    assert con_rows("SELECT * FROM exchange_draws ORDER BY drawer_id") == before


def test_deleting_an_exchange_needs_the_unlock(client, ids, unlock):
    ex = mk_exchange(client, ids)
    r = client.delete(f"/api/exchanges/{ex}", json={}, headers=mut())
    assert (r.status_code, r.get_json()) == (423, LOCKED)
    unlock()
    put_draw(client, ex, ids.alex, ids.jordan)
    assert client.delete(f"/api/exchanges/{ex}", json={}, headers=mut()).status_code == 200
    assert con_rows("SELECT * FROM exchanges") == [] and con_rows("SELECT * FROM exchange_draws") == []


def test_a_person_in_a_draw_cannot_be_deleted(client, ids, unlock):
    unlock()
    ex = mk_exchange(client, ids)
    put_draw(client, ex, ids.alex, ids.jordan)
    r = client.delete(f"/api/people/{ids.jordan}", json={}, headers=mut())
    assert r.status_code == 409
    assert con_rows("SELECT id FROM people WHERE id = ?", ids.jordan) == [(ids.jordan,)]


# ---------------------------------------------------------------- the gift for a draw
def lock_now(client):
    assert client.post("/api/lock/lock", json={}, headers=mut()).status_code == 200


def test_a_gift_for_a_draw(client, ids):
    ex = mk_exchange(client, ids)
    put_draw(client, ex, ids.alex, ids.jordan)
    gid = mk_gift(client, ids.o, "Mug", to=[ids.jordan], frm=[ids.alex], exchange_id=ex, price="20")
    g = client.get(f"/api/gifts/{gid}", headers=auth()).get_json()
    assert (g["exchange_id"], g["track"], g["status"], g["recipient_ids"], g["giver_ids"]) == (ex, "ours", "idea", [ids.jordan], [ids.alex])
    plain = mk_gift(client, ids.o, "Plain", to=[ids.jordan])
    assert client.get(f"/api/gifts/{plain}", headers=auth()).get_json()["exchange_id"] is None
    assert listing(client, ids)[0]["draws"][0]["gift_id"] == gid


def test_a_gift_must_match_a_draw_of_its_own_occasion(client, ids):
    ex = mk_exchange(client, ids)
    other = mk_exchange(client, ids, occasion=mk_occasion_other(client))
    put_draw(client, ex, ids.alex, ids.jordan)
    base = {"occasion_id": ids.o, "title": "Mug", "recipient_ids": [ids.jordan], "giver_ids": [ids.alex], "exchange_id": ex}
    for change in ({"recipient_ids": [ids.riley]}, {"recipient_ids": [ids.jordan, ids.riley]}, {"giver_ids": []},
                   {"giver_ids": [ids.sam]}, {"giver_ids": [ids.alex, ids.sam]}, {"exchange_id": other},
                   {"exchange_id": 999999}, {"exchange_id": True}, {"exchange_id": "1"}, {"exchange_id": 1.5}):
        r = client.post("/api/gifts", json={**base, **change}, headers=mut())
        assert r.status_code == 400, (change, r.get_json())
    assert con_rows("SELECT id FROM gifts") == []


def mk_occasion_other(client):
    return mk_occasion(client, name="Birthday", kind="birthday", date="2026-06-01")


def test_one_gift_per_draw(client, ids):
    ex = mk_exchange(client, ids)
    put_draw(client, ex, ids.alex, ids.jordan)
    first = mk_gift(client, ids.o, "Mug", to=[ids.jordan], frm=[ids.alex], exchange_id=ex)
    r = client.post("/api/gifts", json={"occasion_id": ids.o, "title": "Pen", "recipient_ids": [ids.jordan],
                                        "giver_ids": [ids.alex], "exchange_id": ex}, headers=mut())
    assert (r.status_code, r.get_json()) == (409, {"error": "This draw already has a gift."})
    assert con_rows("SELECT id, exchange_id FROM gifts") == [(first, ex)]
    assert client.patch(f"/api/gifts/{first}", json={"exchange_id": None}, headers=mut()).get_json()["exchange_id"] is None
    assert mk_gift(client, ids.o, "Pen", to=[ids.jordan], frm=[ids.alex], exchange_id=ex)


def test_editing_a_linked_gift_cannot_break_the_draw(client, ids):
    ex = mk_exchange(client, ids)
    put_draw(client, ex, ids.alex, ids.jordan)
    gid = mk_gift(client, ids.o, "Mug", to=[ids.jordan], frm=[ids.alex], exchange_id=ex)
    for body in ({"recipient_ids": [ids.riley]}, {"giver_ids": []}, {"giver_ids": [ids.sam]},
                 {"title": "Renamed", "recipient_ids": [ids.riley]}):
        assert client.patch(f"/api/gifts/{gid}", json=body, headers=mut()).status_code == 400, body
    assert con_rows("SELECT title, exchange_id FROM gifts") == [("Mug", ex)]
    assert con_rows("SELECT person_id FROM gift_recipients") == [(ids.jordan,)]
    assert client.patch(f"/api/gifts/{gid}", json={"title": "Better mug", "price": "19"}, headers=mut()).status_code == 200
    assert client.patch(f"/api/gifts/{gid}", json={"exchange_id": None, "recipient_ids": [ids.riley]}, headers=mut()).status_code == 200


def test_a_draw_with_a_gift_cannot_be_removed_or_redrawn(client, ids):
    ex = mk_exchange(client, ids)
    put_draw(client, ex, ids.alex, ids.jordan)
    mk_gift(client, ids.o, "Mug", to=[ids.jordan], frm=[ids.alex], exchange_id=ex)
    assert client.delete(f"/api/exchanges/{ex}/draws/{ids.alex}", json={}, headers=mut()).status_code == 409
    assert put_draw(client, ex, ids.alex, ids.riley).status_code == 409
    assert put_draw(client, ex, ids.alex, ids.jordan).status_code == 200      # restating the same draw is fine
    assert con_rows("SELECT drawer_id, recipient_id FROM exchange_draws") == [(ids.alex, ids.jordan)]
    assert client.delete(f"/api/exchanges/{ex}", json={}, headers=mut()).status_code == 423   # locked: needs the unlock


@pytest.fixture
def world(client, ids, unlock):
    """One exchange, locked at the end. Draws: alex to jordan (visible, gift over budget), sam to kid1 (hidden by its
    recipient), kid2 to riley (hidden by its drawer), kid3 to casey (hidden by its drawer, with a gift)."""
    unlock()
    ex = mk_exchange(client, ids, budget=2500)
    for drawer, to in ((ids.alex, ids.jordan), (ids.sam, ids.kid1), (ids.kid2, ids.riley), (ids.kid3, ids.casey)):
        assert put_draw(client, ex, drawer, to).status_code == 200
    visible = mk_gift(client, ids.o, "EXCH-VISIBLE", to=[ids.jordan], frm=[ids.alex], exchange_id=ex, price="30")
    control = mk_gift(client, ids.o, "CONTROL-GIFT", to=[ids.jordan], frm=[ids.alex], price="12")
    hidden_recipient = mk_gift(client, ids.o, "ZZ-EXCH-RECIPIENT", to=[ids.kid1], frm=[ids.sam], exchange_id=ex, price="42.42")
    hidden = mk_gift(client, ids.o, "ZZ-EXCH-DRAWER", to=[ids.casey], frm=[ids.kid3], exchange_id=ex, price="42.42")
    lock_now(client)
    group = client.post("/api/groups", json={"name": "Side A"}, headers=mut()).get_json()["id"]
    return types.SimpleNamespace(ids=ids, ex=ex, visible=visible, control=control, group=group,
                                 hidden=(hidden_recipient, hidden), secret=(hidden_recipient, hidden))


MARKERS = ("zz-exch", "4242", '"santa"')


def leaked(text):
    return [m for m in MARKERS if m in text.lower()]


def test_a_gift_from_a_protected_drawer_is_hidden_while_locked(client, ids, world):
    missing = client.get("/api/gifts/999999", headers=auth())
    for gid in world.hidden:
        r = client.get(f"/api/gifts/{gid}", headers=auth())
        assert (r.status_code, r.get_json()) == (missing.status_code, missing.get_json()) == (404, {"error": "No such gift."})
        assert client.patch(f"/api/gifts/{gid}", json={"title": "x"}, headers=mut()).status_code == 404
        assert client.post(f"/api/gifts/{gid}/status", json={"status": "given"}, headers=mut()).status_code == 404
        assert client.delete(f"/api/gifts/{gid}", headers=mut()).status_code == 404
    casey = client.get(f"/api/occasions/{ids.o}/people/{ids.casey}/gifts", headers=auth()).get_json()
    assert casey == []
    jordan = client.get(f"/api/occasions/{ids.o}/people/{ids.jordan}/gifts", headers=auth()).get_json()
    assert {g["title"] for g in jordan} == {"EXCH-VISIBLE", "CONTROL-GIFT"}
    assert client.get(f"/api/people/{ids.casey}/history", headers=auth()).get_json()["gifts"] == []
    s = client.get(f"/api/occasions/{ids.o}/summary", headers=auth()).get_json()
    casey_row = next(p for g in s["groups"] for p in g["people"] if p["id"] == ids.casey)
    assert (casey_row["gift_count"], casey_row["headline"], casey_row["spent_cents"], casey_row["planned_cents"]) == (0, None, 0, 0)
    for path in ("lists/pending", "lists/to-wrap", "lists/where", "lists/counts", "summary", "exchanges"):
        assert not leaked(client.get(f"/api/occasions/{ids.o}/{path}", headers=auth()).get_data(as_text=True)), path
    assert len(con_rows("SELECT id FROM gifts")) == 4


def test_a_locked_request_cannot_create_a_gift_for_a_protected_draw(client, ids, world):
    body = {"occasion_id": ids.o, "title": "x", "recipient_ids": [ids.casey], "giver_ids": [ids.kid3]}
    for exchange in (world.ex, 999999):
        r = client.post("/api/gifts", json={**body, "exchange_id": exchange}, headers=mut())
        assert (r.status_code, r.get_json()) == (423, LOCKED), exchange
    assert len(con_rows("SELECT id FROM gifts")) == 4


def test_a_locked_patch_cannot_move_a_gift_onto_a_protected_giver(client, ids, world):
    r = client.patch(f"/api/gifts/{world.visible}", json={"giver_ids": [ids.kid2]}, headers=mut())
    assert (r.status_code, r.get_json()) == (423, LOCKED)
    assert con_rows("SELECT person_id FROM gift_givers WHERE gift_id = ?", world.visible) == [(ids.alex,)]


def test_a_locked_patch_cannot_link_a_visible_gift_from_a_protected_giver(client, ids, world, unlock):
    unlock()
    plain = mk_gift(client, ids.o, "KID-GIFT", to=[ids.riley], frm=[ids.kid2])
    lock_now(client)
    assert client.get(f"/api/gifts/{plain}", headers=auth()).status_code == 200       # not an exchange gift: visible
    for exchange in (world.ex, 999999):
        r = client.patch(f"/api/gifts/{plain}", json={"exchange_id": exchange}, headers=mut())
        assert (r.status_code, r.get_json()) == (423, LOCKED), exchange
    assert con_rows("SELECT exchange_id FROM gifts WHERE id = ?", plain) == [(None,)]


def test_the_agent_api_never_shows_a_protected_draw_gift_below_an_unlocked_full_token(client, ids, world):
    every = "?status=" + ",".join(agent.ALL_STATUSES)
    for scope in ("delivery", "shop", "full"):
        tok = mk_token(scope, name=f"{scope} agent")
        listed = client.get("/api/v1/gifts" + every, headers=bearer(tok)).get_data(as_text=True)
        assert not leaked(listed), scope
        for gid in world.hidden:
            assert client.get(f"/api/v1/gifts/{gid}", headers=bearer(tok)).status_code == 404, scope
            assert client.patch(f"/api/v1/gifts/{gid}/order", json={"seller": "x"}, headers=bearer(tok)).status_code == 404
    full = mk_token("full", name="open agent", unlocked=True)
    assert {g["title"] for g in client.get("/api/v1/gifts" + every, headers=bearer(full)).get_json()} >= {"ZZ-EXCH-DRAWER", "ZZ-EXCH-RECIPIENT"}


def test_an_agent_cannot_link_a_gift_to_an_exchange(client, ids, world):
    full = mk_token("full", name="open agent", unlocked=True)
    r = client.post("/api/v1/gifts", json={"occasion_id": ids.o, "title": "x", "recipient_ids": [ids.jordan],
                                           "giver_ids": [ids.alex], "exchange_id": world.ex}, headers=bearer(full))
    assert r.status_code == 400
    assert len(con_rows("SELECT id FROM gifts")) == 4


def test_no_get_under_api_leaks_a_protected_draw_or_its_gift_when_locked(client, ids, world):
    seen = test_privacy.walk_gets(client, world)
    assert any("exchange" in url for url in seen)
    for url, text in seen.items():
        assert not leaked(text), (url, leaked(text))
    assert any("EXCH-VISIBLE" in t for t in seen.values())           # the walk does reach exchange gifts


def protected_exchange_state():
    return (con_rows("SELECT d.* FROM exchange_draws d JOIN people a ON a.id = d.drawer_id JOIN people b ON b.id = d.recipient_id "
                     "WHERE a.protected = 1 OR b.protected = 1 ORDER BY d.exchange_id, d.drawer_id"),
            con_rows("SELECT g.* FROM gifts g WHERE " + gifts.PROTECTED_SQL + " ORDER BY g.id"),
            con_rows("SELECT id FROM people WHERE protected = 1 ORDER BY id"))


def test_no_locked_write_under_api_changes_or_reveals_a_protected_draw(client, ids, world):
    bodies = ({"name": "pwned", "budget_cents": 1, "date": "2026-01-01", "recipient_id": ids.kid1, "exchange_id": world.ex},
              {"recipient_id": ids.jordan, "title": "pwned", "giver_ids": [ids.kid3], "exchange_id": world.ex},
              {"recipient_id": ids.casey, "protected": False}, {})
    before, sent = protected_exchange_state(), 0
    for rule in test_privacy.api_rules({"POST", "PUT", "PATCH", "DELETE"}):
        if "exchange" not in rule.endpoint and "gifts" not in rule.endpoint:
            continue
        for method in sorted(rule.methods & {"POST", "PUT", "PATCH", "DELETE"}):
            for url, values in test_privacy.urls(rule, world):
                for body in bodies:
                    r = client.open(url, method=method, json=body, headers=mut())
                    sent += 1
                    assert r.status_code < 500, (method, url, r.status_code)
                    assert not leaked(r.get_data(as_text=True)), (method, url)
                    if values.get("gift_id") in world.hidden:
                        assert r.status_code in (404, 423), (method, url)
                    assert protected_exchange_state() == before, (method, url, body)
    assert sent > 40


# ---------------------------------------------------------------- the home summary and the over-budget flag
@pytest.mark.parametrize("price, budget, want", [(2501, 2500, True), (2500, 2500, False), (2499, 2500, False),
                                                  (None, 2500, False), (9999, None, False), (0, 0, False), (1, 0, True)])
def test_over_budget_boundaries(price, budget, want):
    assert exchanges.over_budget(price, budget) is want


def summary(client, ids):
    return client.get(f"/api/occasions/{ids.o}/summary", headers=auth()).get_json()


def test_the_summary_lists_each_exchange_with_its_draws(client, ids):
    ex = mk_exchange(client, ids, budget=2500)
    put_draw(client, ex, ids.alex, ids.jordan)
    put_draw(client, ex, ids.sam, ids.riley)
    gid = mk_gift(client, ids.o, "Mug", to=[ids.jordan], frm=[ids.alex], exchange_id=ex, price="30")
    assert summary(client, ids)["exchanges"] == [{"id": ex, "name": "Secret Santa", "budget_cents": 2500, "date": "2026-12-20", "draws": [
        {"drawer_id": ids.alex, "recipient_id": ids.jordan, "gift_id": gid, "gift_title": "Mug", "gift_status": "idea",
         "price_cents": 3000, "over_budget": True},
        {"drawer_id": ids.sam, "recipient_id": ids.riley, "gift_id": None, "gift_title": None, "gift_status": None,
         "price_cents": None, "over_budget": False}]}]
    assert listing(client, ids) == summary(client, ids)["exchanges"]
    assert client.post(f"/api/gifts/{gid}/status", json={"status": "wrapped"}, headers=mut()).status_code == 200
    assert summary(client, ids)["exchanges"][0]["draws"][0]["gift_status"] == "wrapped"


def test_a_gift_over_the_budget_is_flagged_not_refused(client, ids):
    ex = mk_exchange(client, ids, budget=2500)
    put_draw(client, ex, ids.alex, ids.jordan)
    gid = mk_gift(client, ids.o, "Watch", to=[ids.jordan], frm=[ids.alex], exchange_id=ex, price="25.01")
    assert con_rows("SELECT price_cents FROM gifts WHERE id = ?", gid) == [(2501,)]
    assert listing(client, ids)[0]["draws"][0]["over_budget"] is True
    assert client.patch(f"/api/gifts/{gid}", json={"price": "25.00"}, headers=mut()).status_code == 200
    assert listing(client, ids)[0]["draws"][0]["over_budget"] is False
    client.patch(f"/api/exchanges/{ex}", json={"budget_cents": 2000}, headers=mut())
    assert listing(client, ids)[0]["draws"][0]["over_budget"] is True       # the budget is read when asked, not stored


def test_the_summary_exchanges_hide_protected_draws_while_locked(client, ids, world, unlock):
    s = summary(client, ids)
    assert [(d["drawer_id"], d["recipient_id"]) for d in s["exchanges"][0]["draws"]] == [(ids.alex, ids.jordan)]
    assert not leaked(client.get(f"/api/occasions/{ids.o}/summary", headers=auth()).get_data(as_text=True))
    unlock()
    assert len(summary(client, ids)["exchanges"][0]["draws"]) == 4
    assert "ZZ-EXCH-DRAWER" in client.get(f"/api/occasions/{ids.o}/summary", headers=auth()).get_data(as_text=True)


def test_exchange_gifts_count_in_money_for_unprotected_recipients(client, ids):
    ex = mk_exchange(client, ids)
    put_draw(client, ex, ids.alex, ids.jordan)
    mk_gift(client, ids.o, "Mug", to=[ids.jordan], frm=[ids.alex], exchange_id=ex, price="20")
    jordan = next(p for g in summary(client, ids)["groups"] for p in g["people"] if p["id"] == ids.jordan)
    assert (jordan["planned_cents"], jordan["gift_count"], jordan["headline"]) == (2000, 1, "Mug")


# ---------------------------------------------------------------- carry-forward
def test_copying_an_occasion_copies_exchange_names_and_budgets_only(client, ids, unlock):
    unlock()
    ex = mk_exchange(client, ids, name="Secret Santa", budget=2500, date="2026-12-20")
    mk_exchange(client, ids, name="Favorite Things", budget=None, date=None)
    put_draw(client, ex, ids.alex, ids.jordan)
    mk_gift(client, ids.o, "Mug", to=[ids.jordan], frm=[ids.alex], exchange_id=ex)
    new = post(client, "/api/occasions", {"name": "Christmas 2027", "kind": "christmas", "date": "2027-12-25",
                                          "copy_from": ids.o}).get_json()["id"]
    copied = listing(client, ids, occasion=new)
    assert [(e["name"], e["budget_cents"], e["date"], e["draws"]) for e in copied] == \
        [("Secret Santa", 2500, None, []), ("Favorite Things", None, None, [])]
    assert {e["id"] for e in copied}.isdisjoint({e["id"] for e in listing(client, ids)})
    assert con_rows("SELECT occasion_id, COUNT(*) FROM exchange_draws d JOIN exchanges e ON e.id = d.exchange_id GROUP BY 1") == [(ids.o, 1)]
    assert con_rows("SELECT COUNT(*) FROM gifts WHERE occasion_id = ?", new) == [(0,)]
    assert len(listing(client, ids)) == 2                         # the source keeps its exchanges and draws
    assert len(listing(client, ids)[0]["draws"]) == 1


def test_copying_an_occasion_with_no_exchanges_or_without_copy_from_adds_none(client, ids):
    plain = post(client, "/api/occasions", {"name": "A", "kind": "other", "date": "2027-01-01", "copy_from": ids.o}).get_json()["id"]
    mk_exchange(client, ids)
    bare = post(client, "/api/occasions", {"name": "B", "kind": "other", "date": "2027-01-02"}).get_json()["id"]
    assert listing(client, ids, occasion=plain) == [] and listing(client, ids, occasion=bare) == []


def test_copying_never_carries_a_protected_draw(client, ids, world, unlock):
    new = post(client, "/api/occasions", {"name": "Christmas 2027", "kind": "christmas", "date": "2027-12-25",
                                          "copy_from": ids.o}).get_json()["id"]
    unlock()
    assert [e["draws"] for e in listing(client, ids, occasion=new)] == [[]]
    assert con_rows("SELECT COUNT(*) FROM exchange_draws") == [(4,)]
