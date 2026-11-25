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


# ---------------------------------------------------------------- taking a copy
def take(client, idea_id, person_id, **body):
    return client.post(f"/api/ideas/{idea_id}/take/{person_id}", json=body, headers=mut())


def states(client, idea_id):
    body = client.get(f"/api/ideas/{idea_id}", headers=auth()).get_json()
    return {p["person_id"]: (p["state"], p["gift_id"]) for p in body["people"]}


def test_take_for_one_leaves_others_open(client, ids, unlock):
    unlock()
    idea = mk_idea(client, "Animal tracking bracelet", [ids.kid1, ids.kid2, ids.kid3], store="Shop",
                   url="https://example.com/b", price="24.99", notes="Size S")
    r = take(client, idea["id"], ids.kid2, giver_ids=[], giver_name="Uncle Casey")
    assert r.status_code == 201, r.get_json()
    gift = r.get_json()
    assert gift["recipient_ids"] == [ids.kid2]
    assert (gift["title"], gift["url"], gift["store"], gift["price_cents"], gift["notes"]) == \
        ("Animal tracking bracelet", "https://example.com/b", "Shop", 2499, "Size S")
    assert (gift["occasion_id"], gift["giver_ids"], gift["giver_name"], gift["track"]) == \
        (ids.o, [], "Uncle Casey", "theirs")
    body = client.get(f"/api/ideas/{idea['id']}", headers=auth()).get_json()
    got = {p["person_id"]: (p["state"], p["gift_id"], p["occasion_id"]) for p in body["people"]}
    assert got == {ids.kid1: ("open", None, None), ids.kid2: ("taken", gift["id"], ids.o),
                   ids.kid3: ("open", None, None)}


def test_take_with_an_owner_and_an_occasion(client, ids, con):
    later = con.execute("INSERT INTO occasions (name, kind, date) VALUES ('Birthday', 'birthday', '2027-03-01')").lastrowid
    idea = mk_idea(client, "Kite", [ids.jordan])
    gift = take(client, idea["id"], ids.jordan, giver_ids=[ids.alex], occasion_id=later).get_json()
    assert (gift["occasion_id"], gift["giver_ids"], gift["track"]) == (later, [ids.alex], "ours")


@pytest.mark.parametrize("body", [{}, {"giver_ids": []}, {"giver_ids": [], "giver_name": " "},
                                  {"giver_name": None}])
def test_take_needs_a_giver(client, ids, body):
    idea = mk_idea(client, "Kite", [ids.jordan])
    r = take(client, idea["id"], ids.jordan, **body)
    assert (r.status_code, r.get_json()) == (400, {"error": "Say who is giving it."})
    assert con_rows("SELECT COUNT(*) FROM gifts") == [(0,)]
    assert states(client, idea["id"]) == {ids.jordan: ("open", None)}


@pytest.mark.parametrize("body", [{"giver_ids": "x"}, {"giver_ids": [999999]}, {"giver_name": "x" * 81},
                                  {"giver_ids": [1], "occasion_id": 999999}, {"giver_ids": [1], "occasion_id": "x"}])
def test_take_validation(client, ids, body):
    idea = mk_idea(client, "Kite", [ids.jordan])
    body = {k: [ids.alex] if v == [1] else v for k, v in body.items()}
    assert take(client, idea["id"], ids.jordan, **body).status_code == 400
    assert con_rows("SELECT COUNT(*) FROM gifts") == [(0,)]


def test_take_without_an_occasion(client, ids, con):
    idea = mk_idea(client, "Kite", [ids.jordan])
    con.execute("UPDATE occasions SET archived = 1")
    r = take(client, idea["id"], ids.jordan, giver_ids=[ids.alex])
    assert (r.status_code, r.get_json()) == (400, {"error": "Create an occasion first."})


def test_take_untagged_person(client, ids):
    idea = mk_idea(client, "Kite", [ids.jordan])
    for pid in (ids.riley, ids.kid1, 999999):
        r = take(client, idea["id"], pid, giver_ids=[ids.alex])
        assert r.status_code == 404, pid
    assert take(client, 999999, ids.jordan, giver_ids=[ids.alex]).get_json() == {"error": "No such idea."}
    assert con_rows("SELECT COUNT(*) FROM gifts") == [(0,)]


def test_take_twice_is_409(client, ids):
    idea = mk_idea(client, "Kite", [ids.jordan, ids.riley])
    assert take(client, idea["id"], ids.jordan, giver_ids=[ids.alex]).status_code == 201
    r = take(client, idea["id"], ids.jordan, giver_ids=[ids.sam])
    assert (r.status_code, r.get_json()) == (409, {"error": "Someone just took this. Reload."})
    assert con_rows("SELECT COUNT(*) FROM gifts") == [(1,)]
    assert take(client, idea["id"], ids.riley, giver_ids=[ids.sam]).status_code == 201


def test_take_race(client, ids, monkeypatch):
    """Two owners take the same copy at once: the second's check passed before the first committed, so the guard
    inside gifts.create's transaction is what refuses it, and its gift is rolled back."""
    import gifts
    from common import ApiError
    idea = mk_idea(client, "Kite", [ids.jordan])["id"]
    a, b = db.connect(settings.DB_PATH), db.connect(settings.DB_PATH)
    real = gifts.create
    first = {}

    def racing_create(con, user, data, **kw):
        if not first:  # the first owner commits between the second owner's check and their insert
            monkeypatch.setattr(gifts, "create", real)
            first["gid"] = ideas.take(a, USER, idea, ids.jordan, {"giver_ids": [ids.alex]})
        return real(con, user, data, **kw)

    monkeypatch.setattr(gifts, "create", racing_create)
    try:
        with pytest.raises(ApiError) as err:
            ideas.take(b, USER, idea, ids.jordan, {"giver_ids": [ids.sam]})
        assert (err.value.status, err.value.message) == (409, "Someone just took this. Reload.")
    finally:
        a.close()
        b.close()
    assert con_rows("SELECT id FROM gifts") == [(first["gid"],)]
    assert con_rows("SELECT gift_id FROM idea_people") == [(first["gid"],)]


def test_locked_take(client, ids, unlock):
    unlock()
    secret = mk_idea(client, "ZZ-SECRET-IDEA-ROW", [ids.kid1, ids.jordan])["id"]
    lock(client)
    for pid in (ids.kid1, ids.jordan):
        r = take(client, secret, pid, giver_ids=[ids.alex])
        assert (r.status_code, r.get_json()) == (404, {"error": "No such idea."})
    visible = mk_idea(client, "Mug", [ids.jordan])["id"]
    r = take(client, visible, ids.jordan, giver_ids=[ids.alex], santa=True)
    assert (r.status_code, r.get_json()) == (423, {"error": "Locked", "locked": True})
    assert take(client, visible, ids.jordan, giver_ids=[ids.alex]).status_code == 201   # unprotected works locked
    assert con_rows("SELECT COUNT(*) FROM gifts") == [(1,)]


# ---------------------------------------------------------------- reopening
def test_delete_gift_reopens_only_that_copy(client, ids, unlock):
    unlock()
    idea = mk_idea(client, "Bracelet", [ids.kid1, ids.kid2, ids.kid3])["id"]
    g1 = take(client, idea, ids.kid1, giver_ids=[ids.alex]).get_json()["id"]
    g2 = take(client, idea, ids.kid2, giver_ids=[ids.alex]).get_json()["id"]
    assert client.delete(f"/api/gifts/{g2}", headers=mut()).status_code == 200
    assert states(client, idea) == {ids.kid1: ("taken", g1), ids.kid2: ("open", None), ids.kid3: ("open", None)}


def test_back_to_ideas(client, ids, unlock):
    unlock()
    idea = mk_idea(client, "Bracelet", [ids.kid1, ids.kid2])["id"]
    g1 = take(client, idea, ids.kid1, giver_ids=[ids.alex]).get_json()["id"]
    g2 = take(client, idea, ids.kid2, giver_name="Uncle Casey").get_json()["id"]
    r = client.post(f"/api/gifts/{g2}/back-to-ideas", json={}, headers=mut())
    assert r.status_code == 200, r.get_json()
    assert client.get(f"/api/gifts/{g2}", headers=auth()).status_code == 404
    assert states(client, idea) == {ids.kid1: ("taken", g1), ids.kid2: ("open", None)}


def test_back_to_ideas_needs_an_idea_gift(client, ids):
    gift = mk_gift(client, ids.o, "Mug", to=[ids.jordan], frm=[ids.alex])
    r = client.post(f"/api/gifts/{gift}/back-to-ideas", json={}, headers=mut())
    assert (r.status_code, r.get_json()) == (400, {"error": "This gift did not come from an idea."})
    assert client.post("/api/gifts/999999/back-to-ideas", json={}, headers=mut()).status_code == 404
    assert client.get(f"/api/gifts/{gift}", headers=auth()).status_code == 200


def test_back_to_ideas_goes_through_the_wishlist_wrapper(client, ids, monkeypatch):
    import wishlist
    calls = []
    monkeypatch.setattr(wishlist, "delete_gift", lambda con, gid: calls.append(gid))
    idea = mk_idea(client, "Mug", [ids.jordan])["id"]
    gift = take(client, idea, ids.jordan, giver_ids=[ids.alex]).get_json()["id"]
    assert client.post(f"/api/gifts/{gift}/back-to-ideas", json={}, headers=mut()).status_code == 200
    assert calls == [gift]


def test_removing_recipient_reopens(client, ids, unlock):
    unlock()
    idea = mk_idea(client, "Bracelet", [ids.kid1, ids.kid2, ids.kid3])["id"]
    g1 = take(client, idea, ids.kid1, giver_ids=[ids.alex]).get_json()["id"]
    g2 = take(client, idea, ids.kid2, giver_ids=[ids.alex]).get_json()["id"]
    r = client.patch(f"/api/gifts/{g2}", json={"recipient_ids": [ids.jordan]}, headers=mut())
    assert r.status_code == 200, r.get_json()
    assert states(client, idea) == {ids.kid1: ("taken", g1), ids.kid2: ("open", None), ids.kid3: ("open", None)}
    # adding a recipient leaves the copy alone
    client.patch(f"/api/gifts/{g1}", json={"recipient_ids": [ids.kid1, ids.jordan]}, headers=mut())
    assert states(client, idea)[ids.kid1] == ("taken", g1)


def test_clearing_givers_on_idea_gift_is_400(client, ids, unlock):
    unlock()
    idea = mk_idea(client, "Kite", [ids.jordan, ids.riley])["id"]
    gift = take(client, idea, ids.jordan, giver_ids=[ids.alex]).get_json()["id"]
    named = take(client, idea, ids.riley, giver_name="Uncle Casey").get_json()["id"]
    back = (400, {"error": "Send it back to ideas instead."})
    for gid, body in ((gift, {"giver_ids": []}), (gift, {"giver_ids": [], "giver_name": None}),
                      (named, {"giver_name": ""}), (named, {"giver_name": None, "giver_ids": []})):
        r = client.patch(f"/api/gifts/{gid}", json=body, headers=mut())
        assert (r.status_code, r.get_json()) == back, body
    r = client.post(f"/api/gifts/{gift}/assign", json={"giver_ids": [], "expected_giver_ids": [ids.alex]},
                    headers=mut())
    assert (r.status_code, r.get_json()) == back
    r = client.post(f"/api/gifts/{named}/assign", json={"giver_ids": [], "expected_giver_ids": [],
                                                         "expected_giver_name": "Uncle Casey"}, headers=mut())
    assert (r.status_code, r.get_json()) == back
    assert con_rows("SELECT person_id FROM gift_givers WHERE gift_id = ?", gift) == [(ids.alex,)]
    assert con_rows("SELECT giver_name FROM gifts WHERE id = ?", named) == [("Uncle Casey",)]
    # changing the giver is fine
    r = client.patch(f"/api/gifts/{named}", json={"giver_name": None, "giver_ids": [ids.sam]}, headers=mut())
    assert r.status_code == 200, r.get_json()
    # a plain gift may still be unassigned
    plain = mk_gift(client, ids.o, "Mug", to=[ids.jordan], frm=[ids.alex])
    assert client.patch(f"/api/gifts/{plain}", json={"giver_ids": []}, headers=mut()).status_code == 200


def test_taken_gift_turning_protected_hides_the_idea(client, ids, unlock):
    unlock()
    idea = mk_idea(client, "Kite", [ids.jordan, ids.riley], share=True)["id"]
    gift = take(client, idea, ids.jordan, giver_ids=[ids.alex]).get_json()["id"]
    assert client.patch(f"/api/gifts/{gift}", json={"recipient_ids": [ids.jordan, ids.kid1]},
                        headers=mut()).status_code == 200
    lock(client)
    assert client.get(f"/api/ideas/{idea}", headers=auth()).status_code == 404
    assert take(client, idea, ids.riley, giver_ids=[ids.alex]).status_code == 404
    assert client.get("/api/ideas", headers=auth()).get_json() == []
    assert client.get(f"/api/ideas?person={ids.riley}", headers=auth()).get_json() == []
    assert client.get("/api/ideas/share", headers=auth()).get_json() == {"text": ""}
    assert client.get(f"/api/ideas/share?people={ids.riley}", headers=auth()).get_json() == {"text": ""}


# ---------------------------------------------------------------- move a gift to ideas
def test_to_idea(client, ids, unlock):
    unlock()
    gift = mk_gift(client, ids.o, "Puzzle", to=[ids.kid1, ids.kid2], url="https://example.com/p", store="Shop",
                   price="9.50", notes="1000 pieces")
    r = client.post(f"/api/gifts/{gift}/to-idea", json={}, headers=mut())
    assert r.status_code == 201, r.get_json()
    idea = r.get_json()
    assert (idea["title"], idea["url"], idea["store"], idea["price_cents"], idea["notes"], idea["share"]) == \
        ("Puzzle", "https://example.com/p", "Shop", 950, "1000 pieces", False)
    assert [(p["person_id"], p["state"]) for p in idea["people"]] == [(ids.kid1, "open"), (ids.kid2, "open")]
    assert client.get(f"/api/gifts/{gift}", headers=auth()).status_code == 404
    assert client.get(f"/api/ideas/{idea['id']}", headers=auth()).get_json() == idea


def test_to_idea_refusals(client, ids):
    given = mk_gift(client, ids.o, "Mug", to=[ids.jordan], frm=[ids.alex])
    named = mk_gift(client, ids.o, "Mug", to=[ids.jordan], giver_name="Uncle Casey")
    idea = mk_idea(client, "Kite", [ids.jordan])["id"]
    taken = take(client, idea, ids.jordan, giver_ids=[ids.alex]).get_json()["id"]
    for gid in (given, named, taken):
        r = client.post(f"/api/gifts/{gid}/to-idea", json={}, headers=mut())
        assert r.status_code == 400, gid
    assert client.post("/api/gifts/999999/to-idea", json={}, headers=mut()).status_code == 404
    assert con_rows("SELECT COUNT(*) FROM gifts") == [(3,)]
    assert con_rows("SELECT COUNT(*) FROM ideas") == [(1,)]


def test_to_idea_keeps_the_wish_and_refuses_a_shared_wish_gift(client, ids, con):
    one = mk_gift(client, ids.o, "Wish", to=[ids.jordan])
    two = mk_gift(client, ids.o, "Wish 2", to=[ids.jordan, ids.riley])
    con.execute("UPDATE gifts SET wishlist_item_id = 11 WHERE id = ?", (one,))
    con.execute("UPDATE gifts SET wishlist_item_id = 12 WHERE id = ?", (two,))
    r = client.post(f"/api/gifts/{two}/to-idea", json={}, headers=mut())
    assert (r.status_code, r.get_json()) == (400, {"error": "Remove the other recipients first."})
    idea = client.post(f"/api/gifts/{one}/to-idea", json={}, headers=mut()).get_json()
    assert idea["wishlist_item_id"] == 11
    assert con_rows("SELECT COUNT(*) FROM gifts WHERE wishlist_item_id = 11") == [(0,)]
    # a wish some idea already names: 409, and the gift stays
    three = mk_gift(client, ids.o, "Wish 3", to=[ids.jordan])
    con.execute("UPDATE gifts SET wishlist_item_id = 11 WHERE id = ?", (three,))
    r = client.post(f"/api/gifts/{three}/to-idea", json={}, headers=mut())
    assert (r.status_code, r.get_json()) == (409, {"error": "This wish is already an idea. Reload."})
    assert client.get(f"/api/gifts/{three}", headers=auth()).status_code == 200


def test_to_idea_checks_inside_its_transaction(client, ids, monkeypatch):
    """Another owner assigns the gift between to_idea's first read and its write: the checks run again inside the
    transaction, so the assigned gift is refused (400) and stays, and no idea is made."""
    import contextlib
    gift = mk_gift(client, ids.o, "Wish", to=[ids.jordan])
    real = db.transaction
    fired = []

    @contextlib.contextmanager
    def racing(con):
        if not fired:
            fired.append(1)
            other = db.connect(settings.DB_PATH)
            other.execute("INSERT INTO gift_givers (gift_id, person_id) VALUES (?, ?)", (gift, ids.alex))
            other.close()
        with real(con):
            yield

    monkeypatch.setattr(db, "transaction", racing)
    r = client.post(f"/api/gifts/{gift}/to-idea", json={}, headers=mut())
    assert (r.status_code, r.get_json()) == (400, {"error": "Only a gift nobody is giving yet can move to ideas."})
    assert fired
    assert con_rows("SELECT COUNT(*) FROM gifts WHERE id = ?", gift) == [(1,)]
    assert con_rows("SELECT COUNT(*) FROM ideas") == [(0,)]


def test_locked_to_idea_of_a_protected_gift_is_404(client, ids):
    from conftest import mk_protected_gift
    gift = mk_protected_gift(ids, title="ZZ-SECRET", to=[ids.kid1], frm=[])
    for path in ("to-idea", "back-to-ideas"):
        r = client.post(f"/api/gifts/{gift}/{path}", json={}, headers=mut())
        assert (r.status_code, r.get_json()) == (404, {"error": "No such gift."})
    assert con_rows("SELECT COUNT(*) FROM ideas") == [(0,)]


# ---------------------------------------------------------------- the share list
def share(client, query=""):
    return client.get(f"/api/ideas/share{query}", headers=auth())


def test_share_text_format(client, ids, unlock):
    unlock()
    bracelet = mk_idea(client, "Animal tracking bracelet", [ids.kid1, ids.kid2, ids.kid3], store="Shop",
                       url="https://example.com/b", share=True, notes="never shared", price="5")["id"]
    take(client, bracelet, ids.kid2, giver_ids=[ids.alex])
    mk_idea(client, "Puzzle", [ids.kid2], url="https://example.com/p", share=True)
    mk_idea(client, "Secret plan", [ids.kid1])
    r = share(client, f"?people={ids.kid1},{ids.kid2},{ids.kid3}")
    assert r.status_code == 200, r.get_json()
    assert r.get_json() == {"text": "Kid 1\n- Animal tracking bracelet - Shop - https://example.com/b\n\n"
                                    "Kid 2\n- Puzzle - https://example.com/p\n\n"
                                    "Kid 3\n- Animal tracking bracelet - Shop - https://example.com/b"}
    assert share(client, f"?people={ids.kid3}").get_json() == \
        {"text": "Kid 3\n- Animal tracking bracelet - Shop - https://example.com/b"}
    assert share(client, f"?people={ids.jordan}").get_json() == {"text": ""}


def test_share_locked(client, ids, unlock):
    unlock()
    mk_idea(client, "Bracelet", [ids.kid1], share=True)
    mk_idea(client, "Mug", [ids.jordan], share=True)
    mk_idea(client, "Kite", [ids.riley, ids.kid2], share=True)
    lock(client)
    for q in (f"?people={ids.kid1}", f"?people={ids.jordan},{ids.kid2}"):
        r = share(client, q)
        assert (r.status_code, r.get_json()) == (423, {"error": "Locked", "locked": True}), q
    assert share(client).get_json() == {"text": "Jordan\n- Mug"}
    for bad in ("?people=x", "?people=", f"?people={ids.jordan},", "?people=-1", "?people=999999"):
        assert share(client, bad).status_code == 400, bad


def test_unlocked_share_without_people_lists_everyone(client, ids, unlock):
    unlock()
    mk_idea(client, "Bracelet", [ids.kid1], share=True)
    mk_idea(client, "Mug", [ids.jordan], share=True)
    assert share(client).get_json() == {"text": "Jordan\n- Mug\n\nKid 1\n- Bracelet"}


# ---------------------------------------------------------------- the board
def test_board_lists_open_ideas(client, ids, unlock):
    unlock()
    bracelet = mk_idea(client, "Bracelet", [ids.kid1, ids.kid2])["id"]
    kite = mk_idea(client, "Kite", [ids.kid1])["id"]
    take(client, kite, ids.kid1, giver_ids=[ids.alex])
    take(client, bracelet, ids.kid2, giver_ids=[ids.alex])
    board = client.get(f"/api/occasions/{ids.o}/board/{ids.kid1}", headers=auth()).get_json()
    assert [(i["id"], i["copy_state"]) for i in board["ideas"]] == [(bracelet, "open")]
    assert board["ideas"][0]["title"] == "Bracelet"
    # the gift taken from the kite is an ordinary assigned gift on the board, not unassigned
    assert board["unassigned"] == []
    board2 = client.get(f"/api/occasions/{ids.o}/board/{ids.kid2}", headers=auth()).get_json()
    assert board2["ideas"] == []


def test_school_mode_take_share_and_moves_404(client, ids, monkeypatch):
    idea = mk_idea(client, "Mug", [ids.jordan])["id"]
    gift = mk_gift(client, ids.o, "Kite", to=[ids.jordan])
    monkeypatch.setattr(settings, "MODE", "school")
    missing = client.get("/api/nothing-here", headers=auth()).get_json()
    for method, url in (("POST", f"/api/ideas/{idea}/take/{ids.jordan}"), ("GET", "/api/ideas/share"),
                        ("POST", f"/api/gifts/{gift}/to-idea"), ("POST", f"/api/gifts/{gift}/back-to-ideas")):
        r = client.open(url, method=method, json={"giver_ids": [ids.alex]}, headers=mut())
        assert (r.status_code, r.get_json()) == (404, missing), (method, url)
    assert con_rows("SELECT COUNT(*) FROM gifts") == [(1,)]


# ---------------------------------------------------------------- from_idea on gift JSON (the UI's Back to ideas)
def test_gift_json_says_whether_it_came_from_an_idea(client, ids, unlock, monkeypatch):
    unlock()
    idea = mk_idea(client, "Bracelet", [ids.kid1])["id"]
    taken = take(client, idea, ids.kid1, giver_ids=[ids.alex]).get_json()
    assert taken["from_idea"] is True
    plain = mk_gift(client, ids.o, "Mug", to=[ids.kid1])
    assert client.get(f"/api/gifts/{plain}", headers=auth()).get_json()["from_idea"] is False
    assert client.get(f"/api/gifts/{taken['id']}", headers=auth()).get_json()["from_idea"] is True
    board = client.get(f"/api/occasions/{ids.o}/board/{ids.kid1}", headers=auth()).get_json()
    assert [g["from_idea"] for g in board["unassigned"]] == [False]
    assert [g["from_idea"] for grp in board["givers"] for g in grp["gifts"]] == [True]
    monkeypatch.setattr(settings, "MODE", "school")
    assert "from_idea" not in client.get(f"/api/gifts/{plain}", headers=auth()).get_json()
