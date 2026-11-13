"""Owners as co-givers of a protected drawer's exchange gift: a child draws the name, a parent buys the gift. The gift is
from the drawer and every owner, so it is on the ours track and counted in money, and it stays exactly as hidden under
the lock as it was when the drawer was its only giver. An owner's own draw is unchanged."""
import pytest
from conftest import auth, bearer, con_rows, mk_gift, mk_token, mut

import agent
import db
import exchanges
import settings
import wishlist
from test_exchanges import lock_now, mk_exchange, put_draw, summary

MARK = "ZZ-COGIVE"


@pytest.fixture
def ex(client, ids, unlock):
    """Unlocked. One exchange (budget 25.00): kid1 drew jordan, kid2 drew riley, alex drew jordan."""
    unlock()
    e = mk_exchange(client, ids, budget=2500)
    for drawer, to in ((ids.kid1, ids.jordan), (ids.kid2, ids.riley), (ids.alex, ids.jordan)):
        assert put_draw(client, e, drawer, to).status_code == 200
    return e


def gift_of(client, gid):
    return client.get(f"/api/gifts/{gid}", headers=auth()).get_json()


def test_a_protected_drawers_gift_is_from_the_drawer_and_every_owner(client, ids, ex):
    gid = mk_gift(client, ids.o, f"{MARK}-A", to=[ids.jordan], frm=[ids.kid1], exchange_id=ex, price="20")
    g = gift_of(client, gid)
    assert (g["giver_ids"], g["track"], g["status"]) == (sorted([ids.alex, ids.sam, ids.kid1]), "ours", "idea")
    assert g["recipient_ids"] == [ids.jordan]
    d = next(x for e in client.get(f"/api/occasions/{ids.o}/exchanges", headers=auth()).get_json() for x in e["draws"]
             if x["drawer_id"] == ids.kid1)
    assert (d["gift_id"], d["price_cents"]) == (gid, 2000)


def test_owners_in_the_body_are_not_duplicated_and_both_drawer_and_owners_are_required(client, ids, ex):
    gid = mk_gift(client, ids.o, f"{MARK}-B", to=[ids.riley], frm=[ids.alex, ids.kid2], exchange_id=ex)
    assert gift_of(client, gid)["giver_ids"] == sorted([ids.alex, ids.sam, ids.kid2])
    assert client.patch(f"/api/gifts/{gid}", json={"giver_ids": [ids.kid2]}, headers=mut()).status_code == 200
    assert gift_of(client, gid)["giver_ids"] == sorted([ids.alex, ids.sam, ids.kid2])   # re-expanded
    for body in ({"giver_ids": [ids.alex]}, {"giver_ids": [ids.kid2, ids.kid3]}, {"giver_ids": [ids.kid2, ids.jordan]}):
        assert client.patch(f"/api/gifts/{gid}", json=body, headers=mut()).status_code == 400, body
    assert gift_of(client, gid)["giver_ids"] == sorted([ids.alex, ids.sam, ids.kid2])


def test_an_owners_own_draw_is_unchanged_and_not_confused_with_a_co_giver(client, ids, ex):
    kid_gift = mk_gift(client, ids.o, f"{MARK}-KID", to=[ids.jordan], frm=[ids.kid1], exchange_id=ex)
    own = mk_gift(client, ids.o, "OWN", to=[ids.jordan], frm=[ids.alex], exchange_id=ex)   # no 409: alex only co-gives
    assert gift_of(client, own)["giver_ids"] == [ids.alex]
    draws = {d["drawer_id"]: d["gift_id"] for e in client.get(f"/api/occasions/{ids.o}/exchanges", headers=auth()).get_json()
             for d in e["draws"]}
    assert (draws[ids.alex], draws[ids.kid1]) == (own, kid_gift)
    # the owner's draw can be redrawn once its own gift is gone; the child's gift is not alex's gift
    assert client.delete(f"/api/gifts/{own}", json={}, headers=mut()).status_code == 200
    assert client.delete(f"/api/exchanges/{ex}/draws/{ids.alex}", json={}, headers=mut()).status_code == 200
    assert client.delete(f"/api/exchanges/{ex}/draws/{ids.kid1}", json={}, headers=mut()).status_code == 409
    # an owner draw with a second owner is not a draw gift
    r = client.post("/api/gifts", json={"occasion_id": ids.o, "title": "x", "recipient_ids": [ids.jordan],
                                        "giver_ids": [ids.alex, ids.sam], "exchange_id": ex}, headers=mut())
    assert r.status_code == 400


def test_linking_an_existing_gift_adds_the_owners_and_moves_it_to_the_ours_track(client, ids, ex):
    gid = mk_gift(client, ids.o, f"{MARK}-C", to=[ids.jordan], frm=[ids.kid1])
    assert gift_of(client, gid)["track"] == "theirs"
    assert client.patch(f"/api/gifts/{gid}", json={"exchange_id": ex}, headers=mut()).status_code == 200
    g = gift_of(client, gid)
    assert (g["giver_ids"], g["track"], g["status"]) == (sorted([ids.alex, ids.sam, ids.kid1]), "ours", "decided")


@pytest.mark.parametrize("who", ["kid1", "alex"])
def test_santa_is_refused_on_any_exchange_gift(client, ids, ex, who):
    frm = [getattr(ids, who)]
    body = {"occasion_id": ids.o, "title": "x", "recipient_ids": [ids.jordan], "giver_ids": frm, "exchange_id": ex, "santa": True}
    assert client.post("/api/gifts", json=body, headers=mut()).status_code == 400
    assert con_rows("SELECT id FROM gifts") == []
    gid = mk_gift(client, ids.o, "ok", to=[ids.jordan], frm=frm, exchange_id=ex)
    assert client.patch(f"/api/gifts/{gid}", json={"santa": True}, headers=mut()).status_code == 400
    plain = mk_gift(client, ids.o, "plain", to=[ids.riley], frm=[ids.alex], santa=True)
    r = client.patch(f"/api/gifts/{plain}", json={"exchange_id": ex}, headers=mut())
    assert r.status_code == 400 and gift_of(client, plain)["exchange_id"] is None


def test_an_exchange_gift_is_ordered_and_wrapped_like_any_owner_purchase(client, ids, ex):
    gid = mk_gift(client, ids.o, f"{MARK}-D", to=[ids.jordan], frm=[ids.kid1], exchange_id=ex)
    ok = {"status": "ordered", "seller": "Shop", "ship_to_id": con_rows("SELECT id FROM addresses")[0][0], "eta": "2026-12-10"}
    assert client.post(f"/api/gifts/{gid}/status", json=ok, headers=mut()).status_code == 200
    assert client.post(f"/api/gifts/{gid}/status", json={"status": "arrived"}, headers=mut()).status_code == 200
    wrap = client.get(f"/api/occasions/{ids.o}/lists/to-wrap", headers=auth()).get_json()["groups"]
    assert [x["title"] for grp in wrap for x in grp["gifts"]] == [f"{MARK}-D"]
    assert client.post(f"/api/gifts/{gid}/status", json={"status": "confirmed"}, headers=mut()).status_code == 400  # theirs-track word


def money(client, ids):
    jordan = next(p for g in summary(client, ids)["groups"] for p in g["people"] if p["id"] == ids.jordan)
    return jordan["spent_cents"], jordan["planned_cents"], jordan["gift_count"]


def test_the_gift_counts_in_spent_and_planned_money_for_its_recipient(client, ids, unlock):
    unlock()
    e = mk_exchange(client, ids)
    put_draw(client, e, ids.kid1, ids.jordan)
    gid = mk_gift(client, ids.o, f"{MARK}-E", to=[ids.jordan], frm=[ids.kid1], exchange_id=e, price="20", status="decided")
    assert money(client, ids) == (0, 2000, 1)
    assert client.post(f"/api/gifts/{gid}/status", json={"status": "wrapped"}, headers=mut()).status_code == 200
    assert money(client, ids) == (2000, 0, 1)


def test_nothing_about_the_gift_shows_while_locked_and_the_owners_do_not_unhide_it(client, ids, ex):
    gid = mk_gift(client, ids.o, f"{MARK}-F", to=[ids.jordan], frm=[ids.kid1], exchange_id=ex, price="42.42", status="arrived")
    control = mk_gift(client, ids.o, "CONTROL", to=[ids.jordan], frm=[ids.alex], price="10", status="arrived")
    assert money(client, ids) == (1000 + 4242, 0, 2)
    unlocked_counts = client.get(f"/api/occasions/{ids.o}/lists/counts", headers=auth()).get_json()
    assert unlocked_counts["to_wrap"] == 2
    lock_now(client)
    assert money(client, ids) == (1000, 0, 1)
    assert client.get(f"/api/occasions/{ids.o}/lists/counts", headers=auth()).get_json()["to_wrap"] == 1
    missing = client.get("/api/gifts/999999", headers=auth())
    r = client.get(f"/api/gifts/{gid}", headers=auth())
    assert (r.status_code, r.get_json()) == (missing.status_code, missing.get_json())
    for verb, body in (("patch", {"title": "x"}), ("post", {"status": "wrapped"})):
        url = f"/api/gifts/{gid}" + ("/status" if verb == "post" else "")
        assert getattr(client, verb)(url, json=body, headers=mut()).status_code == 404
    assert client.delete(f"/api/gifts/{gid}", json={}, headers=mut()).status_code == 404
    pages = [f"/api/occasions/{ids.o}/summary", f"/api/occasions/{ids.o}/exchanges", f"/api/people/{ids.jordan}/gifts",
             f"/api/people/{ids.jordan}/history", f"/api/people/{ids.alex}/gifts", f"/api/people/{ids.sam}/gifts",
             f"/api/occasions/{ids.o}/lists/pending", f"/api/occasions/{ids.o}/lists/to-wrap",
             f"/api/occasions/{ids.o}/lists/where", f"/api/occasions/{ids.o}/lists/counts"]
    for url in pages:
        text = client.get(url, headers=auth()).get_data(as_text=True).lower()
        assert MARK.lower() not in text and "4242" not in text, url
    assert control  # the unprotected control gift is still there
    # a locked write cannot hand the gift to an owner either way
    assert client.patch(f"/api/gifts/{control}", json={"exchange_id": ex, "giver_ids": [ids.kid1]}, headers=mut()).status_code == 423


def test_the_privacy_walks_see_nothing_of_the_gift(client, ids, ex):
    import test_exchanges as te
    import types
    mk_gift(client, ids.o, f"{MARK}-G", to=[ids.jordan], frm=[ids.kid1], exchange_id=ex, price="42.42")
    lock_now(client)
    w = types.SimpleNamespace(ids=ids, ex=ex, visible=0, control=0, group=0, hidden=(), secret=())
    seen = te.test_privacy.walk_gets(client, w)
    assert seen
    for url, text in seen.items():
        assert MARK.lower() not in text.lower() and "4242" not in text, url


def test_delivery_and_shop_tokens_cannot_see_or_write_the_gift(client, ids, ex):
    gid = mk_gift(client, ids.o, f"{MARK}-H", to=[ids.jordan], frm=[ids.kid1], exchange_id=ex, price="42.42", status="ordered")
    assert gift_of(client, gid)["giver_ids"] == sorted([ids.alex, ids.sam, ids.kid1])   # an owner is among the givers
    lock_now(client)
    every = "?status=" + ",".join(agent.ALL_STATUSES)
    for scope in ("delivery", "shop", "full"):
        h = bearer(mk_token(scope, name=f"{scope} locked"))
        listed = client.get("/api/v1/gifts" + every, headers=h).get_data(as_text=True)
        assert MARK not in listed and "4242" not in listed, scope
        assert client.get(f"/api/v1/gifts/{gid}", headers=h).status_code == 404, scope
        assert client.patch(f"/api/v1/gifts/{gid}/order", json={"seller": "pwned"}, headers=h).status_code == 404, scope
        assert client.post(f"/api/v1/gifts/{gid}/status", json={"status": "arrived"}, headers=h).status_code == 404, scope
    assert con_rows("SELECT seller, status FROM gifts") == [(None, "ordered")]


def test_the_wishlist_link_sees_a_protected_drawers_gift_as_before(client, ids, ex):
    """A gift linked to a wish and to a draw (only a PATCH can do that): the link writes the drawer's label, never the
    owners' household, and does not count the gift as bought by us."""
    gid = mk_gift(client, ids.o, f"{MARK}-W", to=[ids.jordan], frm=[ids.kid1], exchange_id=ex, status="decided")
    plain = mk_gift(client, ids.o, "PLAIN", to=[ids.jordan], frm=[ids.alex])
    c = db.connect(settings.DB_PATH)
    try:
        assert exchanges.drawer_only(c, gid, [ids.kid1, ids.alex, ids.sam]) == [ids.kid1]
        assert wishlist._expected(c, gid) == (False, wishlist.label(c, [ids.kid1]))
        assert wishlist.label(c, [ids.kid1]) != wishlist.us_label(c)
        assert exchanges.drawer_only(c, plain, [ids.alex]) == [ids.alex]
    finally:
        c.close()


# ---------------------------------------------------------------- the migration (version 7)
def _v6_database(path):
    c = db.connect(str(path))
    for stmt in db._statements(db.SCHEMA):
        c.execute(stmt)
    for version in (2, 3, 4, 5, 6):
        db.MIGRATIONS[version](c)
    c.execute("INSERT INTO meta VALUES ('schema_version', '6')")
    for pid, name, prot in ((1, "Alex", 0), (2, "Sam", 0), (3, "Jordan", 0), (4, "Kid 1", 1), (5, "Kid 2", 1)):
        c.execute("INSERT INTO people (id, name, protected) VALUES (?, ?, ?)", (pid, name, prot))
    for email, pid in (("alex@example.com", 1), ("sam@example.com", 2)):
        c.execute("INSERT INTO users (email, person_id) VALUES (?, ?)", (email, pid))
    c.execute("INSERT INTO occasions (id, name, kind, date) VALUES (1, 'Christmas 2026', 'christmas', '2026-12-25')")
    c.execute("INSERT INTO exchanges (id, occasion_id, name) VALUES (1, 1, 'Secret Santa')")
    for gid, status, ex, givers in ((1, "told", 1, (4,)), (2, "confirmed", 1, (5,)), (3, "given", 1, (4,)),
                                    (4, "told", None, (4,)), (5, "idea", 1, (1,)), (6, "told", 1, (3,)),
                                    (7, "idea", 1, ())):
        c.execute("INSERT INTO gifts (id, occasion_id, title, status, exchange_id, created_by, created_at, updated_at) "
                  "VALUES (?, 1, 'T', ?, ?, 'a', 0, 0)", (gid, status, ex))
        c.execute("INSERT INTO gift_recipients (gift_id, person_id) VALUES (?, 3)", (gid,))
        for p in givers:
            c.execute("INSERT INTO gift_givers (gift_id, person_id) VALUES (?, ?)", (gid, p))
    return c


def test_a_v6_database_migrates_to_v7_adding_the_owners_to_a_protected_drawers_gifts(tmp_path):
    c = _v6_database(tmp_path / "t.db")
    db.migrate(c)
    assert db.SCHEMA_VERSION >= 7
    assert c.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()[0] == str(db.SCHEMA_VERSION)
    givers = {g: sorted(r[0] for r in c.execute("SELECT person_id FROM gift_givers WHERE gift_id = ?", (g,))) for g in range(1, 8)}
    assert givers == {1: [1, 2, 4], 2: [1, 2, 5], 3: [1, 2, 4], 4: [4], 5: [1], 6: [3], 7: []}
    status = dict(c.execute("SELECT id, status FROM gifts").fetchall())
    assert status == {1: "decided", 2: "arrived", 3: "given", 4: "told", 5: "idea", 6: "told", 7: "idea"}
    snapshot = (c.execute("SELECT * FROM gifts ORDER BY id").fetchall(), c.execute("SELECT * FROM gift_givers ORDER BY 1, 2").fetchall())
    c.execute("UPDATE meta SET value = '6' WHERE key = 'schema_version'")   # run the step again over migrated data
    db.migrate(c)
    assert (c.execute("SELECT * FROM gifts ORDER BY id").fetchall(), c.execute("SELECT * FROM gift_givers ORDER BY 1, 2").fetchall()) == snapshot
    c.close()


def test_the_v7_step_leaves_gifts_alone_when_there_are_no_owners(tmp_path):
    c = _v6_database(tmp_path / "t.db")
    c.execute("DELETE FROM users")
    db.migrate(c)
    assert dict(c.execute("SELECT id, status FROM gifts").fetchall())[1] == "told"
    assert c.execute("SELECT COUNT(*) FROM gift_givers WHERE gift_id = 1").fetchone()[0] == 1
    c.close()
