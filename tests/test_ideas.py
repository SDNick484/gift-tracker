"""Ideas: one idea tagged with several people, each person's copy open or taken, and the idle lock over them (an idea
with any protected person tagged does not exist while locked). Routes are family-only."""
import pytest

import db
import ideas
import settings
from conftest import auth, con_rows, mk_gift, mut

USER = {"email": "alex@example.com", "name": "Alex"}


def mk_idea(client, title, person_ids, **fields):
    r = client.post("/api/ideas", json={"title": title, "person_ids": list(person_ids), **fields}, headers=mut())
    assert r.status_code == 201, r.get_json()
    return r.get_json()


def mk_idea_direct(title, person_ids, **fields):
    """Insert through ideas.create, past the lock (as conftest.mk_protected_gift does for gifts)."""
    c = db.connect(settings.DB_PATH)
    try:
        return ideas.create(c, USER, {"title": title, "person_ids": list(person_ids), **fields})
    finally:
        c.close()


def lock(client):
    assert client.post("/api/lock/lock", json={}, headers=mut()).status_code == 200


# ---------------------------------------------------------------- create, read, edit, delete
def test_create_one_idea_for_three_people(client, ids, unlock):
    unlock()
    body = mk_idea(client, "Animal tracking bracelet", [ids.kid1, ids.kid2, ids.kid3], store="Shop",
                   url="https://example.com/b")
    assert body["title"] == "Animal tracking bracelet"
    assert (body["store"], body["url"], body["share"]) == ("Shop", "https://example.com/b", False)
    assert body["created_by"] == "alex@example.com"
    assert [p["person_id"] for p in body["people"]] == [ids.kid1, ids.kid2, ids.kid3]
    for p in body["people"]:
        assert (p["state"], p["gift_id"], p["occasion_id"], p["held_by"]) == ("open", None, None, None)
    assert set(body) == {"id", "title", "url", "store", "price_cents", "notes", "share", "wishlist_item_id",
                         "created_by", "updated_at", "people"}
    assert client.get(f"/api/ideas/{body['id']}", headers=auth()).get_json() == body


def test_create_takes_price_notes_and_share(client, ids):
    body = mk_idea(client, "Book", [ids.jordan], price="12.50", notes="Paperback", share=True)
    assert (body["price_cents"], body["notes"], body["share"]) == (1250, "Paperback", True)


@pytest.mark.parametrize("bad", [
    {"person_ids": "J"},                                  # no title
    {"title": "", "person_ids": "J"},
    {"title": "x" * 201, "person_ids": "J"},
    {"title": "Book"},                                    # person_ids missing
    {"title": "Book", "person_ids": []},
    {"title": "Book", "person_ids": [999999]},
    {"title": "Book", "person_ids": ["x"]},
    {"title": "Book", "person_ids": [True]},
    {"title": "Book", "person_ids": "J", "url": "ftp://x"},
    {"title": "Book", "person_ids": "J", "price": "abc"},
    {"title": "Book", "person_ids": "J", "share": "yes"},
    {"title": "Book", "person_ids": "J", "store": 5},
])
def test_validation(client, ids, bad):
    body = {k: [ids.jordan] if v == "J" else v for k, v in bad.items()}
    r = client.post("/api/ideas", json=body, headers=mut())
    assert r.status_code == 400, (body, r.get_json())
    assert con_rows("SELECT COUNT(*) FROM ideas") == [(0,)]


def test_the_title_message_names_an_idea(client, ids):
    r = client.post("/api/ideas", json={"title": "", "person_ids": [ids.jordan]}, headers=mut())
    assert r.get_json() == {"error": "An idea needs a title of 1 to 200 characters."}
    r = client.post("/api/gifts", json={"title": "", "occasion_id": ids.o, "recipient_ids": [ids.jordan]}, headers=mut())
    assert r.get_json() == {"error": "A gift needs a title of 1 to 200 characters."}


def test_person_ids_are_de_duplicated(client, ids):
    body = mk_idea(client, "Book", [ids.jordan, ids.riley, ids.jordan])
    assert [p["person_id"] for p in body["people"]] == [ids.jordan, ids.riley]


def test_patch_fields_tags_and_share(client, ids):
    idea = mk_idea(client, "Book", [ids.jordan, ids.riley, ids.casey])
    r = client.patch(f"/api/ideas/{idea['id']}", json={"title": "Big book", "share": True, "store": "Shop",
                                                       "person_ids": [ids.jordan, ids.casey]}, headers=mut())
    assert r.status_code == 200, r.get_json()
    body = r.get_json()
    assert (body["title"], body["share"], body["store"]) == ("Big book", True, "Shop")
    assert [p["person_id"] for p in body["people"]] == [ids.jordan, ids.casey]
    assert client.get(f"/api/ideas/{idea['id']}", headers=auth()).get_json() == body
    # a field left out stays as it was
    body = client.patch(f"/api/ideas/{idea['id']}", json={"share": False}, headers=mut()).get_json()
    assert (body["title"], body["share"], body["store"]) == ("Big book", False, "Shop")


@pytest.mark.parametrize("bad", [{"title": ""}, {"person_ids": "x"}, {"person_ids": [999999]}, {"url": "ftp://x"},
                                 {"share": 1}])
def test_patch_validation(client, ids, bad):
    idea = mk_idea(client, "Book", [ids.jordan])
    assert client.patch(f"/api/ideas/{idea['id']}", json=bad, headers=mut()).status_code == 400
    assert client.get(f"/api/ideas/{idea['id']}", headers=auth()).get_json() == idea


def test_retagging_keeps_a_taken_copy_and_untagging_it_leaves_the_gift(client, ids, con):
    idea = mk_idea(client, "Book", [ids.jordan, ids.riley])
    gift = mk_gift(client, ids.o, "Book", to=[ids.jordan], frm=[ids.alex])
    con.execute("UPDATE idea_people SET gift_id = ? WHERE idea_id = ? AND person_id = ?", (gift, idea["id"], ids.jordan))
    body = client.patch(f"/api/ideas/{idea['id']}", json={"person_ids": [ids.riley, ids.jordan, ids.casey]},
                        headers=mut()).get_json()
    states = {p["person_id"]: (p["state"], p["gift_id"], p["occasion_id"]) for p in body["people"]}
    assert states == {ids.jordan: ("taken", gift, ids.o), ids.riley: ("open", None, None),
                      ids.casey: ("open", None, None)}
    body = client.patch(f"/api/ideas/{idea['id']}", json={"person_ids": [ids.riley]}, headers=mut()).get_json()
    assert [p["person_id"] for p in body["people"]] == [ids.riley]
    assert client.get(f"/api/gifts/{gift}", headers=auth()).status_code == 200


def test_patch_no_people_deletes(client, ids):
    idea = mk_idea(client, "Book", [ids.jordan])
    r = client.patch(f"/api/ideas/{idea['id']}", json={"person_ids": []}, headers=mut())
    assert (r.status_code, r.get_json()) == (200, {"deleted": True})
    assert client.get(f"/api/ideas/{idea['id']}", headers=auth()).status_code == 404
    assert con_rows("SELECT COUNT(*) FROM idea_people") == [(0,)]


def test_delete(client, ids):
    idea = mk_idea(client, "Book", [ids.jordan, ids.riley])
    assert client.delete(f"/api/ideas/{idea['id']}", headers=mut()).status_code == 200
    assert client.get(f"/api/ideas/{idea['id']}", headers=auth()).status_code == 404
    assert client.delete(f"/api/ideas/{idea['id']}", headers=mut()).status_code == 404
    assert con_rows("SELECT COUNT(*) FROM idea_people") == [(0,)]


def test_missing_idea_is_404(client, ids):
    for r in (client.get("/api/ideas/999999", headers=auth()),
              client.patch("/api/ideas/999999", json={"title": "x"}, headers=mut()),
              client.delete("/api/ideas/999999", headers=mut())):
        assert (r.status_code, r.get_json()) == (404, {"error": "No such idea."})


def test_list_filter_by_person(client, ids):
    a = mk_idea(client, "Book", [ids.jordan, ids.riley])["id"]
    b = mk_idea(client, "Kite", [ids.riley])["id"]
    c = mk_idea(client, "Mug", [ids.casey])["id"]
    assert [i["id"] for i in client.get("/api/ideas", headers=auth()).get_json()] == [a, b, c]
    assert [i["id"] for i in client.get(f"/api/ideas?person={ids.riley}", headers=auth()).get_json()] == [a, b]
    assert [i["id"] for i in client.get(f"/api/ideas?person={ids.jordan}", headers=auth()).get_json()] == [a]
    assert client.get("/api/ideas?person=999999", headers=auth()).get_json() == []
    assert client.get("/api/ideas?person=abc", headers=auth()).status_code == 400
    # the list carries the full idea JSON
    assert client.get(f"/api/ideas?person={ids.casey}", headers=auth()).get_json() == \
        [client.get(f"/api/ideas/{c}", headers=auth()).get_json()]


def test_people_are_in_person_sort_order(client, ids, con):
    con.execute("UPDATE people SET sort_order = 0 WHERE id = ?", (ids.casey,))
    con.execute("UPDATE people SET sort_order = 5 WHERE id IN (?, ?)", (ids.jordan, ids.riley))
    body = mk_idea(client, "Book", [ids.riley, ids.jordan, ids.casey])
    assert [p["person_id"] for p in body["people"]] == [ids.casey, ids.jordan, ids.riley]


def test_copy_state(client, ids, con):
    idea = mk_idea(client, "Book", [ids.jordan, ids.riley])
    gift = mk_gift(client, ids.o, "Book", to=[ids.jordan], frm=[ids.alex])
    con.execute("UPDATE idea_people SET gift_id = ? WHERE idea_id = ? AND person_id = ?", (gift, idea["id"], ids.jordan))
    row = con.execute("SELECT * FROM ideas WHERE id = ?", (idea["id"],)).fetchone()
    assert ideas.copy_state(con, row, ids.jordan) == ("taken", None)
    assert ideas.copy_state(con, row, ids.riley) == ("open", None)
    # deleting the gift reopens the copy
    assert client.delete(f"/api/gifts/{gift}", headers=mut()).status_code == 200
    assert ideas.copy_state(con, row, ids.jordan) == ("open", None)


def test_deleting_a_person_with_ideas_is_409(client, ids, unlock):
    unlock()
    pat = client.post("/api/people", json={"name": "Pat"}, headers=mut()).get_json()["id"]
    idea = mk_idea(client, "Book", [pat])
    r = client.delete(f"/api/people/{pat}", headers=mut())
    assert (r.status_code, r.get_json()) == (409, {"error": "This person has ideas. Deactivate them instead."})
    assert client.delete(f"/api/ideas/{idea['id']}", headers=mut()).status_code == 200
    assert client.delete(f"/api/people/{pat}", headers=mut()).status_code == 200


# ---------------------------------------------------------------- the idle lock
def test_a_new_client_is_locked_and_cannot_tag_a_protected_person(client, ids):
    r = client.post("/api/ideas", json={"title": "x", "person_ids": [ids.kid1]}, headers=mut())
    assert (r.status_code, r.get_json()) == (423, {"error": "Locked", "locked": True})


def test_locked_protected_idea_is_missing(client, ids, unlock):
    unlock()
    secret = mk_idea(client, "ZZ-SECRET-IDEA-ROW", [ids.kid1, ids.jordan])["id"]
    visible = mk_idea(client, "Mug", [ids.jordan])["id"]
    lock(client)
    missing = (404, {"error": "No such idea."})
    for iid in (secret, 999999):
        for r in (client.get(f"/api/ideas/{iid}", headers=auth()),
                  client.patch(f"/api/ideas/{iid}", json={"title": "x"}, headers=mut()),
                  client.patch(f"/api/ideas/{iid}", json={"title": ""}, headers=mut()),   # 404 before 400
                  client.patch(f"/api/ideas/{iid}", json={"person_ids": []}, headers=mut()),
                  client.delete(f"/api/ideas/{iid}", headers=mut())):
            assert (r.status_code, r.get_json()) == missing, iid
    assert [i["id"] for i in client.get("/api/ideas", headers=auth()).get_json()] == [visible]
    assert [i["id"] for i in client.get(f"/api/ideas?person={ids.jordan}", headers=auth()).get_json()] == [visible]
    assert client.get(f"/api/ideas?person={ids.kid1}", headers=auth()).get_json() == []
    assert con_rows("SELECT title FROM ideas WHERE id = ?", secret) == [("ZZ-SECRET-IDEA-ROW",)]
    assert con_rows("SELECT COUNT(*) FROM idea_people WHERE idea_id = ?", secret) == [(2,)]


def test_locked_create_naming_protected_is_423(client, ids):
    for people in ([ids.kid1], [ids.jordan, ids.kid2]):
        r = client.post("/api/ideas", json={"title": "x", "person_ids": people}, headers=mut())
        assert (r.status_code, r.get_json()) == (423, {"error": "Locked", "locked": True})
    assert con_rows("SELECT COUNT(*) FROM ideas") == [(0,)]


def test_locked_tagging_a_protected_person_on_a_visible_idea_is_423(client, ids):
    idea = mk_idea(client, "Mug", [ids.jordan])
    r = client.patch(f"/api/ideas/{idea['id']}", json={"person_ids": [ids.jordan, ids.kid1]}, headers=mut())
    assert (r.status_code, r.get_json()) == (423, {"error": "Locked", "locked": True})
    assert con_rows("SELECT person_id FROM idea_people WHERE idea_id = ?", idea["id"]) == [(ids.jordan,)]


def test_locked_unprotected_idea_works(client, ids):
    idea = mk_idea(client, "Mug", [ids.jordan])
    assert client.get(f"/api/ideas/{idea['id']}", headers=auth()).get_json() == idea
    body = client.patch(f"/api/ideas/{idea['id']}", json={"title": "Big mug", "person_ids": [ids.jordan, ids.riley]},
                        headers=mut()).get_json()
    assert body["title"] == "Big mug" and [p["person_id"] for p in body["people"]] == [ids.jordan, ids.riley]
    assert [i["id"] for i in client.get("/api/ideas", headers=auth()).get_json()] == [idea["id"]]
    assert client.delete(f"/api/ideas/{idea['id']}", headers=mut()).status_code == 200


def test_an_idea_whose_taken_copy_points_at_a_protected_gift_is_protected(client, ids, con, unlock):
    """A taken copy's gift that gains a protected co-recipient hides the whole idea, so "taken" never hints at a hidden
    gift."""
    idea = mk_idea(client, "Mug", [ids.jordan])
    gift = mk_gift(client, ids.o, "Mug", to=[ids.jordan], frm=[ids.alex])
    con.execute("UPDATE idea_people SET gift_id = ? WHERE idea_id = ?", (gift, idea["id"]))
    assert not ideas.is_protected(con, idea["id"])
    con.execute("INSERT INTO gift_recipients (gift_id, person_id) VALUES (?, ?)", (gift, ids.kid1))
    assert ideas.is_protected(con, idea["id"])
    assert not ideas.visible(con, idea["id"], unlocked=False)
    assert ideas.visible(con, idea["id"], unlocked=True)
    assert ideas.visible(con, 999999, unlocked=False)   # missing counts as visible: the lookup answers 404
    assert client.get(f"/api/ideas/{idea['id']}", headers=auth()).status_code == 404
    assert client.get("/api/ideas", headers=auth()).get_json() == []
    assert client.get(f"/api/ideas?person={ids.jordan}", headers=auth()).get_json() == []
    unlock()
    assert client.get(f"/api/ideas/{idea['id']}", headers=auth()).status_code == 200


def test_list_json_filters_on_the_lock(con, ids):
    secret = mk_idea_direct("ZZ-SECRET-IDEA-ROW", [ids.kid1])
    seen = mk_idea_direct("Mug", [ids.jordan])
    assert [i["id"] for i in ideas.list_json(con, unlocked=False)] == [seen]
    assert [i["id"] for i in ideas.list_json(con, unlocked=True)] == [secret, seen]
    assert ideas.list_json(con, unlocked=False, person_id=ids.kid1) == []
    assert [i["id"] for i in ideas.list_json(con, unlocked=True, person_id=ids.kid1)] == [secret]


def test_unlocked_protected_idea_works(client, ids, unlock):
    unlock()
    idea = mk_idea(client, "Kite", [ids.kid1, ids.kid2])
    assert client.patch(f"/api/ideas/{idea['id']}", json={"share": True}, headers=mut()).get_json()["share"] is True
    assert [i["id"] for i in client.get(f"/api/ideas?person={ids.kid2}", headers=auth()).get_json()] == [idea["id"]]
    assert client.delete(f"/api/ideas/{idea['id']}", headers=mut()).status_code == 200


# ---------------------------------------------------------------- school mode
def test_school_mode_ideas_404(client, ids, monkeypatch):
    idea = mk_idea(client, "Mug", [ids.jordan])
    monkeypatch.setattr(settings, "MODE", "school")
    missing = client.get("/api/nothing-here", headers=auth()).get_json()
    for method, url in (("GET", "/api/ideas"), ("POST", "/api/ideas"), ("GET", f"/api/ideas/{idea['id']}"),
                        ("PATCH", f"/api/ideas/{idea['id']}"), ("DELETE", f"/api/ideas/{idea['id']}")):
        r = client.open(url, method=method, json={"title": "x", "person_ids": [ids.jordan]}, headers=mut())
        assert (r.status_code, r.get_json()) == (404, missing), (method, url)
    assert con_rows("SELECT title FROM ideas") == [("Mug",)]
