import threading
import time

import pytest

import app as tracker
import boards
import db
import gifts
import settings
from common import ApiError
from conftest import PIN, auth, mk_gift, mut, set_pin


def board(client, ids, pid):
    r = client.get(f"/api/occasions/{ids.o}/board/{pid}", headers=auth())
    assert r.status_code == 200, r.get_json()
    return r.get_json()


def test_board_groups_by_giver_with_us_last(client, ids, unlock):
    unlock()
    a = mk_gift(client, ids.o, "Murdle", to=[ids.kid1])
    b = mk_gift(client, ids.o, "eReader", to=[ids.kid1, ids.kid2, ids.kid3], frm=[ids.jordan, ids.riley])
    c = mk_gift(client, ids.o, "Belt bag", to=[ids.kid1], frm=[ids.alex, ids.sam])
    brd = board(client, ids, ids.kid1)
    assert [g["id"] for g in brd["unassigned"]] == [a]
    assert [(x["label"], [g["id"] for g in x["gifts"]]) for x in brd["givers"]] == [("Jordan & Riley", [b]), ("Us", [c])]


def test_board_person_and_group_fields(client, ids, unlock):
    unlock()
    client.patch(f"/api/people/{ids.kid1}", json={"color": "#b91c1c"}, headers=mut())
    client.patch(f"/api/people/{ids.jordan}", json={"household": "Jordan & Casey"}, headers=mut())
    client.patch(f"/api/people/{ids.casey}", json={"household": "Jordan & Casey"}, headers=mut())
    b = mk_gift(client, ids.o, "Chess set", to=[ids.kid1], frm=[ids.jordan, ids.casey], url="https://example.com/c")
    c = mk_gift(client, ids.o, "Puzzle", to=[ids.kid1], frm=[ids.alex])
    brd = board(client, ids, ids.kid1)
    assert brd["person"] == {"id": ids.kid1, "name": "Kid 1", "color": "#b91c1c"}
    assert brd["givers"] == [
        {"label": "Jordan & Casey", "giver_ids": sorted([ids.jordan, ids.casey]), "is_us": False,
         "gifts": [brd["givers"][0]["gifts"][0]], "list_text": "Jordan & Casey\n\nChess set - https://example.com/c"},
        {"label": "Alex", "giver_ids": [ids.alex], "is_us": True,
         "gifts": [brd["givers"][1]["gifts"][0]], "list_text": "Alex\n\nPuzzle"},
    ]
    assert [brd["givers"][0]["gifts"][0]["id"], brd["givers"][1]["gifts"][0]["id"]] == [b, c]
    assert brd["givers"][1]["gifts"][0]["santa"] is False


def test_other_households_are_ordered_by_label(client, ids, unlock):
    unlock()
    mk_gift(client, ids.o, "One", to=[ids.kid1], frm=[ids.riley])
    mk_gift(client, ids.o, "Two", to=[ids.kid1], frm=[ids.casey])
    mk_gift(client, ids.o, "Three", to=[ids.kid1], frm=[ids.jordan])
    assert [x["label"] for x in board(client, ids, ids.kid1)["givers"]] == ["Casey", "Jordan", "Riley"]


def test_board_leaves_out_other_people_and_occasions(client, ids, unlock):
    unlock()
    other = client.post("/api/occasions", json={"name": "Birthday", "kind": "birthday", "date": "2027-03-01"},
                        headers=mut()).get_json()["id"]
    mk_gift(client, other, "Elsewhere", to=[ids.kid1])
    mk_gift(client, ids.o, "Not hers", to=[ids.kid2])
    brd = board(client, ids, ids.kid1)
    assert brd["unassigned"] == [] and brd["givers"] == []
    assert brd["split"] == {"santa": 0, "us": 0, "others": 0}


def test_split_counts_santa_us_and_others(client, ids, unlock):
    unlock()
    mk_gift(client, ids.o, "Bike", to=[ids.kid1], frm=[ids.alex, ids.sam], santa=True)
    mk_gift(client, ids.o, "Belt bag", to=[ids.kid1], frm=[ids.alex, ids.sam])
    mk_gift(client, ids.o, "eReader", to=[ids.kid1], frm=[ids.jordan])
    brd = board(client, ids, ids.kid1)
    assert brd["split"] == {"santa": 1, "us": 1, "others": 1}


def test_evenness_splits_shared_gifts(client, ids, unlock):
    unlock()
    mk_gift(client, ids.o, "Camera", to=[ids.kid1, ids.kid2, ids.kid3], frm=[ids.alex], price="90")
    mk_gift(client, ids.o, "Belt bag", to=[ids.kid1], frm=[ids.alex], price="38")
    brd = board(client, ids, ids.kid1)
    assert {e["person_id"]: e["cents"] for e in brd["evenness"]} == {ids.kid1: 6800, ids.kid2: 3000, ids.kid3: 3000}
    assert [e["name"] for e in brd["evenness"]] == ["Kid 1", "Kid 2", "Kid 3"]


def test_evenness_counts_only_ours_track_priced_gifts_in_this_occasion(client, ids, unlock):
    unlock()
    mk_gift(client, ids.o, "From Casey", to=[ids.kid1], frm=[ids.casey], price="50")       # theirs: not counted
    mk_gift(client, ids.o, "No price", to=[ids.kid1], frm=[ids.alex])
    mk_gift(client, ids.o, "Given", to=[ids.kid1], frm=[ids.alex], price="10", status="given")
    mk_gift(client, ids.o, "Unassigned idea", to=[ids.kid1], price="5")                   # no givers is ours
    mk_gift(client, ids.o, "With an adult", to=[ids.kid1, ids.jordan], frm=[ids.sam], price="0.03")  # 2c Jordan (lower id), 1c Kid 1
    other = client.post("/api/occasions", json={"name": "Birthday", "kind": "birthday", "date": "2027-03-01"},
                        headers=mut()).get_json()["id"]
    mk_gift(client, other, "Elsewhere", to=[ids.kid1], frm=[ids.alex], price="999")
    ev = {e["person_id"]: e["cents"] for e in board(client, ids, ids.kid1)["evenness"]}
    assert ev == {ids.kid1: 1501, ids.kid2: 0, ids.kid3: 0}


def test_assign_conflict_is_409_and_first_assignment_stands(client, ids, unlock):
    """Two owners on two phones, both looking at the gift as unassigned. Each has unlocked on their own device
    (the unlock cookie is per user, so the second owner gets a client of their own)."""
    unlock()
    other = tracker.app.test_client()
    set_pin(other, PIN, "sam@example.com")
    assert other.post("/api/lock/unlock", json={"pin": PIN}, headers=mut("sam@example.com")).status_code == 200
    g = mk_gift(client, ids.o, "Murdle", to=[ids.kid1])
    first = client.post(f"/api/gifts/{g}/assign", json={"giver_ids": [ids.jordan, ids.riley], "expected_giver_ids": []}, headers=mut())
    second = other.post(f"/api/gifts/{g}/assign", json={"giver_ids": [ids.casey], "expected_giver_ids": []},
                        headers=mut("sam@example.com"))
    assert (first.status_code, second.status_code) == (200, 409)
    assert second.get_json() == {"error": "Someone just assigned this. Reload."}
    assert client.get(f"/api/gifts/{g}", headers=auth()).get_json()["giver_ids"] == [ids.jordan, ids.riley]


def test_simultaneous_assignment_one_wins_the_other_is_409(client, ids, unlock, monkeypatch):
    """Review Focus 2. Two connections assign the same unassigned gift at the same moment. The write is slowed
    so both would read the old giver set if the read were outside the transaction; BEGIN IMMEDIATE makes the
    second wait, see the first's givers, and refuse."""
    unlock()
    g = mk_gift(client, ids.o, "Murdle", to=[ids.kid1])
    real = gifts._set_givers

    def slow(con, gift_id, giver_ids):
        time.sleep(0.3)
        real(con, gift_id, giver_ids)

    monkeypatch.setattr(gifts, "_set_givers", slow)
    start, results = threading.Barrier(2), {}

    def run(name, givers):
        con = db.connect(settings.DB_PATH)
        try:
            start.wait()
            boards.assign(con, g, givers, [])
            results[name] = "ok"
        except ApiError as e:
            results[name] = e.status
        finally:
            con.close()

    threads = [threading.Thread(target=run, args=("a", [ids.jordan])), threading.Thread(target=run, args=("b", [ids.casey]))]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(results.values(), key=str) == [409, "ok"]
    winner = [ids.jordan] if results["a"] == "ok" else [ids.casey]
    assert client.get(f"/api/gifts/{g}", headers=auth()).get_json()["giver_ids"] == winner


def test_expected_giver_ids_compare_as_a_set(client, ids, unlock):
    unlock()
    g = mk_gift(client, ids.o, "Murdle", to=[ids.kid1], frm=[ids.jordan, ids.riley])
    r = client.post(f"/api/gifts/{g}/assign", json={"giver_ids": [ids.casey], "expected_giver_ids": [ids.riley, ids.jordan]},
                    headers=mut())
    assert r.status_code == 200 and r.get_json()["giver_ids"] == [ids.casey]
    r = client.post(f"/api/gifts/{g}/assign", json={"giver_ids": [], "expected_giver_ids": [ids.jordan]}, headers=mut())
    assert r.status_code == 409


def test_assign_to_others_sets_told_and_unassign_returns_to_idea(client, ids, unlock, freeze_today):
    unlock()
    freeze_today("2026-11-20")
    g = mk_gift(client, ids.o, "Murdle", to=[ids.kid1])
    client.post(f"/api/gifts/{g}/assign", json={"giver_ids": [ids.casey], "expected_giver_ids": []}, headers=mut())
    got = client.get(f"/api/gifts/{g}", headers=auth()).get_json()
    assert (got["status"], got["told_at"]) == ("told", "2026-11-20")
    client.post(f"/api/gifts/{g}/assign", json={"giver_ids": [], "expected_giver_ids": [ids.casey]}, headers=mut())
    got = client.get(f"/api/gifts/{g}", headers=auth()).get_json()
    assert (got["status"], got["told_at"], got["giver_ids"]) == ("idea", None, [])


def test_unassigning_a_gift_from_us_drops_santa_and_returns_to_idea(client, ids, unlock):
    unlock()
    g = mk_gift(client, ids.o, "Bike", to=[ids.kid1], frm=[ids.alex, ids.sam], santa=True, status="wrapped")
    r = client.post(f"/api/gifts/{g}/assign", json={"giver_ids": [], "expected_giver_ids": [ids.alex, ids.sam]},
                    headers=mut())
    assert r.status_code == 200
    assert (r.get_json()["status"], r.get_json()["santa"]) == ("idea", False)


@pytest.mark.parametrize("body", [
    {"giver_ids": "x", "expected_giver_ids": []},
    {"giver_ids": [True], "expected_giver_ids": []},
    {"giver_ids": [999999], "expected_giver_ids": []},
    {"giver_ids": []},
    {"giver_ids": [], "expected_giver_ids": "x"},
    {"giver_ids": [], "expected_giver_ids": [1.5]},
    {"expected_giver_ids": []},
])
def test_assign_bad_body_is_400(client, ids, unlock, body):
    unlock()
    g = mk_gift(client, ids.o, "Murdle", to=[ids.kid1])
    assert client.post(f"/api/gifts/{g}/assign", json=body, headers=mut()).status_code == 400


def test_assign_missing_gift_is_404(client, ids, unlock):
    unlock()
    r = client.post("/api/gifts/999999/assign", json={"giver_ids": [], "expected_giver_ids": []}, headers=mut())
    assert (r.status_code, r.get_json()) == (404, {"error": "No such gift."})


def test_list_text():
    assert boards.list_text("Jordan & Riley", [{"title": "Murdle", "url": "https://example.com/m"}, {"title": "Chess set", "url": None}]) \
        == "Jordan & Riley\n\nMurdle - https://example.com/m\nChess set"


def test_board_locked_is_423_and_unprotected_person_is_404(client, ids, unlock):
    assert client.get(f"/api/occasions/{ids.o}/board/{ids.kid1}", headers=auth()).status_code == 423
    unlock()
    assert client.get(f"/api/occasions/{ids.o}/board/{ids.jordan}", headers=auth()).status_code == 404


def test_board_missing_person_or_occasion_is_404(client, ids, unlock):
    unlock()
    assert client.get(f"/api/occasions/{ids.o}/board/999999", headers=auth()).status_code == 404
    assert client.get(f"/api/occasions/999999/board/{ids.kid1}", headers=auth()).status_code == 404


def test_assign_while_locked_is_423_and_changes_nothing(client, ids, unlock):
    unlock()
    g = mk_gift(client, ids.o, "Murdle", to=[ids.kid1])
    client.post("/api/lock/lock", json={}, headers=mut())
    r = client.post(f"/api/gifts/{g}/assign", json={"giver_ids": [ids.casey], "expected_giver_ids": []}, headers=mut())
    assert (r.status_code, r.get_json()) == (423, {"error": "Locked", "locked": True})
    unlock()
    assert client.get(f"/api/gifts/{g}", headers=auth()).get_json()["giver_ids"] == []
