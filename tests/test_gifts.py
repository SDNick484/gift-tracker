import pytest

import common
import gifts
from conftest import auth, con_rows, mk_gift, mut

USER = {"email": "alex@example.com", "name": "Alex"}


@pytest.mark.parametrize("raw,cents", [("29", 2900), ("29.9", 2990), ("$29.99", 2999), ("1,299.00", 129900),
                                        (1500, 1500), ("", None), (None, None), ("  7 ", 700), ("0", 0),
                                        ("100,000", 10_000_000), (10_000_000, 10_000_000), ("0.07", 7)])
def test_parse_cents_accepts(raw, cents):
    assert common.parse_cents(raw) == cents


@pytest.mark.parametrize("raw", ["abc", "-5", "NaN", "inf", "1.999", "1e5", 10_000_001, 12.5, True, -1, "1,29", "$",
                                 ".5", "5.", "1 000", "$$5", "100,000.01", ["5"], {"a": 1}, float("nan"), "٣"])
def test_parse_cents_rejects(raw):
    with pytest.raises(common.ApiError) as e:
        common.parse_cents(raw)
    assert e.value.status == 400


def test_price_over_the_api_is_cents_or_null_never_float(client, ids):
    g = mk_gift(client, ids.o, "Book", to=[ids.jordan], price="$29.99")
    assert client.get(f"/api/gifts/{g}", headers=auth()).get_json()["price_cents"] == 2999
    for bad in ("abc", "-5", "NaN", 12.5):
        assert client.patch(f"/api/gifts/{g}", json={"price": bad}, headers=mut()).status_code == 400
    r = client.patch(f"/api/gifts/{g}", json={"price": ""}, headers=mut())
    assert r.status_code == 200 and r.get_json()["price_cents"] is None
    # a literal NaN in the JSON body is a float too
    r = client.patch(f"/api/gifts/{g}", data='{"price": NaN}', headers=mut())
    assert r.status_code == 400
    assert con_rows("SELECT typeof(price_cents) FROM gifts WHERE id=?", g) == [("null",)]


def test_joint_gift_is_one_row_with_two_recipients_and_two_givers(client, ids):
    g = mk_gift(client, ids.o, "Analogue 3D", to=[ids.jordan, ids.riley], frm=[ids.alex, ids.sam])
    body = client.get(f"/api/gifts/{g}", headers=auth()).get_json()
    assert body["recipient_ids"] == [ids.jordan, ids.riley] and body["giver_ids"] == [ids.alex, ids.sam]
    assert body["track"] == "ours" and body["status"] == "idea"
    assert con_rows("SELECT COUNT(*) FROM gifts") == [(1,)]


def test_gift_with_no_giver_is_ours_and_records_the_creator(client, ids):
    g = mk_gift(client, ids.o, "Idea", to=[ids.jordan])
    body = client.get(f"/api/gifts/{g}", headers=auth()).get_json()
    assert (body["track"], body["status"], body["giver_ids"], body["told_at"]) == ("ours", "idea", [], None)
    assert body["created_by"] == "alex@example.com"


def test_gift_from_someone_else_starts_told(client, ids, freeze_today, unlock):
    unlock(); freeze_today("2026-11-20")      # kid1 is protected: adding a gift for them needs the unlock
    g = mk_gift(client, ids.o, "eReader", to=[ids.kid1], frm=[ids.casey])
    body = client.get(f"/api/gifts/{g}", headers=auth()).get_json()
    assert (body["track"], body["status"], body["told_at"]) == ("theirs", "told", "2026-11-20")


def test_a_gift_from_an_owner_and_someone_else_is_ours(client, ids):
    g = mk_gift(client, ids.o, "Shared", to=[ids.jordan], frm=[ids.alex, ids.casey])
    assert client.get(f"/api/gifts/{g}", headers=auth()).get_json()["track"] == "ours"


def test_status_must_belong_to_the_track(client, ids):
    g = mk_gift(client, ids.o, "Book", to=[ids.jordan], frm=[ids.alex])
    assert client.post(f"/api/gifts/{g}/status", json={"status": "arrived"}, headers=mut()).status_code == 200
    assert client.post(f"/api/gifts/{g}/status", json={"status": "confirmed"}, headers=mut()).status_code == 400
    t = mk_gift(client, ids.o, "Scarf", to=[ids.jordan], frm=[ids.casey])
    assert client.post(f"/api/gifts/{t}/status", json={"status": "confirmed"}, headers=mut()).status_code == 200
    assert client.post(f"/api/gifts/{t}/status", json={"status": "given"}, headers=mut()).status_code == 200
    assert client.post(f"/api/gifts/{t}/status", json={"status": "wrapped"}, headers=mut()).status_code == 400
    assert client.post(f"/api/gifts/{t}/status", json={"status": 3}, headers=mut()).status_code == 400
    assert client.post("/api/gifts/999/status", json={"status": "given"}, headers=mut()).status_code == 404


def test_status_on_create_must_belong_to_the_track(client, ids):
    body = {"occasion_id": ids.o, "title": "X", "recipient_ids": [ids.jordan], "giver_ids": [ids.casey], "status": "wrapped"}
    assert client.post("/api/gifts", json=body, headers=mut()).status_code == 400


def test_santa_only_on_gifts_from_a_user(client, ids, unlock):
    unlock()
    ok = mk_gift(client, ids.o, "Bike", to=[ids.kid1], frm=[ids.alex, ids.sam])
    assert client.patch(f"/api/gifts/{ok}", json={"santa": True}, headers=mut()).status_code == 200
    no = mk_gift(client, ids.o, "Scarf", to=[ids.kid1], frm=[ids.casey])
    assert client.patch(f"/api/gifts/{no}", json={"santa": True}, headers=mut()).status_code == 400
    nobody = mk_gift(client, ids.o, "Idea", to=[ids.kid1])
    assert client.patch(f"/api/gifts/{nobody}", json={"santa": True}, headers=mut()).status_code == 400
    body = {"occasion_id": ids.o, "title": "X", "recipient_ids": [ids.kid1], "giver_ids": [ids.casey], "santa": True}
    assert client.post("/api/gifts", json=body, headers=mut()).status_code == 400


def test_santa_in_the_same_patch_that_changes_givers_is_checked_against_the_new_givers(client, ids, unlock):
    unlock()
    g = mk_gift(client, ids.o, "Bike", to=[ids.kid1], frm=[ids.alex])
    r = client.patch(f"/api/gifts/{g}", json={"giver_ids": [ids.casey], "santa": True}, headers=mut())
    assert r.status_code == 400
    # and nothing was applied
    assert con_rows("SELECT giver_ids FROM (SELECT group_concat(person_id) AS giver_ids FROM gift_givers WHERE gift_id=?)", g) == [(str(ids.alex),)]


def test_moving_to_another_giver_clears_santa_and_maps_status(con, ids):
    g = gifts.create(con, USER, {"occasion_id": ids.o, "title": "Bike", "recipient_ids": [ids.kid1],
                                 "giver_ids": [ids.alex], "santa": True, "status": "arrived"})
    gifts.set_givers(con, g, [ids.casey])
    row = con.execute("SELECT status, santa, told_at FROM gifts WHERE id=?", (g,)).fetchone()
    assert (row["status"], row["santa"]) == ("told", 0) and row["told_at"]


def test_moving_back_to_an_owner_maps_status_to_decided_and_given_survives(con, ids):
    g = gifts.create(con, USER, {"occasion_id": ids.o, "title": "Book", "recipient_ids": [ids.jordan], "giver_ids": [ids.casey]})
    gifts.set_givers(con, g, [ids.sam])
    assert con.execute("SELECT status FROM gifts WHERE id=?", (g,)).fetchone()[0] == "decided"
    gifts.set_status(con, g, "given")
    gifts.set_givers(con, g, [ids.casey])
    assert con.execute("SELECT status FROM gifts WHERE id=?", (g,)).fetchone()[0] == "given"
    gifts.set_givers(con, g, [ids.alex])
    assert con.execute("SELECT status FROM gifts WHERE id=?", (g,)).fetchone()[0] == "given"


def test_changing_givers_within_a_track_keeps_status_and_santa(con, ids):
    g = gifts.create(con, USER, {"occasion_id": ids.o, "title": "Bike", "recipient_ids": [ids.kid1],
                                 "giver_ids": [ids.alex], "santa": True, "status": "wrapped"})
    gifts.set_givers(con, g, [ids.alex, ids.sam])
    row = con.execute("SELECT status, santa FROM gifts WHERE id=?", (g,)).fetchone()
    assert (row["status"], row["santa"]) == ("wrapped", 1)
    gifts.set_givers(con, g, [])  # no owner left to be Santa
    assert con.execute("SELECT santa FROM gifts WHERE id=?", (g,)).fetchone()[0] == 0


def test_set_givers_unknown_person_is_400(con, ids):
    g = gifts.create(con, USER, {"occasion_id": ids.o, "title": "Book", "recipient_ids": [ids.jordan]})
    with pytest.raises(common.ApiError) as e:
        gifts.set_givers(con, g, [99999])
    assert e.value.status == 400


@pytest.mark.parametrize("patch", [{"title": ""}, {"recipient_ids": []}, {"url": "javascript:alert(1)"},
                                   {"price": "abc"}, {"recipient_ids": [99999]}, {"giver_ids": [99999]},
                                   {"recipient_ids": "1"}, {"recipient_ids": [True]}, {"title": "x" * 201},
                                   {"url": "ftp://example.com"}, {"santa": "yes"}])
def test_bad_gift_fields_are_400(client, ids, patch):
    g = mk_gift(client, ids.o, "Book", to=[ids.jordan])
    assert client.patch(f"/api/gifts/{g}", json=patch, headers=mut()).status_code == 400


def test_failed_patch_changes_nothing(client, ids):
    g = mk_gift(client, ids.o, "Book", to=[ids.jordan])
    client.patch(f"/api/gifts/{g}", json={"title": "New", "price": "abc"}, headers=mut())
    assert client.get(f"/api/gifts/{g}", headers=auth()).get_json()["title"] == "Book"


@pytest.mark.parametrize("body", [{"title": "X", "recipient_ids": [1]},                       # no occasion
                                  {"occasion_id": 999, "title": "X", "recipient_ids": [1]},
                                  {"occasion_id": "1", "title": "X", "recipient_ids": [1]},
                                  {"title": "X"}, {}])
def test_bad_gift_create_is_400(client, ids, body):
    if body.get("occasion_id") == "1":
        body = {**body, "occasion_id": str(ids.o)}
    assert client.post("/api/gifts", json=body, headers=mut()).status_code == 400


def test_valid_urls_and_clearing(client, ids):
    g = mk_gift(client, ids.o, "Book", to=[ids.jordan], url="https://example.com/book", store="Shop", notes="n")
    body = client.get(f"/api/gifts/{g}", headers=auth()).get_json()
    assert (body["url"], body["store"], body["notes"]) == ("https://example.com/book", "Shop", "n")
    body = client.patch(f"/api/gifts/{g}", json={"url": "", "store": ""}, headers=mut()).get_json()
    assert (body["url"], body["store"]) == (None, None)


def test_update_replaces_recipients_and_givers(client, ids):
    g = mk_gift(client, ids.o, "Book", to=[ids.jordan], frm=[ids.alex])
    body = client.patch(f"/api/gifts/{g}", json={"recipient_ids": [ids.riley, ids.casey], "giver_ids": [ids.sam]},
                        headers=mut()).get_json()
    assert body["recipient_ids"] == sorted([ids.riley, ids.casey]) and body["giver_ids"] == [ids.sam]


def test_delete_gift_removes_its_links(client, ids):
    g = mk_gift(client, ids.o, "Book", to=[ids.jordan], frm=[ids.alex])
    assert client.delete(f"/api/gifts/{g}", headers=mut()).status_code == 200
    assert client.get(f"/api/gifts/{g}", headers=auth()).status_code == 404
    assert con_rows("SELECT COUNT(*) FROM gift_recipients") == [(0,)] and con_rows("SELECT COUNT(*) FROM gift_givers") == [(0,)]
    assert client.delete(f"/api/gifts/{g}", headers=mut()).status_code == 404


def test_person_gifts_in_an_occasion_include_joint_gifts(client, ids):
    mk_gift(client, ids.o, "Joint", to=[ids.jordan, ids.riley])
    mk_gift(client, ids.o, "Solo", to=[ids.riley])

    def titles(pid):
        return [g["title"] for g in client.get(f"/api/occasions/{ids.o}/people/{pid}/gifts", headers=auth()).get_json()]

    assert titles(ids.jordan) == ["Joint"] and titles(ids.riley) == ["Joint", "Solo"]
    assert client.get(f"/api/occasions/999/people/{ids.jordan}/gifts", headers=auth()).status_code == 404
    assert client.get(f"/api/occasions/{ids.o}/people/999/gifts", headers=auth()).status_code == 404


def test_history_is_newest_occasion_first(client, ids):
    from conftest import mk_occasion
    old = mk_occasion(client, "Christmas 2025", "christmas", "2025-12-25")
    mk_gift(client, old, "Old", to=[ids.jordan])
    mk_gift(client, ids.o, "New", to=[ids.jordan, ids.riley])
    h = client.get(f"/api/people/{ids.jordan}/history", headers=auth()).get_json()
    assert h["person"]["name"] == "Jordan"
    assert [(g["title"], g["occasion"]["name"]) for g in h["gifts"]] == [("New", "Christmas 2026"), ("Old", "Christmas 2025")]
    assert client.get("/api/people/999/history", headers=auth()).status_code == 404


def test_giver_only_person_cannot_be_deleted(client, ids, unlock):
    unlock()                                  # deleting a person needs the unlock (tests/test_privacy.py)
    mk_gift(client, ids.o, "Book", to=[ids.jordan], frm=[ids.casey])
    r = client.delete(f"/api/people/{ids.casey}", headers=mut())
    assert r.status_code == 409


def test_santa_is_in_the_json_only_when_unlocked(con, ids):
    g = gifts.create(con, USER, {"occasion_id": ids.o, "title": "Bike", "recipient_ids": [ids.kid1], "giver_ids": [ids.alex], "santa": True})
    row = gifts.get(con, g)
    assert gifts.to_json(con, row, unlocked=True)["santa"] is True
    assert "santa" not in gifts.to_json(con, row, unlocked=False)
