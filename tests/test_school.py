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
FAMILY_ONLY = {"lock_set_pin", "lock_unlock", "lock_lock", "person_board", "gifts_assign"}


@pytest.fixture
def school(monkeypatch):
    monkeypatch.setattr(settings, "MODE", "school")


@pytest.fixture
def staff(client, school):
    """One occasion and five staff, all in it with a plan; plus four gifts (columns) with no recipients yet:
    their ids are in `gift` (creating them needs a recipient, so each starts with `avery` and the test removes her
    only if it needs an empty column)."""
    o = mk_occasion(client, "Christmas 2025", date="2025-12-19")
    out = {"o": o}
    for key, name, role in (("avery", "Ms. Avery", "homeroom"), ("blake", "Ms. Blake", "homeroom"),
                            ("chen", "Ms. Chen", "assistant"), ("diaz", "Mr. Diaz", "special subject"),
                            ("ellis", "Ms. Ellis", "administrator")):
        out[key] = mk_person(client, name, role=role)
        put_plan(client, o, out[key])
    out["gift"] = {}
    for key, title in (("card35", "Card $35"), ("card25", "Card $25"), ("apprec", "Apprec."), ("granola", "Granola")):
        out["gift"][key] = mk_gift(client, o, title, to=[out["ellis"]])
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
def test_the_family_only_views_are_exactly_these():
    assert {r.endpoint for r in rules_with("family")} == FAMILY_ONLY


def test_every_family_only_route_is_404_in_school_mode(client, ids, school):
    missing = client.get("/api/nothing-here", headers=mut())
    assert missing.status_code == 404
    assert sweep(client, rules_with("family"), ids, missing) >= 5


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
