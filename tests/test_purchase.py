"""After purchase: ship-to addresses, the location tree, order fields and the ship-to check, and the three lists.
The lock rules for all of it are pinned here and walked by tests/test_privacy.py."""
import pytest

import db
import settings
from conftest import auth, con_rows, mk_gift, mk_protected_gift, mut

LOCKED = {"error": "Locked", "locked": True}


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
