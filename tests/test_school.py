"""School mode (MODE=school): what it hides, the staff-by-gift matrix, and the routes that only exist in it.

settings.MODE is read at request time (common.is_school), so the `school` fixture just sets it; the database is the
same fresh one every test gets. Names are generic: Ms. Avery, Ms. Blake, Ms. Chen, Mr. Diaz, Ms. Ellis."""
import os
import subprocess
import sys
import types

import pytest

import app as tracker
import settings
from conftest import auth, con_rows, mk_gift, mk_occasion, mk_person, mk_protected_gift, mut, put_plan
from test_privacy import api_rules, urls

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FAMILY_ONLY = {"lock_set_pin", "lock_unlock", "lock_lock", "person_board", "gifts_assign",
               "locations_list", "locations_create", "locations_update", "locations_delete"}
SCHOOL_ONLY = {"school_matrix", "school_cell_set", "school_cell_remove", "school_role_add"}


@pytest.fixture
def school(monkeypatch):
    monkeypatch.setattr(settings, "MODE", "school")


@pytest.fixture
def staff(client, school):
    """Christmas 2025 with five staff (all in it, plan individual) and four gifts, each created with its first
    recipient (a gift needs one): Card $35 for Ms. Avery, Card $25 for Ms. Chen, Apprec. for Ms. Blake and Granola
    for Mr. Diaz. Ms. Ellis has no gift."""
    o = mk_occasion(client, "Christmas 2025", date="2025-12-19")
    out = {"o": o}
    for key, name, role in (("avery", "Ms. Avery", "homeroom"), ("blake", "Ms. Blake", "homeroom"),
                            ("chen", "Ms. Chen", "assistant"), ("diaz", "Mr. Diaz", "special subject"),
                            ("ellis", "Ms. Ellis", "administrator")):
        out[key] = mk_person(client, name, role=role)
        put_plan(client, o, out[key])
    out["card35"] = mk_gift(client, o, "Card $35", to=[out["avery"]])
    out["card25"] = mk_gift(client, o, "Card $25", to=[out["chen"]])
    out["apprec"] = mk_gift(client, o, "Apprec.", to=[out["blake"]])
    out["granola"] = mk_gift(client, o, "Granola", to=[out["diaz"]])
    return types.SimpleNamespace(**out)


def world(ids):
    """The object test_privacy.args() reads, over this test's `ids`."""
    return types.SimpleNamespace(ids=ids, secret=(), visible=1, group=1)


def rules_with(mode_only):
    return [r for r in tracker.app.url_map.iter_rules()
            if getattr(tracker.app.view_functions[r.endpoint], "mode_only", None) == mode_only]


def forbidden_in(node):
    """Every `santa` key and every `protected: true` anywhere in a parsed JSON response."""
    found = []
    if isinstance(node, dict):
        found += [k for k in node if k == "santa"] + [k for k, v in node.items() if k == "protected" and v is True]
        for v in node.values():
            found += forbidden_in(v)
    elif isinstance(node, list):
        for v in node:
            found += forbidden_in(v)
    return found


def sweep(client, rules, ids, missing):
    """Every method of every rule, every argument combination: each answers exactly the generic 404."""
    sent = 0
    for rule in rules:
        for url, _values in urls(rule, world(ids)):
            for method in sorted(rule.methods & {"GET", "POST", "PUT", "PATCH", "DELETE"}):
                r = client.open(url, method=method, json={}, headers=mut())
                sent += 1
                assert (r.status_code, r.get_json()) == (404, missing.get_json()), (method, url, r.get_json())
    return sent


# ---------------------------------------------------------------- the mode switch
def test_the_family_only_and_school_only_views_are_exactly_these():
    assert {r.endpoint for r in rules_with("family")} == FAMILY_ONLY
    assert {r.endpoint for r in rules_with("school")} == SCHOOL_ONLY


def test_every_family_only_route_is_404_in_school_mode(client, ids, school):
    missing = client.get("/api/nothing-here", headers=mut())
    assert missing.status_code == 404
    assert sweep(client, rules_with("family"), ids, missing) >= 5


def test_every_school_route_is_404_in_family_mode(client, ids):
    missing = client.get("/api/nothing-here", headers=mut())
    assert sweep(client, rules_with("school"), ids, missing) >= 4
    # and the data is untouched
    assert con_rows("SELECT COUNT(*) FROM gift_recipients") == [(0,)]


def test_family_routes_still_work_in_family_mode(client, ids, unlock):
    assert client.post("/api/lock/pin", json={"pin": "4821"}, headers=mut()).status_code == 200
    unlock()
    assert client.get(f"/api/occasions/{ids.o}/board/{ids.kid1}", headers=auth()).status_code == 200


def test_a_family_only_route_is_still_behind_identity_and_the_csrf_checks(client, ids, school):
    lan = client.get(f"/api/occasions/{ids.o}/board/{ids.kid1}", environ_base={"REMOTE_ADDR": "192.0.2.50"})
    assert lan.status_code == 403
    assert client.post("/api/lock/lock", json={}, headers=auth()).status_code == 400   # no X-Tracker: refused before the 404


def test_school_mode_never_returns_or_accepts_santa(client, ids, school):
    g = mk_gift(client, ids.o, "Card", to=[ids.jordan])
    assert "santa" not in client.get(f"/api/gifts/{g}", headers=auth()).get_json()
    for body in ({"santa": True}, {"santa": False}, {"santa": None}):
        r = client.patch(f"/api/gifts/{g}", json=body, headers=mut())
        assert (r.status_code, r.get_json()) == (400, {"error": "Santa is not used in school mode."})
        r = client.post("/api/gifts", json={"occasion_id": ids.o, "title": "x", "recipient_ids": [ids.jordan], **body}, headers=mut())
        assert (r.status_code, r.get_json()) == (400, {"error": "Santa is not used in school mode."})
    assert con_rows("SELECT santa FROM gifts") == [(0,)]


def test_a_family_database_opened_in_school_mode_shows_no_santa_and_no_protected_person(client, ids, monkeypatch, unlock):
    """Data a family instance made (a protected child and a Santa gift for her) must not surface if a database
    is ever opened in school mode: nothing is locked, so nothing is filtered, so the edges have to hold."""
    gid = mk_protected_gift(ids, title="Bike", santa=True)
    unlock()
    assert forbidden_in(client.get(f"/api/gifts/{gid}", headers=auth()).get_json()) == ["santa"]  # control: it is there
    assert [p["id"] for p in client.get("/api/people", headers=auth()).get_json() if p["protected"]] == [ids.kid1, ids.kid2, ids.kid3]
    monkeypatch.setattr(settings, "MODE", "school")
    sent = 0
    for rule in api_rules({"GET"}):
        for url, _ in urls(rule, world(ids)):
            r = client.get(url, headers=auth())
            sent += 1
            assert r.status_code < 500, url
            assert not forbidden_in(r.get_json()), url
    assert sent > 10
    assert forbidden_in({"a": [{"santa": 1}, {"protected": True}]}) == ["santa", "protected"]  # the check can fail
    shown = client.get(f"/api/gifts/{gid}", headers=auth())
    assert shown.status_code == 200 and "santa" not in shown.get_json()
    assert all(p["protected"] is False for p in client.get("/api/people", headers=auth()).get_json())


def test_protected_true_is_400_in_school_mode(client, ids, school):
    assert client.post("/api/people", json={"name": "Ms. Avery", "protected": True}, headers=mut()).status_code == 400
    assert client.patch(f"/api/people/{ids.jordan}", json={"protected": True}, headers=mut()).status_code == 400
    assert client.post("/api/people", json={"name": "Ms. Blake", "protected": False}, headers=mut()).status_code == 201
    assert client.post("/api/people", json={"name": "Ms. Chen", "protected": None}, headers=mut()).status_code == 201
    assert con_rows("SELECT COUNT(*) FROM people WHERE name LIKE 'Ms.%' AND protected = 1") == [(0,)]


def test_people_can_be_deleted_in_school_mode_without_an_unlock(client, ids, school):
    spare = mk_person(client, "Mr. Diaz")
    assert client.delete(f"/api/people/{spare}", headers=mut()).status_code == 200


def test_me_names_the_mode_only_in_school_mode(client, school):
    me = client.get("/api/me", headers=auth()).get_json()
    assert me["mode"] == "school" and me["unlocked"] is True and me["has_pin"] is False


def test_family_me_has_no_mode_key(client):
    assert "mode" not in client.get("/api/me", headers=auth()).get_json()


def test_school_mode_sets_and_clears_no_unlock_cookie(client, school):
    r = client.get("/api/me", headers=auth())
    assert not any("gt_unlock" in h for h in r.headers.getlist("Set-Cookie"))
    client.set_cookie("gt_unlock", "junk", domain="localhost")
    r = client.get("/api/me", headers=auth())
    assert not any("gt_unlock" in h for h in r.headers.getlist("Set-Cookie"))


def _import_settings(mode):
    env = {**os.environ, "MODE": mode}
    return subprocess.run([sys.executable, "-c", "import settings; print(settings.MODE)"], cwd=ROOT_DIR, env=env,
                          capture_output=True, text=True)


@pytest.mark.parametrize("raw,mode", [("school", "school"), (" SCHOOL ", "school"), ("", "family"), ("Family", "family")])
def test_mode_is_read_trimmed_and_lowercased_with_family_as_the_default(raw, mode):
    r = _import_settings(raw)
    assert (r.returncode, r.stdout.strip()) == (0, mode), r.stderr


def test_an_unknown_mode_is_refused_at_import():
    r = _import_settings("staff")
    assert r.returncode != 0 and "MODE" in r.stderr


# ---------------------------------------------------------------- role
def test_role_is_stored_trimmed_and_cleared_with_empty_text(client, school):
    pid = mk_person(client, "Ms. Avery", role="  homeroom ")
    assert client.get("/api/people", headers=auth()).get_json()[-1]["role"] == "homeroom"
    r = client.patch(f"/api/people/{pid}", json={"role": ""}, headers=mut())
    assert r.status_code == 200 and r.get_json()["role"] is None
    assert client.patch(f"/api/people/{pid}", json={"name": "Ms. Avery"}, headers=mut()).get_json()["role"] is None
    assert client.patch(f"/api/people/{pid}", json={"role": "special subject"}, headers=mut()).get_json()["role"] == "special subject"
    assert client.patch(f"/api/people/{pid}", json={"name": "Ms. Avery-Hill"}, headers=mut()).get_json()["role"] == "special subject"


@pytest.mark.parametrize("role", ["x" * 51, 5, True, ["a"]])
def test_a_bad_role_is_400_and_stores_nothing(client, school, role):
    assert client.post("/api/people", json={"name": "Ms. Avery", "role": role}, headers=mut()).status_code == 400
    pid = mk_person(client, "Ms. Blake", role="homeroom")
    assert client.patch(f"/api/people/{pid}", json={"role": role}, headers=mut()).status_code == 400
    assert con_rows("SELECT name, role FROM people WHERE name LIKE 'Ms.%'") == [("Ms. Blake", "homeroom")]


def test_role_is_ignored_and_never_returned_in_family_mode(client):
    r = client.post("/api/people", json={"name": "Ms. Avery", "role": "homeroom"}, headers=mut())
    assert r.status_code == 201 and "role" not in r.get_json()
    assert con_rows("SELECT role FROM people WHERE name = 'Ms. Avery'") == [(None,)]
    assert all("role" not in p for p in client.get("/api/people", headers=auth()).get_json())
    assert client.patch(f"/api/people/{r.get_json()['id']}", json={"role": "x" * 99}, headers=mut()).status_code == 200  # ignored, not validated


# ---------------------------------------------------------------- editing a group gift keeps each recipient's detail
def test_editing_recipients_keeps_detail_and_done_of_those_who_stay(client, school):
    import db
    o = mk_occasion(client, "Christmas 2025", date="2025-12-19")
    avery, blake, chen = (mk_person(client, n) for n in ("Ms. Avery", "Ms. Blake", "Ms. Chen"))
    g = mk_gift(client, o, "Card", to=[avery, blake])
    c = db.connect(settings.DB_PATH)
    c.execute("UPDATE gift_recipients SET detail = '$35 · coffee shop', done = 1 WHERE gift_id = ? AND person_id = ?", (g, avery))
    c.execute("UPDATE gift_recipients SET detail = 'by hand' WHERE gift_id = ? AND person_id = ?", (g, blake))
    c.close()
    r = client.patch(f"/api/gifts/{g}", json={"recipient_ids": [avery, chen]}, headers=mut())
    assert r.status_code == 200 and r.get_json()["recipient_ids"] == [avery, chen]
    assert con_rows("SELECT person_id, detail, done FROM gift_recipients WHERE gift_id = ? ORDER BY person_id", g) == \
        [(avery, "$35 · coffee shop", 1), (chen, None, 0)]
    # the same list again changes nothing, and a title-only edit leaves every row alone
    client.patch(f"/api/gifts/{g}", json={"recipient_ids": [chen, avery], "title": "Cards"}, headers=mut())
    client.patch(f"/api/gifts/{g}", json={"title": "Gift cards"}, headers=mut())
    assert con_rows("SELECT person_id, detail, done FROM gift_recipients WHERE gift_id = ? ORDER BY person_id", g) == \
        [(avery, "$35 · coffee shop", 1), (chen, None, 0)]


# ---------------------------------------------------------------- the matrix
def matrix(client, o):
    r = client.get(f"/api/occasions/{o}/matrix", headers=auth())
    assert r.status_code == 200, r.get_json()
    return r.get_json()


def cell(client, gid, pid, body=None, method="PUT", **kw):
    return client.open(f"/api/gifts/{gid}/recipients/{pid}", method=method, json={} if body is None else body, headers=mut(), **kw)


def test_matrix_shape_counts_and_order(client, staff):
    s = staff
    assert cell(client, s.card35, s.avery, {"detail": "$35 · coffee shop", "done": True}).status_code == 200
    assert cell(client, s.card35, s.blake).status_code == 201
    assert cell(client, s.apprec, s.avery, {"detail": "card"}).status_code == 201
    m = matrix(client, s.o)
    assert m["occasion"] == {"id": s.o, "name": "Christmas 2025", "kind": "christmas", "date": "2025-12-19", "archived": False}
    assert [g["title"] for g in m["gifts"]] == ["Card $35", "Card $25", "Apprec.", "Granola"]
    assert m["gifts"][0] == {"id": s.card35, "title": "Card $35", "status": "idea", "recipient_count": 2, "done_count": 1}
    assert [(g["recipient_count"], g["done_count"]) for g in m["gifts"]] == [(2, 1), (1, 0), (2, 0), (1, 0)]
    assert [x["name"] for x in m["staff"]] == ["Ms. Avery", "Ms. Blake", "Ms. Chen", "Mr. Diaz", "Ms. Ellis"]
    assert m["staff"][0] == {"id": s.avery, "name": "Ms. Avery", "role": "homeroom", "color": None, "active": True, "plan": "individual"}
    assert m["cells"] == [
        {"gift_id": s.card35, "person_id": s.avery, "detail": "$35 · coffee shop", "done": True},
        {"gift_id": s.card35, "person_id": s.blake, "detail": None, "done": False},
        {"gift_id": s.card25, "person_id": s.chen, "detail": None, "done": False},
        {"gift_id": s.apprec, "person_id": s.avery, "detail": "card", "done": False},
        {"gift_id": s.apprec, "person_id": s.blake, "detail": None, "done": False},
        {"gift_id": s.granola, "person_id": s.diaz, "detail": None, "done": False},
    ]
    assert list(m) == ["occasion", "gifts", "staff", "cells"]


def test_rows_are_recipients_plus_everyone_active_who_is_not_skipped_or_an_owner(client, staff):
    s = staff
    put_plan(client, s.o, s.ellis, plan="skip")                      # skipped, no gift: no row
    newcomer = mk_person(client, "Ms. Fox", role="homeroom")         # added in Settings, in no occasion yet: a row, no plan
    left = mk_person(client, "Ms. Hall", role="homeroom", active=False)  # inactive and in no occasion: no row
    put_plan(client, s.o, s.diaz, plan="skip")                       # skipped but has a gift: still a row
    receiver = mk_person(client, "Mr. Grant", role="assistant", active=False)
    assert cell(client, s.card25, receiver).status_code == 201       # inactive but a recipient: a row
    m = matrix(client, s.o)
    names = [x["name"] for x in m["staff"]]
    assert set(names) == {"Ms. Avery", "Ms. Blake", "Ms. Chen", "Mr. Diaz", "Ms. Fox", "Mr. Grant"}
    assert "Alex" not in names and "Sam" not in names and left not in [x["id"] for x in m["staff"]]
    by_id = {x["id"]: x for x in m["staff"]}
    assert by_id[newcomer]["plan"] is None and by_id[s.diaz]["plan"] == "skip" and by_id[receiver]["active"] is False


def test_an_inactive_person_in_the_occasion_is_still_a_row_and_marked_inactive(client, staff):
    s = staff
    client.patch(f"/api/people/{s.ellis}", json={"active": False}, headers=mut())
    row = next(x for x in matrix(client, s.o)["staff"] if x["id"] == s.ellis)
    assert (row["active"], row["plan"]) == (False, "individual")
    stranger = mk_person(client, "Ms. Ivy", active=False)
    assert stranger not in [x["id"] for x in matrix(client, s.o)["staff"]]


def test_a_new_occasion_starts_from_the_previous_staff_list(client, staff):
    s = staff
    new = mk_occasion(client, "End of year 2026", kind="other", date="2026-06-05", copy_from=s.o)
    m = matrix(client, new)
    assert [x["name"] for x in m["staff"]] == ["Ms. Avery", "Ms. Blake", "Ms. Chen", "Mr. Diaz", "Ms. Ellis"]
    assert m["gifts"] == [] and m["cells"] == []
    assert [x["role"] for x in m["staff"]] == ["homeroom", "homeroom", "assistant", "special subject", "administrator"]


def test_columns_and_cells_belong_to_their_own_occasion(client, staff):
    s = staff
    other = mk_occasion(client, "Teacher Appreciation", kind="other", date="2026-05-05")
    g = mk_gift(client, other, "Flowers", to=[s.avery])
    m = matrix(client, s.o)
    assert g not in [x["id"] for x in m["gifts"]] and all(c["gift_id"] != g for c in m["cells"])
    assert [x["id"] for x in matrix(client, other)["gifts"]] == [g]


def test_matrix_for_a_missing_occasion_is_404(client, staff):
    r = client.get("/api/occasions/99999/matrix", headers=auth())
    assert (r.status_code, r.get_json()) == (404, {"error": "No such occasion."})


# ---------------------------------------------------------------- one cell
def test_put_adds_then_updates_and_changes_only_the_keys_sent(client, staff):
    s = staff
    r = cell(client, s.card35, s.blake)
    assert (r.status_code, r.get_json()) == (201, {"gift_id": s.card35, "person_id": s.blake, "detail": None, "done": False,
                                                    "recipient_count": 2, "done_count": 0})
    r = cell(client, s.card35, s.blake, {"done": True})
    assert (r.status_code, r.get_json()["done"], r.get_json()["done_count"]) == (200, True, 1)
    r = cell(client, s.card35, s.blake, {"detail": "  $35 · bookstore "})
    assert (r.get_json()["detail"], r.get_json()["done"]) == ("$35 · bookstore", True)
    r = cell(client, s.card35, s.blake, {"done": False})
    assert (r.get_json()["detail"], r.get_json()["done"], r.get_json()["done_count"]) == ("$35 · bookstore", False, 0)
    r = cell(client, s.card35, s.blake, {"detail": None})
    assert r.get_json()["detail"] is None
    assert cell(client, s.card35, s.blake, {"detail": ""}).get_json()["detail"] is None
    assert cell(client, s.card35, s.blake, {"done": None}).get_json()["done"] is False     # null means "not sent"
    assert con_rows("SELECT COUNT(*) FROM gift_recipients WHERE gift_id = ?", s.card35) == [(2,)]


def test_put_with_a_detail_on_a_new_cell_creates_it_with_that_detail(client, staff):
    s = staff
    r = cell(client, s.card25, s.ellis, {"detail": "$25 · tea", "done": True})
    assert (r.status_code, r.get_json()["detail"], r.get_json()["done"], r.get_json()["recipient_count"]) == (201, "$25 · tea", True, 2)


def test_a_cell_change_moves_the_gifts_updated_at(client, staff, clock):
    s = staff
    clock.advance(60)
    cell(client, s.card35, s.blake)
    assert con_rows("SELECT updated_at FROM gifts WHERE id = ?", s.card35) == [(clock.t,)]
    clock.advance(60)
    cell(client, s.card35, s.blake, method="DELETE")
    assert con_rows("SELECT updated_at FROM gifts WHERE id = ?", s.card35) == [(clock.t,)]


@pytest.mark.parametrize("body", [{"done": "yes"}, {"done": 1}, {"done": 0}, {"detail": 5}, {"detail": ["x"]}, {"detail": "x" * 201}])
def test_put_rejects_bad_values_and_stores_nothing(client, staff, body):
    s = staff
    assert cell(client, s.card35, s.avery, body).status_code == 400
    assert cell(client, s.card35, s.blake, body).status_code == 400
    assert con_rows("SELECT person_id, detail, done FROM gift_recipients WHERE gift_id = ?", s.card35) == [(s.avery, None, 0)]


def test_put_needs_a_json_object(client, staff):
    s = staff
    r = client.put(f"/api/gifts/{s.card35}/recipients/{s.blake}", data="[1]", headers=mut())
    assert r.status_code == 400


def test_put_a_missing_person_or_gift_is_404(client, staff):
    s = staff
    assert cell(client, s.card35, 99999).status_code == 404
    assert cell(client, 99999, s.avery).status_code == 404
    assert cell(client, 99999, 99999).status_code == 404


def test_delete_removes_a_cell_and_refuses_the_last_one(client, staff):
    s = staff
    cell(client, s.card35, s.blake, {"done": True})
    r = cell(client, s.card35, s.blake, method="DELETE")
    assert (r.status_code, r.get_json()) == (200, {"gift_id": s.card35, "person_id": s.blake, "recipient_count": 1, "done_count": 0})
    r = cell(client, s.card35, s.avery, method="DELETE")
    assert (r.status_code, r.get_json()) == (409, {"error": "A gift needs at least one recipient. Delete the gift instead."})
    assert con_rows("SELECT person_id FROM gift_recipients WHERE gift_id = ?", s.card35) == [(s.avery,)]
    r = cell(client, s.card35, s.blake, method="DELETE")                     # no longer a recipient
    assert (r.status_code, r.get_json()) == (404, {"error": "That person is not a recipient of this gift."})
    assert cell(client, 99999, s.avery, method="DELETE").status_code == 404


def test_removing_and_adding_back_starts_the_cell_afresh(client, staff):
    s = staff
    cell(client, s.card35, s.blake, {"detail": "x", "done": True})
    cell(client, s.card35, s.blake, method="DELETE")
    r = cell(client, s.card35, s.blake)
    assert (r.status_code, r.get_json()["detail"], r.get_json()["done"]) == (201, None, False)


def test_deleting_a_gift_removes_its_column_and_cells(client, staff):
    s = staff
    cell(client, s.card35, s.blake, {"done": True})
    assert client.delete(f"/api/gifts/{s.card35}", headers=mut()).status_code == 200
    m = matrix(client, s.o)
    assert s.card35 not in [g["id"] for g in m["gifts"]] and all(c["gift_id"] != s.card35 for c in m["cells"])
    assert con_rows("SELECT COUNT(*) FROM gift_recipients WHERE gift_id = ?", s.card35) == [(0,)]


# ---------------------------------------------------------------- add everyone with a role
def test_add_everyone_with_a_role(client, staff):
    s = staff
    cell(client, s.card35, s.blake, {"detail": "kept", "done": True})
    r = client.post(f"/api/gifts/{s.card35}/recipients", json={"role": " Homeroom "}, headers=mut())
    assert (r.status_code, r.get_json()) == (200, {"added": [], "recipient_count": 2, "done_count": 1})   # both already there
    r = client.post(f"/api/gifts/{s.card25}/recipients", json={"role": "HOMEROOM"}, headers=mut())
    assert (r.status_code, r.get_json()) == (200, {"added": [s.avery, s.blake], "recipient_count": 3, "done_count": 0})
    r = client.post(f"/api/gifts/{s.card25}/recipients", json={"role": "homeroom"}, headers=mut())
    assert r.get_json()["added"] == []
    assert con_rows("SELECT detail, done FROM gift_recipients WHERE gift_id = ? AND person_id = ?", s.card35, s.blake) == [("kept", 1)]
    assert con_rows("SELECT detail, done FROM gift_recipients WHERE gift_id = ? AND person_id = ?", s.card25, s.avery) == [(None, 0)]


def test_add_role_skips_inactive_people_and_unknown_roles_add_nobody(client, staff):
    s = staff
    extra = mk_person(client, "Ms. Hall", role="homeroom")
    client.patch(f"/api/people/{extra}", json={"active": False}, headers=mut())
    r = client.post(f"/api/gifts/{s.granola}/recipients", json={"role": "homeroom"}, headers=mut())
    assert r.get_json()["added"] == [s.avery, s.blake]
    r = client.post(f"/api/gifts/{s.granola}/recipients", json={"role": "bus driver"}, headers=mut())
    assert (r.status_code, r.get_json()["added"]) == (200, [])


@pytest.mark.parametrize("body", [{}, {"role": ""}, {"role": "   "}, {"role": 3}, {"role": None}, {"role": ["homeroom"]}, {"role": "x" * 51}])
def test_add_role_needs_a_role(client, staff, body):
    s = staff
    assert client.post(f"/api/gifts/{s.granola}/recipients", json=body, headers=mut()).status_code == 400
    assert con_rows("SELECT COUNT(*) FROM gift_recipients WHERE gift_id = ?", s.granola) == [(1,)]


def test_add_role_to_a_missing_gift_is_404(client, staff):
    assert client.post("/api/gifts/99999/recipients", json={"role": "homeroom"}, headers=mut()).status_code == 404


# ---------------------------------------------------------------- editing a gift through the routes keeps details
def test_patching_recipients_after_setting_cells_keeps_them(client, staff):
    s = staff
    cell(client, s.card35, s.blake, {"detail": "$35", "done": True})
    r = client.patch(f"/api/gifts/{s.card35}", json={"recipient_ids": [s.avery, s.blake, s.chen]}, headers=mut())
    assert r.status_code == 200
    m = matrix(client, s.o)
    assert {"gift_id": s.card35, "person_id": s.blake, "detail": "$35", "done": True} in m["cells"]
    assert next(g for g in m["gifts"] if g["id"] == s.card35)["recipient_count"] == 3


# ---------------------------------------------------------------- the guards still apply
def test_school_writes_need_the_csrf_headers_and_identity(client, staff):
    s = staff
    path = f"/api/gifts/{s.card35}/recipients/{s.blake}"
    assert client.put(path, json={}, headers=auth()).status_code == 400
    assert client.delete(path, headers=auth()).status_code == 400
    assert client.post(f"/api/gifts/{s.card35}/recipients", json={"role": "homeroom"}, headers=auth()).status_code == 400
    lan = {"environ_base": {"REMOTE_ADDR": "192.0.2.50"}}
    assert client.get(f"/api/occasions/{s.o}/matrix", **lan).status_code == 403
    assert client.put(path, json={}, **lan).status_code == 403
    assert con_rows("SELECT COUNT(*) FROM gift_recipients WHERE gift_id = ?", s.card35) == [(1,)]


def test_the_wrong_origin_is_refused_on_a_cell_write(client, staff):
    s = staff
    r = client.put(f"/api/gifts/{s.card35}/recipients/{s.blake}", json={}, headers={**mut(), "Origin": "https://evil.example.com"})
    assert r.status_code == 403


def test_no_school_mode_request_is_a_server_error(client, ids, school):
    """Walk every route in school mode with hostile bodies: 4xx or 2xx, never a 500 (the matrix routes included)."""
    bodies = [{}, {"role": 5}, {"role": "homeroom"}, {"detail": ["x"]}, {"detail": "x" * 500}, {"done": "yes"}, {"done": None},
              {"santa": True}, {"protected": True}, {"title": "x", "recipient_ids": "nope"}, {"plan": "skip", "budget_cents": -1}]
    assert mk_gift(client, ids.o, "Card", to=[ids.jordan]) == 1      # test_privacy.args() points gift_id at 1
    sent = 0
    for rule in api_rules({"GET", "POST", "PUT", "PATCH", "DELETE"}):
        if rule.rule.startswith("/api/lock"):
            continue
        for method in sorted(rule.methods & {"GET", "POST", "PUT", "PATCH", "DELETE"}):
            for url, _values in urls(rule, world(ids)):
                for body in ([None] if method == "GET" else bodies):
                    r = client.open(url, method=method, json=body, headers=mut())
                    sent += 1
                    assert r.status_code < 500, (method, url, body, r.status_code, r.get_data(as_text=True)[:200])
    assert sent > 200
