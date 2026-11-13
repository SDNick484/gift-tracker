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
