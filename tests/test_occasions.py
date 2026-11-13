import pytest

import occasions
from conftest import auth, con_rows, mk_occasion, mk_person, mut, put_plan


def test_copy_from_carries_plans_for_active_people_only(client):
    a, b = mk_person(client, "Jordan"), mk_person(client, "Riley")
    o1 = mk_occasion(client, "Christmas 2025", "christmas", "2025-12-25")
    put_plan(client, o1, a, "joint", 7500); put_plan(client, o1, b, "exchange", None)
    client.patch(f"/api/people/{b}", json={"active": False}, headers=mut())
    o2 = client.post("/api/occasions", json={"name": "Christmas 2026", "kind": "christmas", "date": "2026-12-25",
                                             "copy_from": o1}, headers=mut()).get_json()["id"]
    rows = con_rows("SELECT person_id, plan, budget_cents FROM occasion_people WHERE occasion_id=?", o2)
    assert rows == [(a, "joint", 7500)]


def test_copy_from_copies_no_gifts_and_leaves_the_source_alone(client):
    a = mk_person(client, "Jordan")
    o1 = mk_occasion(client, "Christmas 2025", "christmas", "2025-12-25")
    put_plan(client, o1, a, "individual", 100)
    o2 = mk_occasion(client, "Christmas 2026", copy_from=o1)
    assert con_rows("SELECT COUNT(*) FROM gifts") == [(0,)]
    assert con_rows("SELECT COUNT(*) FROM occasion_people WHERE occasion_id=?", o1) == [(1,)]
    assert con_rows("SELECT COUNT(*) FROM occasion_people WHERE occasion_id=?", o2) == [(1,)]


def test_new_occasion_without_copy_from_has_no_people(client):
    mk_person(client, "Jordan")
    o = mk_occasion(client)
    assert con_rows("SELECT COUNT(*) FROM occasion_people WHERE occasion_id=?", o) == [(0,)]


def test_copy_from_unknown_occasion_is_404_and_creates_nothing(client):
    r = client.post("/api/occasions", json={"name": "X", "kind": "other", "date": "2026-01-01", "copy_from": 999},
                    headers=mut())
    assert r.status_code == 404
    assert con_rows("SELECT COUNT(*) FROM occasions") == [(0,)]


@pytest.mark.parametrize("body", [{"name": "", "kind": "christmas", "date": "2026-12-25"},
                                  {"name": "X", "kind": "party", "date": "2026-12-25"},
                                  {"name": "X", "kind": "other", "date": "12/25/2026"},
                                  {"name": "X", "kind": "other", "date": "2026-02-30"},
                                  {"name": "X", "kind": "other", "date": "20261225"},
                                  {"name": "X", "kind": "other"},
                                  {"name": "X", "date": "2026-12-25"},
                                  {"name": "X", "kind": "other", "date": "2026-12-25", "copy_from": "1"}])
def test_bad_occasion_is_400(client, body):
    assert client.post("/api/occasions", json=body, headers=mut()).status_code == 400


def test_current_prefers_next_upcoming(con, freeze_today):
    freeze_today("2026-11-12")
    occasions.create(con, "Christmas 2025", "christmas", "2025-12-25", None)
    nxt = occasions.create(con, "Christmas 2026", "christmas", "2026-12-25", None)
    occasions.create(con, "Birthday 2027", "birthday", "2027-03-01", None)
    assert occasions.current(con)["id"] == nxt


def test_current_falls_back_to_latest_and_skips_archived(con, freeze_today):
    freeze_today("2030-01-01")
    assert occasions.current(con) is None
    occasions.create(con, "Old", "other", "2025-01-01", None)
    latest = occasions.create(con, "Newer", "other", "2026-01-01", None)
    assert occasions.current(con)["id"] == latest
    con.execute("UPDATE occasions SET archived = 1 WHERE id = ?", (latest,))
    assert occasions.current(con)["name"] == "Old"


def test_current_includes_today(con, freeze_today):
    freeze_today("2026-12-25")
    oid = occasions.create(con, "Christmas 2026", "christmas", "2026-12-25", None)
    occasions.create(con, "Later", "other", "2027-01-01", None)
    assert occasions.current(con)["id"] == oid


def test_plan_upsert_and_remove(client):
    p, o = mk_person(client, "Jordan"), mk_occasion(client)
    put_plan(client, o, p, "joint", 5000)
    put_plan(client, o, p, "skip", None)
    assert con_rows("SELECT plan, budget_cents FROM occasion_people WHERE occasion_id=?", o) == [("skip", None)]
    assert client.delete(f"/api/occasions/{o}/people/{p}", headers=mut()).status_code == 200
    assert client.delete(f"/api/occasions/{o}/people/{p}", headers=mut()).status_code == 404


@pytest.mark.parametrize("body", [{}, {"plan": "party"}, {"plan": "joint", "budget_cents": -1},
                                  {"plan": "joint", "budget_cents": 10_000_001}, {"plan": "joint", "budget_cents": "5"},
                                  {"plan": "joint", "budget_cents": 12.5}, {"plan": "joint", "budget_cents": True}])
def test_bad_plan_is_400(client, body):
    p, o = mk_person(client, "Jordan"), mk_occasion(client)
    assert client.put(f"/api/occasions/{o}/people/{p}", json=body, headers=mut()).status_code == 400


def test_plan_for_unknown_occasion_or_person_is_404(client):
    p, o = mk_person(client, "Jordan"), mk_occasion(client)
    assert client.put(f"/api/occasions/999/people/{p}", json={"plan": "joint"}, headers=mut()).status_code == 404
    assert client.put(f"/api/occasions/{o}/people/999", json={"plan": "joint"}, headers=mut()).status_code == 404


def test_patch_and_list(client, freeze_today):
    freeze_today("2026-11-12")
    p = mk_person(client, "Jordan")
    o = mk_occasion(client, "Christmas 2026")
    put_plan(client, o, p, "joint", 100)
    assert client.patch(f"/api/occasions/{o}", json={"name": "Xmas", "date": "2026-12-24"}, headers=mut()).status_code == 200
    assert client.patch(f"/api/occasions/{o}", json={"date": "nope"}, headers=mut()).status_code == 400
    assert client.patch(f"/api/occasions/{o}", json={"archived": "yes"}, headers=mut()).status_code == 400
    assert client.patch("/api/occasions/999", json={"name": "X"}, headers=mut()).status_code == 404
    listed = client.get("/api/occasions", headers=auth()).get_json()
    assert listed == [{"id": o, "name": "Xmas", "kind": "christmas", "date": "2026-12-24", "archived": False,
                       "current": True, "people": [{"person_id": p, "plan": "joint", "budget_cents": 100}]}]
