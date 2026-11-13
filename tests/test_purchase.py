"""After purchase: ship-to addresses, the location tree, order fields and the ship-to check, and the three lists.
The lock rules for all of it are pinned here and walked by tests/test_privacy.py."""
import pytest

import db
import settings
from conftest import auth, con_rows, mk_gift, mk_protected_gift, mut
import test_privacy
from test_privacy import MARKERS, api_rules, protected_state, urls, walk_gets

LOCKED = {"error": "Locked", "locked": True}
world = test_privacy.world  # the fixture of the privacy walk, reused below


@pytest.fixture
def school(monkeypatch):
    monkeypatch.setattr(settings, "MODE", "school")


# ---------------------------------------------------------------- helpers
def post(client, url, body=None):
    return client.post(url, json=body or {}, headers=mut())


def patch(client, url, body):
    return client.patch(url, json=body, headers=mut())


def delete(client, url):
    return client.delete(url, headers=mut())


def get(client, url):
    r = client.get(url, headers=auth())
    assert r.status_code == 200, r.get_json()
    return r.get_json()


def make_location(client, name, parent_id=None, **extra):
    r = post(client, "/api/locations", {"name": name, "parent_id": parent_id, **extra})
    assert r.status_code == 201, r.get_json()
    return r.get_json()["id"]


def make_chain(client):
    garage = make_location(client, "Garage")
    shelf = make_location(client, "Top shelf", garage)
    return garage, shelf, make_location(client, "Blue bin", shelf)


def make_address(client, label, **extra):
    r = post(client, "/api/addresses", {"label": label, **extra})
    assert r.status_code == 201, r.get_json()
    return r.get_json()["id"]


def sql(query, *params):
    """A write straight to the database (past the API), for setting up gifts in places and on orders."""
    c = db.connect(settings.DB_PATH)
    try:
        c.execute(query, params)
    finally:
        c.close()


def homes():
    return [r[0] for r in con_rows("SELECT id FROM addresses WHERE is_home = 1")]


# ---------------------------------------------------------------- addresses
def test_exactly_one_home_address(client):
    assert get(client, "/api/addresses") == [{"id": 1, "label": "Home", "is_home": True}]
    gp = make_address(client, "Grandparents' house")
    assert homes() == [1]
    assert patch(client, f"/api/addresses/{gp}", {"is_home": True}).get_json() == {"id": gp, "label": "Grandparents' house", "is_home": True}
    assert homes() == [gp]
    assert [a["id"] for a in get(client, "/api/addresses")][0] == gp   # home first
    r = patch(client, f"/api/addresses/{gp}", {"is_home": False})
    assert r.status_code == 400 and homes() == [gp]
    assert make_address(client, "Office", is_home=True) and len(homes()) == 1
    assert patch(client, "/api/addresses/999", {"label": "x"}).status_code == 404


def test_address_labels_are_validated(client):
    for body in ({}, {"label": ""}, {"label": "x" * 101}, {"label": 5}, {"label": "ok", "is_home": "yes"}):
        assert post(client, "/api/addresses", body).status_code == 400, body
    assert [r[0] for r in con_rows("SELECT label FROM addresses")] == ["Home"]


def test_the_home_address_cannot_be_deleted_and_an_address_on_an_order_cannot_either(client, ids, unlock):
    unlock()
    gp, spare = make_address(client, "Grandparents' house"), make_address(client, "Spare")
    assert delete(client, "/api/addresses/1").status_code == 409
    g = mk_gift(client, ids.o, "Camera", to=[ids.jordan], frm=[ids.alex])
    sql("UPDATE gifts SET ship_to_id = ? WHERE id = ?", gp, g)
    r = delete(client, f"/api/addresses/{gp}")
    assert r.status_code == 409 and con_rows("SELECT COUNT(*) FROM addresses") == [(3,)]
    assert delete(client, f"/api/addresses/{spare}").status_code == 200
    assert delete(client, "/api/addresses/999").status_code == 404


# ---------------------------------------------------------------- locations
def test_location_paths_use_the_arrow_and_come_in_tree_order(client):
    garage, shelf, bin_ = make_chain(client)
    closet = make_location(client, "Hall closet", sort_order=-1)
    tree = get(client, "/api/locations")
    assert [(t["path"], t["depth"]) for t in tree] == [
        ("Hall closet", 0), ("Garage", 0), ("Garage › Top shelf", 1), ("Garage › Top shelf › Blue bin", 2)]
    assert tree[3] == {"id": bin_, "parent_id": shelf, "name": "Blue bin", "sort_order": 0,
                       "path": "Garage › Top shelf › Blue bin", "depth": 2}
    assert closet and garage


def test_a_location_cannot_be_its_own_ancestor(client):
    garage, shelf, bin_ = make_chain(client)
    for target, parent in ((garage, garage), (garage, bin_), (garage, shelf), (shelf, bin_), (shelf, shelf)):
        r = patch(client, f"/api/locations/{target}", {"parent_id": parent})
        assert r.status_code == 400, (target, parent)
    assert dict(con_rows("SELECT id, parent_id FROM locations")) == {garage: None, shelf: garage, bin_: shelf}
    # legal moves still work: to the top, under a sibling, and a rename that restates the parent
    assert patch(client, f"/api/locations/{bin_}", {"parent_id": None}).status_code == 200
    assert patch(client, f"/api/locations/{shelf}", {"parent_id": bin_}).status_code == 200      # bin_ is no longer below shelf
    assert patch(client, f"/api/locations/{garage}", {"name": "Big garage", "parent_id": None}).status_code == 200


def test_bad_location_bodies_are_400(client):
    garage = make_location(client, "Garage")
    for body in ({}, {"name": ""}, {"name": "x" * 101}, {"name": 5}, {"name": "A", "parent_id": 999},
                 {"name": "A", "parent_id": "1"}, {"name": "A", "parent_id": True}, {"name": "A", "sort_order": "1"}):
        assert post(client, "/api/locations", body).status_code == 400, body
    assert patch(client, f"/api/locations/{garage}", {"parent_id": 999}).status_code == 400
    assert patch(client, "/api/locations/999", {"name": "x"}).status_code == 404
    assert len(con_rows("SELECT id FROM locations")) == 1


def test_a_location_holding_a_gift_cannot_be_deleted(client, ids, unlock):
    unlock()
    garage, shelf, bin_ = make_chain(client)
    g = mk_gift(client, ids.o, "Camera", to=[ids.jordan], frm=[ids.alex])
    sql("UPDATE gifts SET location_id = ? WHERE id = ?", bin_, g)
    assert delete(client, f"/api/locations/{garage}").status_code == 409
    assert len(con_rows("SELECT id FROM locations")) == 3
    sql("UPDATE gifts SET location_id = NULL WHERE id = ?", g)
    assert delete(client, f"/api/locations/{garage}").status_code == 200
    assert con_rows("SELECT COUNT(*) FROM locations") == [(0,)]
    assert delete(client, "/api/locations/999").status_code == 404


def test_deleting_a_location_or_an_address_needs_the_unlock(client, ids):
    _, _, bin_ = make_chain(client)
    gp = make_address(client, "Grandparents' house")
    for url in (f"/api/locations/{bin_}", "/api/locations/999", f"/api/addresses/{gp}", "/api/addresses/999"):
        r = delete(client, url)
        assert (r.status_code, r.get_json()) == (423, LOCKED), url
    assert len(con_rows("SELECT id FROM locations")) == 3 and len(con_rows("SELECT id FROM addresses")) == 2


def test_the_location_tree_names_no_gift(client, ids):
    garage, _, bin_ = make_chain(client)
    sql("UPDATE gifts SET location_id = ? WHERE id = ?", bin_, mk_protected_gift(ids, title="ZZ-SECRET-GIFT"))
    text = client.get("/api/locations", headers=auth()).get_data(as_text=True)
    assert "ZZ-SECRET" not in text and "gift" not in text.lower() and garage


def test_location_routes_are_404_in_school_mode_and_addresses_are_not(client, school):
    missing = client.get("/api/nothing-here", headers=auth())
    for method, url in (("GET", "/api/locations"), ("POST", "/api/locations"), ("PATCH", "/api/locations/1"), ("DELETE", "/api/locations/1")):
        r = client.open(url, method=method, json={}, headers=mut())
        assert (r.status_code, r.get_json()) == (404, missing.get_json()), (method, url)
    assert client.get("/api/addresses", headers=auth()).status_code == 200
    assert post(client, "/api/addresses", {"label": "Office"}).status_code == 201


# ---------------------------------------------------------------- order fields and the ship-to check
def set_status(client, gift_id, status, **order):
    return post(client, f"/api/gifts/{gift_id}/status", {"status": status, **order})


def camera(client, ids, **fields):
    return mk_gift(client, ids.o, "Camera", to=[ids.jordan], frm=[ids.alex], status="decided", **fields)


ORDER = {"seller": "Camera shop", "ship_to_id": 1, "eta": "2026-12-09"}


def test_ordered_needs_seller_ship_to_and_eta(client, ids):
    g = camera(client, ids)
    for body in ({}, {"seller": "Camera shop"}, {"seller": "Camera shop", "ship_to_id": 1}, {"eta": "2026-12-09", "ship_to_id": 1},
                 {"seller": "  ", "ship_to_id": 1, "eta": "2026-12-09"}, {"seller": "Camera shop", "ship_to_id": None, "eta": "2026-12-09"}):
        r = set_status(client, g, "ordered", **body)
        assert r.status_code == 400, body
    r = set_status(client, g, "ordered")
    assert r.get_json()["missing"] == ["seller", "ship_to_id", "eta"]
    assert r.get_json()["error"] == "Marking a gift ordered needs seller, ship_to_id, eta."
    assert set_status(client, g, "ordered", seller="Camera shop").get_json()["missing"] == ["ship_to_id", "eta"]
    assert con_rows("SELECT status, seller, eta, ordered_by, order_number FROM gifts WHERE id = ?", g) == [("decided", None, None, None, None)]
    r = set_status(client, g, "ordered", order_number="1234", **ORDER)
    j = r.get_json()
    assert (r.status_code, j["status"], j["seller"], j["order_number"], j["eta"], j["ship_to_id"], j["ship_warning"], j["ordered_by"]) == (
        200, "ordered", "Camera shop", "1234", "2026-12-09", 1, False, "Alex")


def test_fields_stored_earlier_count_and_patch_is_guarded_too(client, ids):
    g = camera(client, ids)
    assert patch(client, f"/api/gifts/{g}", {"status": "ordered"}).status_code == 400            # PATCH is no way around the rule
    assert patch(client, f"/api/gifts/{g}", {"title": "Camera 2", "status": "ordered", "seller": "Shop"}).status_code == 400
    assert con_rows("SELECT title, status, seller FROM gifts WHERE id = ?", g) == [("Camera", "decided", None)]   # all or nothing
    assert patch(client, f"/api/gifts/{g}", ORDER).status_code == 200
    assert con_rows("SELECT status FROM gifts WHERE id = ?", g) == [("decided",)]
    assert set_status(client, g, "ordered").status_code == 200                                    # stored fields are enough
    h = camera(client, ids)
    r = patch(client, f"/api/gifts/{h}", {"status": "ordered", "title": "Camera 3", **ORDER})
    assert (r.status_code, r.get_json()["status"], r.get_json()["title"]) == (200, "ordered", "Camera 3")
    assert patch(client, f"/api/gifts/{h}", {"status": 5}).status_code == 400


def test_ship_to_elsewhere_warns_until_marked_as_meant(client, ids):
    gp = make_address(client, "Grandparents' house")
    g = camera(client, ids)
    j = set_status(client, g, "ordered", seller="Shop", ship_to_id=gp, eta="2026-12-09").get_json()
    assert (j["ship_to_id"], j["ship_elsewhere_ok"], j["ship_warning"]) == (gp, False, True)
    assert patch(client, f"/api/gifts/{g}", {"ship_elsewhere_ok": True}).get_json()["ship_warning"] is False
    assert patch(client, f"/api/gifts/{g}", {"ship_elsewhere_ok": False}).get_json()["ship_warning"] is True
    assert patch(client, f"/api/gifts/{g}", {"ship_to_id": 1}).get_json()["ship_warning"] is False   # home
    assert patch(client, f"/api/gifts/{g}", {"ship_to_id": gp}).get_json()["ship_warning"] is True
    patch(client, f"/api/addresses/{gp}", {"is_home": True})                                          # the warning follows the home address
    j = get(client, f"/api/gifts/{g}")
    assert (j["ship_warning"], j["ship_to_id"]) == (False, gp)
    patch(client, "/api/addresses/1", {"is_home": True})
    assert get(client, f"/api/gifts/{g}")["ship_warning"] is True


def test_a_gift_with_no_ship_to_has_no_warning(client, ids):
    j = get(client, f"/api/gifts/{camera(client, ids)}")
    assert (j["ship_to_id"], j["ship_warning"], j["seller"], j["order_number"], j["eta"], j["arrived_on"], j["ordered_by"],
            j["ship_elsewhere_ok"], j["location_id"], j["location_path"]) == (None, False, None, None, None, None, None, False, None, None)


def test_order_field_validation(client, ids):
    g = camera(client, ids)
    for body in ({"eta": "12/9/2026"}, {"eta": "2026-02-30"}, {"eta": 5}, {"arrived_on": "tomorrow"}, {"ship_to_id": 999},
                 {"ship_to_id": "1"}, {"ship_to_id": True}, {"seller": "x" * 201}, {"order_number": "x" * 101},
                 {"ship_elsewhere_ok": "yes"}, {"seller": 5}):
        r = patch(client, f"/api/gifts/{g}", {"order_number": "KEEP", **body})
        assert r.status_code == 400, body
    assert con_rows("SELECT order_number FROM gifts WHERE id = ?", g) == [(None,)]                 # a 400 writes none of it
    assert patch(client, f"/api/gifts/{g}", {"seller": "Shop", "order_number": "  77  ", "eta": "2026-12-09"}).status_code == 200
    j = patch(client, f"/api/gifts/{g}", {"seller": "", "eta": None, "order_number": None}).get_json()
    assert (j["seller"], j["eta"], j["order_number"]) == (None, None, None)                       # empty and null clear


def test_arrived_stamps_today_unless_a_date_is_given(client, ids, freeze_today):
    freeze_today("2026-12-08")
    g, h = camera(client, ids), camera(client, ids)
    assert set_status(client, g, "arrived").get_json()["arrived_on"] == "2026-12-08"             # bought in a store
    freeze_today("2026-12-20")
    assert set_status(client, g, "wrapped").get_json()["arrived_on"] == "2026-12-08"             # not overwritten
    assert set_status(client, g, "arrived").get_json()["arrived_on"] == "2026-12-08"
    assert set_status(client, h, "arrived", arrived_on="2026-12-07").get_json()["arrived_on"] == "2026-12-07"
    assert set_status(client, h, "wrapped", arrived_on="").get_json()["arrived_on"] is None
    assert set_status(client, h, "arrived").get_json()["arrived_on"] == "2026-12-20"


def test_orders_update_is_the_one_writer(client, ids, con):
    import orders
    g = camera(client, ids)
    orders.update(con, g, {"seller": "Shop", "ship_to_id": 1, "eta": "2026-12-09", "title": "ignored", "status": "given"})
    assert tuple(con.execute("SELECT title, status, seller, ship_to_id, eta FROM gifts WHERE id = ?", (g,)).fetchone()) == (
        "Camera", "decided", "Shop", 1, "2026-12-09")
    from common import ApiError
    with pytest.raises(ApiError) as e:
        orders.update(con, 999, {})
    assert e.value.status == 404
    with pytest.raises(ApiError) as e:
        orders.update(con, g, {"eta": "soon"})
    assert e.value.status == 400


def test_a_gift_from_someone_else_cannot_be_ordered(client, ids):
    g = mk_gift(client, ids.o, "Book", to=[ids.jordan], frm=[ids.casey])
    r = set_status(client, g, "ordered", **ORDER)
    assert r.status_code == 400 and con_rows("SELECT status, seller FROM gifts WHERE id = ?", g) == [("told", None)]


def test_a_gift_can_be_placed_and_unplaced(client, ids):
    _, _, bin_ = make_chain(client)
    g = camera(client, ids)
    j = patch(client, f"/api/gifts/{g}", {"location_id": bin_}).get_json()
    assert (j["location_id"], j["location_path"]) == (bin_, "Garage › Top shelf › Blue bin")
    j = patch(client, f"/api/gifts/{g}", {"location_id": None}).get_json()
    assert (j["location_id"], j["location_path"]) == (None, None)
    for bad in (999, "1", True, 1.5):
        assert patch(client, f"/api/gifts/{g}", {"location_id": bad}).status_code == 400, bad
    assert get(client, f"/api/gifts/{g}")["location_id"] is None


def test_school_mode_has_no_location_keys_but_keeps_order_fields(client, ids, school):
    g = camera(client, ids)
    j = get(client, f"/api/gifts/{g}")
    assert "location_id" not in j and "location_path" not in j and "santa" not in j
    assert {"seller", "order_number", "ship_to_id", "ship_elsewhere_ok", "eta", "arrived_on", "ordered_by", "ship_warning"} <= j.keys()
    r = patch(client, f"/api/gifts/{g}", {"location_id": 1})
    assert (r.status_code, r.get_json()) == (400, {"error": "Hiding places are not used in school mode."})
    assert patch(client, f"/api/gifts/{g}", {"location_id": None}).status_code == 400
    assert post(client, "/api/gifts", {"occasion_id": ids.o, "title": "x", "recipient_ids": [ids.jordan], "location_id": None}).status_code == 400
    j = set_status(client, g, "ordered", **ORDER).get_json()
    assert (j["status"], j["seller"], j["ordered_by"]) == ("ordered", "Camera shop", "Alex") and "location_id" not in j
    assert con_rows("SELECT location_id FROM gifts WHERE id = ?", g) == [(None,)]


def test_a_locked_request_cannot_reach_a_protected_gifts_order_fields(client, ids):
    g = mk_protected_gift(ids, title="ZZ-SECRET-GIFT")
    for method, url, body in (("GET", f"/api/gifts/{g}", None), ("PATCH", f"/api/gifts/{g}", {"seller": "ZZ-SELLER"}),
                              ("POST", f"/api/gifts/{g}/status", {"status": "ordered", **ORDER}),
                              ("PATCH", f"/api/gifts/{g}", {"status": "ordered", **ORDER})):
        r = client.open(url, method=method, json=body, headers=mut())
        assert (r.status_code, r.get_json()) == (404, {"error": "No such gift."}), (method, url)
    assert con_rows("SELECT status, seller, ordered_by FROM gifts WHERE id = ?", g) == [("idea", None, None)]


# ---------------------------------------------------------------- the lists
def stage(gift_id, status, **cols):
    """Put a gift in a state directly (the lists read what is stored)."""
    cols = {"status": status, **cols}
    sql(f"UPDATE gifts SET {', '.join(f'{k} = ?' for k in cols)} WHERE id = ?", *cols.values(), gift_id)


def lst(client, ids, name):
    return get(client, f"/api/occasions/{ids.o}/lists/{name}")


def test_pending_is_ordered_gifts_by_eta_with_overdue(client, ids, freeze_today):
    freeze_today("2026-12-10")
    later, yesterday, today, nodate, decided, arrived = (mk_gift(client, ids.o, n, to=[ids.jordan], frm=[ids.alex]) for n in
                                                          ("Later", "Yesterday", "Today", "No date", "Decided", "Arrived"))
    stage(later, "ordered", eta="2026-12-12", seller="A")
    stage(yesterday, "ordered", eta="2026-12-09")
    stage(today, "ordered", eta="2026-12-10")
    stage(nodate, "ordered")                       # created as ordered through the API: allowed, and it has no date
    stage(arrived, "arrived", eta="2026-12-01")    # not pending, and an arrived gift is never overdue
    got = lst(client, ids, "pending")["gifts"]
    assert [(g["title"], g["eta"], g["overdue"]) for g in got] == [
        ("Yesterday", "2026-12-09", True), ("Today", "2026-12-10", False), ("Later", "2026-12-12", False), ("No date", None, False)]
    assert got[2]["seller"] == "A" and got[2]["recipient_ids"] == [ids.jordan] and decided


@pytest.mark.parametrize("day, overdue", [("2026-12-09", False), ("2026-12-10", False), ("2026-12-11", True)])
def test_overdue_flips_the_day_after_the_expected_date(client, ids, freeze_today, day, overdue):
    freeze_today(day)
    stage(mk_gift(client, ids.o, "Camera", to=[ids.jordan], frm=[ids.alex]), "ordered", eta="2026-12-10")
    assert [g["overdue"] for g in lst(client, ids, "pending")["gifts"]] == [overdue]


def test_pending_carries_the_ship_warning(client, ids):
    gp = make_address(client, "Grandparents' house")
    a, b, c = (mk_gift(client, ids.o, n, to=[ids.jordan], frm=[ids.alex]) for n in ("A", "B", "C"))
    stage(a, "ordered", eta="2026-12-09", ship_to_id=gp)
    stage(b, "ordered", eta="2026-12-10", ship_to_id=gp, ship_elsewhere_ok=1)
    stage(c, "ordered", eta="2026-12-11", ship_to_id=1)
    assert [(g["title"], g["ship_to_id"] == gp, g["ship_warning"]) for g in lst(client, ids, "pending")["gifts"]] == [
        ("A", True, True), ("B", True, False), ("C", False, False)]


def test_to_wrap_groups_arrived_gifts_by_recipient_with_the_place(client, ids):
    _, shelf, bin_ = make_chain(client)
    both = mk_gift(client, ids.o, "Board game", to=[ids.riley, ids.jordan], frm=[ids.alex])
    solo = mk_gift(client, ids.o, "Scarf", to=[ids.casey], frm=[ids.sam])
    wrapped, ordered = (mk_gift(client, ids.o, n, to=[ids.jordan], frm=[ids.alex]) for n in ("Wrapped", "Ordered"))
    stage(both, "arrived", location_id=bin_)
    stage(solo, "arrived")
    stage(wrapped, "wrapped")
    stage(ordered, "ordered", eta="2026-12-09")
    groups = lst(client, ids, "to-wrap")["groups"]
    assert [(g["person_id"], [(x["title"], x["location_path"]) for x in g["gifts"]]) for g in groups] == [
        (ids.jordan, [("Board game", "Garage › Top shelf › Blue bin")]),     # people come in the order they are listed
        (ids.riley, [("Board game", "Garage › Top shelf › Blue bin")]),
        (ids.casey, [("Scarf", None)])]
    assert shelf


def test_where_groups_by_place_in_tree_order_and_leaves_unplaced_last(client, ids):
    garage, _, bin_ = make_chain(client)
    closet = make_location(client, "Hall closet")
    a, b, c, d, e, f = (mk_gift(client, ids.o, n, to=[ids.jordan], frm=[ids.alex]) for n in "ABCDEF")
    stage(a, "arrived", location_id=closet)
    stage(b, "wrapped", location_id=bin_)
    stage(c, "arrived", location_id=bin_)
    stage(d, "wrapped")
    stage(e, "ordered", eta="2026-12-09", location_id=garage)     # not in the house yet
    stage(f, "given", location_id=garage)                         # no longer in the house
    groups = lst(client, ids, "where")["groups"]
    assert [(g["path"], [x["title"] for x in g["gifts"]]) for g in groups] == [
        ("Garage › Top shelf › Blue bin", ["B", "C"]), ("Hall closet", ["A"]), (None, ["D"])]
    assert [g["location_id"] for g in groups] == [bin_, closet, None]


def test_counts_are_gifts_not_rows(client, ids):
    both = mk_gift(client, ids.o, "Board game", to=[ids.riley, ids.jordan], frm=[ids.alex])
    a, b, c = (mk_gift(client, ids.o, n, to=[ids.jordan], frm=[ids.alex]) for n in "ABC")
    stage(both, "arrived")
    stage(a, "ordered", eta="2026-12-09")
    stage(b, "ordered", eta="2026-12-09")
    stage(c, "wrapped")
    assert lst(client, ids, "counts") == {"pending": 2, "to_wrap": 1}
    assert len(lst(client, ids, "to-wrap")["groups"]) == 2


def test_empty_lists_and_a_missing_occasion(client, ids):
    assert lst(client, ids, "pending") == {"gifts": []} and lst(client, ids, "to-wrap") == {"groups": []}
    assert lst(client, ids, "where") == {"groups": []} and lst(client, ids, "counts") == {"pending": 0, "to_wrap": 0}
    for name in ("pending", "to-wrap", "where", "counts"):
        assert client.get(f"/api/occasions/999/lists/{name}", headers=auth()).status_code == 404


@pytest.fixture
def hidden(client, ids):
    """Protected gifts that are ordered, arrived and placed, with markers in every field a list can show, a gift shared
    with an unprotected person, and one visible control of each kind."""
    garage, _, bin_ = make_chain(client)
    secret = make_location(client, "ZZ-PLACE")
    mine = mk_protected_gift(ids, title="ZZ-SECRET-ORDERED", to=[ids.kid1], santa=True)
    arrived = mk_protected_gift(ids, title="ZZ-SECRET-ARRIVED", to=[ids.kid2], santa=True)
    shared = mk_protected_gift(ids, title="ZZ-SECRET-SHARED", to=[ids.kid3, ids.jordan], santa=True)
    for g, status in ((mine, "ordered"), (arrived, "arrived"), (shared, "arrived")):
        stage(g, status, seller="ZZ-SELLER", order_number="ZZ-ORDER", eta="2026-12-09", location_id=secret)
    ctl_ordered = mk_gift(client, ids.o, "CONTROL-ORDERED", to=[ids.jordan], frm=[ids.alex])
    ctl_arrived = mk_gift(client, ids.o, "CONTROL-ARRIVED", to=[ids.jordan], frm=[ids.alex])
    stage(ctl_ordered, "ordered", eta="2026-12-10", seller="Camera shop")
    stage(ctl_arrived, "arrived", location_id=bin_)
    return garage


URLS = ("lists/pending", "lists/to-wrap", "lists/where", "lists/counts")


def test_the_lists_hide_protected_gifts_while_locked(client, ids, hidden):
    for name in URLS:
        text = client.get(f"/api/occasions/{ids.o}/{name}", headers=auth()).get_data(as_text=True)
        low = text.lower()
        assert not any(m in low for m in ("zz-", "santa")), name
    assert [g["title"] for g in lst(client, ids, "pending")["gifts"]] == ["CONTROL-ORDERED"]
    assert [(g["person_id"], [x["title"] for x in g["gifts"]]) for g in lst(client, ids, "to-wrap")["groups"]] == [(ids.jordan, ["CONTROL-ARRIVED"])]
    assert [[x["title"] for x in g["gifts"]] for g in lst(client, ids, "where")["groups"]] == [["CONTROL-ARRIVED"]]
    assert lst(client, ids, "counts") == {"pending": 1, "to_wrap": 1}
    # the places and addresses carry no gift: the hidden place's name is just a place
    assert "gift" not in client.get("/api/locations", headers=auth()).get_data(as_text=True).lower()


def test_unlocked_the_same_lists_show_the_protected_gifts(client, ids, hidden, unlock):
    """The check above is not blind: unlocked, the same requests do list them, and still never name Santa."""
    unlock()
    assert sorted(g["title"] for g in lst(client, ids, "pending")["gifts"]) == ["CONTROL-ORDERED", "ZZ-SECRET-ORDERED"]
    assert lst(client, ids, "counts") == {"pending": 2, "to_wrap": 3}
    where = {g["path"]: [x["title"] for x in g["gifts"]] for g in lst(client, ids, "where")["groups"]}
    assert where["ZZ-PLACE"] == ["ZZ-SECRET-ARRIVED", "ZZ-SECRET-SHARED"]
    for name in URLS:
        text = client.get(f"/api/occasions/{ids.o}/{name}", headers=auth()).get_data(as_text=True)
        assert "santa" not in text.lower(), name


def test_locking_again_hides_them_again(client, ids, hidden, unlock):
    unlock()
    assert lst(client, ids, "counts")["pending"] == 2
    assert post(client, "/api/lock/lock").status_code == 200
    assert lst(client, ids, "counts") == {"pending": 1, "to_wrap": 1}


def test_where_is_404_in_school_mode_and_the_other_lists_have_no_location_keys(client, ids, school):
    stage(mk_gift(client, ids.o, "Camera", to=[ids.jordan], frm=[ids.alex]), "arrived")
    r = client.get(f"/api/occasions/{ids.o}/lists/where", headers=auth())
    assert (r.status_code, r.get_json()) == (404, client.get("/api/nothing-here", headers=auth()).get_json())
    item = lst(client, ids, "to-wrap")["groups"][0]["gifts"][0]
    assert "location_id" not in item and "location_path" not in item and item["title"] == "Camera"
    assert lst(client, ids, "counts") == {"pending": 0, "to_wrap": 1} and lst(client, ids, "pending") == {"gifts": []}


def test_the_privacy_walk_sees_no_order_field_or_place_of_a_protected_gift(client, world):
    """tests/test_privacy.py's own walk (every GET under /api/, every argument combination), over its fixture with the
    protected gifts ordered, arrived and placed. The shared fixture gift is "arrived" in the occasion, so it is in
    to-wrap and where for the unprotected co-recipient's view too."""
    place = make_location(client, "ZZ-PLACE")
    for g in world.secret:
        stage(g, "arrived", seller="ZZ-SECRET-SELLER", order_number="ZZ-SECRET-ORDER", eta="2026-12-09", location_id=place)
    seen = walk_gets(client, world)
    assert any(url.endswith("/lists/where") for url in seen) and any(url.endswith("/lists/to-wrap") for url in seen)
    for url, text in seen.items():
        low = text.lower()
        markers = (*MARKERS, "zz-secret", *(("zz-place",) if "/lists/" in url else ()))  # a place's name is no secret, which gift is there is
        assert not [m for m in markers if m in low], url
    # the control: a visible gift in the same place is listed, so the absence above means something
    stage(world.visible, "arrived", location_id=place)
    assert "VISIBLE-GIFT" in client.get(f"/api/occasions/{world.ids.o}/lists/where", headers=auth()).get_data(as_text=True)


def test_no_locked_write_through_the_new_routes_changes_a_protected_gift(client, world):
    """Every non-GET rule with a hostile body, now including addresses and locations; the protected state is untouched."""
    before = protected_state()
    place = make_location(client, "A place")
    sent = 0
    for rule in api_rules({"POST", "PUT", "PATCH", "DELETE"}):
        if not rule.rule.startswith(("/api/addresses", "/api/locations")):
            continue
        for method in sorted(rule.methods & {"POST", "PUT", "PATCH", "DELETE"}):
            for url, _ in urls(rule, world):
                for body in ({}, {"label": "x", "name": "x", "parent_id": place, "is_home": False}):
                    assert client.open(url, method=method, json=body, headers=mut()).status_code < 500
                    sent += 1
    assert sent >= 8 and protected_state() == before
