"""Typed givers: a gift's givers are people plus at most one typed name, for a one-off giver who is not a person. The
name joins the giver ids wherever the track, a board group, a wishlist label or the agent JSON is worked out, and is
remembered as a suggestion (only from gifts the caller can see)."""
import pytest
from conftest import auth, bearer, mk_gift, mk_person, mk_protected_gift, mk_token, mut
from test_exchanges import mk_exchange, put_draw
from test_wishlist import RESERVE, link_tab, no_network, wl  # noqa: F401  (fixtures)

import settings
import wishlist

NAME = "Uncle Casey"


def gift(client, gid, **kw):
    r = client.get(f"/api/gifts/{gid}", headers=auth(**kw))
    assert r.status_code == 200, r.get_json()
    return r.get_json()


def create(client, ids, **body):
    return client.post("/api/gifts", json={"occasion_id": ids.o, "title": "Thing", "recipient_ids": [ids.jordan],
                                           **body}, headers=mut())


def patch(client, gid, body):
    return client.patch(f"/api/gifts/{gid}", json=body, headers=mut())


def assign(client, gid, givers, expected, name=None, expected_name=None):
    return client.post(f"/api/gifts/{gid}/assign", json={"giver_ids": givers, "expected_giver_ids": expected,
                                                         "giver_name": name, "expected_giver_name": expected_name},
                       headers=mut())


def test_typed_name_only_is_theirs(client, ids):
    gid = mk_gift(client, ids.o, "Scarf", to=[ids.jordan], frm=[], giver_name=NAME)
    g = gift(client, gid)
    assert (g["giver_name"], g["giver_ids"], g["track"], g["status"]) == (NAME, [], "theirs", "told")
    assert g["told_at"]
    plain = gift(client, mk_gift(client, ids.o, "Plain", to=[ids.jordan]))
    assert (plain["giver_name"], plain["track"], plain["status"]) == (None, "ours", "idea")


def test_owner_and_name_is_ours(client, ids):
    g = gift(client, mk_gift(client, ids.o, "Scarf", to=[ids.jordan], frm=[ids.alex], giver_name=NAME))
    assert (g["giver_name"], g["giver_ids"], g["track"], g["status"], g["told_at"]) == (NAME, [ids.alex], "ours", "idea", None)


def test_santa_refused_with_name_only(client, ids, unlock):
    unlock()
    r = create(client, ids, giver_name=NAME, santa=True)
    assert (r.status_code, r.get_json()) == (400, {"error": "Santa is only for a gift from an owner."})
    assert create(client, ids, giver_ids=[ids.alex], giver_name=NAME, santa=True).status_code == 201


def test_exchange_gift_refuses_name(client, ids):
    ex = mk_exchange(client, ids)
    put_draw(client, ex, ids.alex, ids.jordan)
    r = create(client, ids, giver_ids=[ids.alex], giver_name=NAME, exchange_id=ex)
    assert (r.status_code, r.get_json()) == (400, {"error": "A gift for an exchange is from the drawer: no typed name."})
    gid = mk_gift(client, ids.o, "Mug", to=[ids.jordan], frm=[ids.alex], exchange_id=ex)
    assert patch(client, gid, {"giver_name": NAME}).status_code == 400
    named = mk_gift(client, ids.o, "Named", to=[ids.jordan], frm=[ids.alex], giver_name=NAME)
    patch(client, gid, {"exchange_id": None})
    assert patch(client, named, {"exchange_id": ex}).status_code == 400  # linking a gift with a name
    assert patch(client, named, {"exchange_id": ex, "giver_name": None}).status_code == 200


def test_name_trimmed_and_limits(client, ids):
    gid = mk_gift(client, ids.o, "Scarf", to=[ids.jordan], giver_name="  Uncle Casey  ")
    assert gift(client, gid)["giver_name"] == NAME
    r = patch(client, gid, {"giver_name": "x" * 81})
    assert (r.status_code, r.get_json()) == (400, {"error": "A giver's name is 1 to 80 characters."})
    assert patch(client, gid, {"giver_name": "x" * 80}).status_code == 200
    for bad in (5, ["Uncle"], True, {"a": 1}):
        r = patch(client, gid, {"giver_name": bad})
        assert (r.status_code, r.get_json()) == (400, {"error": "giver_name must be text."})
    assert patch(client, gid, {"giver_name": ""}).get_json()["giver_name"] is None
    patch(client, gid, {"giver_name": NAME})
    assert patch(client, gid, {"giver_name": None}).get_json()["giver_name"] is None
    patch(client, gid, {"giver_name": NAME})
    assert patch(client, gid, {"giver_name": "   "}).get_json()["giver_name"] is None
    assert patch(client, gid, {"title": "Other"}).get_json()["giver_name"] is None  # absent: unchanged
    patch(client, gid, {"giver_name": NAME})
    assert patch(client, gid, {"title": "Other"}).get_json()["giver_name"] == NAME


def test_patch_changes_track(client, ids):
    gid = mk_gift(client, ids.o, "Scarf", to=[ids.jordan], status="decided")
    g = patch(client, gid, {"giver_name": NAME}).get_json()
    assert (g["track"], g["status"], g["giver_name"]) == ("theirs", "told", NAME)
    assert g["told_at"]
    g = patch(client, gid, {"giver_name": None}).get_json()
    assert (g["track"], g["status"]) == ("ours", "decided")
    g = patch(client, gid, {"giver_ids": [ids.alex], "giver_name": NAME}).get_json()
    assert (g["track"], g["status"], g["giver_ids"], g["giver_name"]) == ("ours", "decided", [ids.alex], NAME)
    g = patch(client, gid, {"giver_ids": []}).get_json()  # the name stays: theirs
    assert (g["track"], g["status"], g["giver_name"]) == ("theirs", "told", NAME)
    assert patch(client, gid, {"status": "confirmed"}).get_json()["status"] == "confirmed"
    r = client.post(f"/api/gifts/{gid}/status", json={"status": "ordered"}, headers=mut())
    assert r.status_code == 400  # not on the theirs track


def test_not_counted_in_spending(client, ids):
    mk_gift(client, ids.o, "Mine", to=[ids.jordan], frm=[ids.alex], price="10.00")
    mk_gift(client, ids.o, "Named", to=[ids.jordan], giver_name=NAME, price="40.00")
    mk_gift(client, ids.o, "Named given", to=[ids.jordan], giver_name=NAME, price="25.00", status="given")
    s = client.get(f"/api/occasions/{ids.o}/summary", headers=auth()).get_json()
    [jordan] = [p for grp in s["groups"] for p in grp["people"] if p["id"] == ids.jordan]
    assert (jordan["spent_cents"], jordan["planned_cents"], jordan["gift_count"]) == (0, 1000, 3)


def test_board_groups_by_name(client, ids, unlock):
    unlock()
    pat = mk_person(client, "Pat", household="Pat & Lee")
    lee = mk_person(client, "Lee", household="Pat & Lee")
    a = mk_gift(client, ids.o, "Kite", to=[ids.kid1], giver_name=NAME, url="https://example.com/k")
    b = mk_gift(client, ids.o, "Drum", to=[ids.kid1], frm=[pat, lee])
    c = mk_gift(client, ids.o, "Bike", to=[ids.kid1], frm=[pat, lee], giver_name=NAME)
    d = mk_gift(client, ids.o, "Book", to=[ids.kid1], frm=[ids.alex], giver_name=NAME, price="5.00")
    u = mk_gift(client, ids.o, "Loose", to=[ids.kid1])
    brd = client.get(f"/api/occasions/{ids.o}/board/{ids.kid1}", headers=auth()).get_json()
    assert [g["id"] for g in brd["unassigned"]] == [u]
    got = [(x["label"], x["is_us"], [g["id"] for g in x["gifts"]]) for x in brd["givers"]]
    assert got == [("Pat & Lee", False, [b]), ("Pat & Lee & Uncle Casey", False, [c]), (NAME, False, [a]),
                   ("Alex & Uncle Casey", True, [d])]
    named = brd["givers"][2]
    assert named["giver_ids"] == [] and named["list_text"].startswith("Uncle Casey\n\n")
    assert named["list_text"] == "Uncle Casey\n\nKite - https://example.com/k"
    assert brd["split"] == {"santa": 0, "us": 1, "others": 3}
    assert {e["person_id"]: e["cents"] for e in brd["evenness"]}[ids.kid1] == 500


def test_assign_with_name_and_conflict(client, ids, unlock):
    unlock()
    gid = mk_gift(client, ids.o, "Kite", to=[ids.kid1])
    r = assign(client, gid, [], [], NAME, None)
    assert r.status_code == 200, r.get_json()
    assert (r.get_json()["giver_name"], r.get_json()["track"], r.get_json()["status"]) == (NAME, "theirs", "told")
    r = assign(client, gid, [ids.jordan], [], None, None)  # the old expectation
    assert (r.status_code, r.get_json()) == (409, {"error": "Someone just assigned this. Reload."})
    r = assign(client, gid, [ids.jordan], [], None, NAME)
    assert (r.status_code, r.get_json()["giver_ids"], r.get_json()["giver_name"]) == (200, [ids.jordan], None)
    r = assign(client, gid, [], [ids.jordan], None, None)  # unassigned: idea again
    assert (r.get_json()["status"], r.get_json()["told_at"], r.get_json()["giver_name"]) == ("idea", None, None)
    assert client.post(f"/api/gifts/{gid}/assign", json={"giver_ids": [], "expected_giver_ids": [],
                                                          "giver_name": 5}, headers=mut()).status_code == 400
    # A body without the name keys works as before.
    assert client.post(f"/api/gifts/{gid}/assign", json={"giver_ids": [ids.alex], "expected_giver_ids": []},
                       headers=mut()).status_code == 200


def test_suggestions(client, ids, clock, unlock):
    for name in (NAME, "Aunt Riley", " uncle casey "):
        mk_gift(client, ids.o, "Thing", to=[ids.jordan], giver_name=name)
        clock.advance(10)
    mk_gift(client, ids.o, "None", to=[ids.jordan])
    assert client.get("/api/giver-names", headers=auth()).get_json() == ["uncle casey", "Aunt Riley"]
    clock.advance(10)
    mk_protected_gift(ids, frm=[], giver_name="Grandpa Secret")
    assert "Grandpa Secret" not in client.get("/api/giver-names", headers=auth()).get_json()
    unlock()
    assert client.get("/api/giver-names", headers=auth()).get_json() == ["Grandpa Secret", "uncle casey", "Aunt Riley"]


def test_suggestions_keep_at_most_fifty(client, ids, clock):
    for i in range(55):
        mk_gift(client, ids.o, "Thing", to=[ids.jordan], giver_name=f"Name {i}")
        clock.advance(1)
    got = client.get("/api/giver-names", headers=auth()).get_json()
    assert len(got) == 50 and got[0] == "Name 54"


def test_school_mode_name_is_400_and_key_absent(client, ids, monkeypatch):
    monkeypatch.setattr(settings, "MODE", "school")
    for value in (NAME, None, ""):
        r = create(client, ids, giver_name=value)
        assert (r.status_code, r.get_json()) == (400, {"error": "Typed givers are not used in school mode."})
    gid = mk_gift(client, ids.o, "Mug", to=[ids.jordan])
    assert "giver_name" not in gift(client, gid)
    assert patch(client, gid, {"giver_name": NAME}).status_code == 400
    assert "giver_name" not in patch(client, gid, {"title": "Cup"}).get_json()
    assert client.get("/api/giver-names", headers=auth()).status_code == 404
    full = mk_token("full", "Everything")
    assert "giver_name" not in client.get(f"/api/v1/gifts/{gid}", headers=bearer(full)).get_json()


def test_agent_json(client, ids):
    gid = mk_gift(client, ids.o, "Scarf", to=[ids.jordan], giver_name=NAME)
    assert client.get(f"/api/v1/gifts/{gid}", headers=bearer(mk_token("full", "All"))).get_json()["giver_name"] == NAME
    patch(client, gid, {"giver_ids": [ids.alex], "status": "decided"})  # shop and delivery read decided
    for scope in ("shop", "delivery"):
        j = client.get(f"/api/v1/gifts/{gid}", headers=bearer(mk_token(scope, scope))).get_json()
        assert j["title"] == "Scarf" and "giver_name" not in j
    r = client.post("/api/v1/gifts", json={"title": "New", "recipient_ids": [ids.jordan], "giver_name": NAME},
                    headers=bearer(mk_token("full", "Maker")))
    assert (r.status_code, r.get_json()) == (400, {"error": "Typed givers are not available to agents."})


def test_wishlist_label_includes_name(client, con, ids):
    pat = mk_person(client, "Pat", household="Pat & Lee")
    assert wishlist.label(con, [], NAME) == NAME
    assert wishlist.label(con, [], "  Uncle Casey ") == NAME
    assert wishlist.label(con, [pat], NAME) == "Pat & Lee & Uncle Casey"
    assert wishlist.label(con, [ids.alex], NAME) == "Us & Uncle Casey"
    assert wishlist.label(con, [], None) is None and wishlist.label(con, [], "") is None
    assert wishlist.label(con, [ids.jordan], "Jordan") == "Jordan"  # one distinct part
    assert wishlist.label(con, [], "y" * 90) == "y" * 80


@pytest.fixture
def kite(client, ids, wl, unlock):  # noqa: F811
    """Kid 1's tab linked and the Lego wish (11) on the board as an unassigned gift."""
    unlock()
    link_tab(client, ids.kid1, 3)
    client.get(f"/api/occasions/{ids.o}/board/{ids.kid1}", headers=auth())
    r = client.post(f"/api/occasions/{ids.o}/board/{ids.kid1}/wishes/11", json={}, headers=mut())
    assert r.status_code == 201, r.get_json()
    return r.get_json()["id"]


def test_a_named_giver_reserves_the_wish_under_the_name(client, ids, wl, kite):  # noqa: F811
    assert assign(client, kite, [], [], NAME, None).status_code == 200
    assert wl.posts() == [(RESERVE, {"by": NAME})]
    assert patch(client, kite, {"giver_name": "Aunt Riley"}).status_code == 200
    assert wl.posts()[-1] == (RESERVE, {"by": "Aunt Riley"})
    assert assign(client, kite, [], [], None, "Aunt Riley").status_code == 200
    assert wl.posts()[-1] == (RESERVE, {"by": None})
