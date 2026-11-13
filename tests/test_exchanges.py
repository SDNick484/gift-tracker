"""Gift exchanges: the exchanges of an occasion, who in this household drew whom, the gift for each draw, the over-budget
flag, carry-forward, and above all the lock: a draw whose drawer or recipient is protected, and any gift linked to
it, does not exist while locked. School mode has exchanges but no lock and no protected drawer."""
import pytest

from conftest import auth, con_rows, mut

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
