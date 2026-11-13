import pytest

from conftest import auth, mk_gift, mk_occasion, mk_person, mut


def test_users_are_seeded_as_people_in_household_us(client):
    ppl = client.get("/api/people", headers=auth()).get_json()
    assert [p["name"] for p in ppl if p["is_user"]] == ["Alex", "Sam"]
    assert {p["household"] for p in ppl if p["is_user"]} == {"Us"}


def test_ensure_users_is_idempotent(con):
    import people
    import settings
    people.ensure_users(con, settings.USERS)
    people.ensure_users(con, settings.USERS)
    assert con.execute("SELECT COUNT(*) FROM people").fetchone()[0] == 2
    assert con.execute("SELECT COUNT(*) FROM groups WHERE name='Us'").fetchone()[0] == 1
    assert people.user_person_ids(con) == {r["id"] for r in con.execute("SELECT id FROM people")}


def test_households_group_by_label(client):
    mk_person(client, "Jordan", household="Jordan & Riley"); mk_person(client, "Riley", household="Jordan & Riley")
    mk_person(client, "Casey")
    labels = [h["label"] for h in client.get("/api/households", headers=auth()).get_json()]
    assert labels == ["Us", "Jordan & Riley", "Casey"]


def test_household_members_are_listed_together(client):
    a, b = mk_person(client, "Jordan", household="J&R"), mk_person(client, "Riley", household="J&R")
    hh = {h["label"]: h["person_ids"] for h in client.get("/api/households", headers=auth()).get_json()}
    assert hh["J&R"] == [a, b]


@pytest.mark.parametrize("color", ["red", "#12345", "#12345g", "123456", 5])
def test_bad_color_is_400(client, color):
    assert client.post("/api/people", json={"name": "X", "color": color}, headers=mut()).status_code == 400


def test_good_color_and_null_color(client):
    assert client.post("/api/people", json={"name": "X", "color": "#1A2b3C"}, headers=mut()).status_code == 201
    assert client.post("/api/people", json={"name": "Y", "color": None}, headers=mut()).status_code == 201


@pytest.mark.parametrize("body", [{}, {"name": ""}, {"name": "  "}, {"name": 5}, {"name": "X", "group_id": 999},
                                  {"name": "X", "protected": "yes"}, {"name": "X", "sort_order": 1.5}])
def test_bad_person_is_400(client, body):
    assert client.post("/api/people", json=body, headers=mut()).status_code == 400


def test_group_crud_and_unique_name(client):
    gid = client.post("/api/groups", json={"name": "Friends"}, headers=mut()).get_json()["id"]
    assert client.post("/api/groups", json={"name": "Friends"}, headers=mut()).status_code == 409
    assert client.patch(f"/api/groups/{gid}", json={"name": "Neighbours"}, headers=mut()).get_json()["name"] == "Neighbours"
    assert client.post("/api/groups", json={"name": ""}, headers=mut()).status_code == 400
    assert "Neighbours" in [g["name"] for g in client.get("/api/groups", headers=auth()).get_json()]
    assert client.delete(f"/api/groups/{gid}", headers=mut()).status_code == 200
    assert client.delete(f"/api/groups/{gid}", headers=mut()).status_code == 404


def test_deleting_a_group_keeps_its_people(client):
    gid = client.post("/api/groups", json={"name": "Friends"}, headers=mut()).get_json()["id"]
    pid = mk_person(client, "Casey", group_id=gid)
    client.delete(f"/api/groups/{gid}", headers=mut())
    person = next(p for p in client.get("/api/people", headers=auth()).get_json() if p["id"] == pid)
    assert person["group_id"] is None


def test_update_person_is_partial(client):
    pid = mk_person(client, "Casey", household="Home", notes="likes tea")
    r = client.patch(f"/api/people/{pid}", json={"name": "Casey B"}, headers=mut()).get_json()
    assert (r["name"], r["household"], r["notes"]) == ("Casey B", "Home", "likes tea")
    assert client.patch("/api/people/9999", json={"name": "X"}, headers=mut()).status_code == 404


def test_delete_person_without_gifts_and_user_person_is_refused(client):
    pid = mk_person(client, "Casey")
    assert client.delete(f"/api/people/{pid}", headers=mut()).status_code == 200
    alex = next(p["id"] for p in client.get("/api/people", headers=auth()).get_json() if p["name"] == "Alex")
    assert client.delete(f"/api/people/{alex}", headers=mut()).status_code == 409


@pytest.mark.xfail(strict=True, reason="needs occasions and gifts: Tasks 4 and 5")
def test_delete_person_with_gifts_is_409_and_deactivate_keeps_history(client):
    pid = mk_person(client, "Jordan"); oid = mk_occasion(client); mk_gift(client, oid, "Book", to=[pid])
    r = client.delete(f"/api/people/{pid}", headers=mut())
    assert r.status_code == 409 and r.get_json()["error"] == "This person has gifts. Deactivate them instead."
    assert client.patch(f"/api/people/{pid}", json={"active": False}, headers=mut()).status_code == 200
    assert pid not in [p["id"] for p in client.get("/api/people", headers=auth()).get_json()]
    assert client.get(f"/api/people/{pid}/history", headers=auth()).get_json()["gifts"][0]["title"] == "Book"
