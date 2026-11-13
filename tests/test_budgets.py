import pytest

import budgets
import gifts
from conftest import auth, mk_gift, mk_person, mut, put_plan

USER = {"email": "alex@example.com", "name": "Alex"}


def mk(con, ids, title, to, frm=(), price=None, status=None):
    """A gift inserted through gifts.create (no lock in the way); price is whole cents."""
    data = {"occasion_id": ids.o, "title": title, "recipient_ids": list(to), "giver_ids": list(frm), "price": price}
    if status:
        data["status"] = status
    return gifts.create(con, USER, data)


def summary(client, oid):
    r = client.get(f"/api/occasions/{oid}/summary", headers=auth())
    assert r.status_code == 200, r.get_json()
    return r.get_json()


def person(s, pid):
    return next(p for g in s["groups"] for p in g["people"] if p["id"] == pid)


def test_shares_split_evenly_with_remainder_to_lowest_ids():
    assert budgets.shares(10000, [7, 3, 5]) == {3: 3334, 5: 3333, 7: 3333}


@pytest.mark.parametrize("price,ids,expected", [(9000, [1, 2, 3], {1: 3000, 2: 3000, 3: 3000}),
                                                 (1, [2, 1], {1: 1, 2: 0}),
                                                 (0, [4], {4: 0}),
                                                 (5, [], {})])
def test_shares_always_add_up_to_the_price(price, ids, expected):
    out = budgets.shares(price, ids)
    assert out == expected and sum(out.values()) == (price if ids else 0)


def test_spent_and_planned_follow_status(con, ids):
    mk(con, ids, "Shoes", to=[ids.jordan], frm=[ids.alex], price=4000, status="idea")
    mk(con, ids, "Book", to=[ids.jordan], frm=[ids.alex], price=2500, status="arrived")
    mk(con, ids, "Scarf", to=[ids.jordan], frm=[ids.casey], price=9900, status="confirmed")   # theirs: not counted
    t = budgets.person_totals(con, ids.o, include_protected=True)[ids.jordan]
    assert t == {"spent_cents": 2500, "planned_cents": 4000, "gift_count": 3}


def test_every_ours_status_lands_in_spent_or_planned(con, ids):
    for i, status in enumerate(gifts.OURS):
        mk(con, ids, f"G{i}", to=[ids.jordan], frm=[ids.alex], price=100, status=status)
    t = budgets.person_totals(con, ids.o, include_protected=True)[ids.jordan]
    assert t == {"spent_cents": 400, "planned_cents": 200, "gift_count": 6}


def test_gift_without_price_counts_only_in_gift_count(con, ids):
    mk(con, ids, "Idea", to=[ids.jordan])
    assert budgets.person_totals(con, ids.o, include_protected=True)[ids.jordan] == \
        {"spent_cents": 0, "planned_cents": 0, "gift_count": 1}


def test_shared_gift_is_split_between_its_recipients(con, ids):
    mk(con, ids, "Console", to=[ids.riley, ids.jordan], frm=[ids.alex, ids.sam], price=10001, status="wrapped")
    t = budgets.person_totals(con, ids.o, include_protected=True)
    lo, hi = sorted([ids.jordan, ids.riley])
    assert (t[lo]["spent_cents"], t[hi]["spent_cents"]) == (5001, 5000)


def test_other_occasions_do_not_count(client, con, ids):
    from conftest import mk_occasion
    old = mk_occasion(client, "Christmas 2025", "christmas", "2025-12-25")
    gifts.create(con, USER, {"occasion_id": old, "title": "Old", "recipient_ids": [ids.jordan], "price": 500})
    assert ids.jordan not in budgets.person_totals(con, ids.o, include_protected=True)


def test_include_protected_false_drops_every_gift_with_a_protected_recipient(con, ids):
    mk(con, ids, "Trip", to=[ids.kid1, ids.jordan], frm=[ids.alex], price=90000, status="arrived")
    mk(con, ids, "Book", to=[ids.jordan], frm=[ids.alex], price=2500, status="arrived")
    t = budgets.person_totals(con, ids.o, include_protected=False)
    assert t[ids.jordan] == {"spent_cents": 2500, "planned_cents": 0, "gift_count": 1}
    assert ids.kid1 not in t
    assert budgets.person_totals(con, ids.o, include_protected=True)[ids.jordan]["spent_cents"] == 2500 + 45000


def test_summary_days_until_and_headline(client, ids, freeze_today):
    freeze_today("2026-11-12")            # the fixture occasion is 2026-12-25
    mk_gift(client, ids.o, "Shoes?", to=[ids.jordan], frm=[ids.alex])
    s = summary(client, ids.o)
    assert s["days_until"] == 43
    jordan = person(s, ids.jordan)
    assert jordan["headline"] == "Shoes?" and jordan["gift_count"] == 1


def test_days_until_is_negative_after_the_date(client, ids, freeze_today):
    freeze_today("2026-12-27")
    assert summary(client, ids.o)["days_until"] == -2


def test_headline_is_the_most_recently_updated_gift(client, con, ids):
    a = mk_gift(client, ids.o, "First", to=[ids.jordan])
    b = mk_gift(client, ids.o, "Second", to=[ids.jordan])
    con.execute("UPDATE gifts SET updated_at = 100 WHERE id = ?", (b,))
    con.execute("UPDATE gifts SET updated_at = 200 WHERE id = ?", (a,))
    assert person(summary(client, ids.o), ids.jordan)["headline"] == "First"
    assert person(summary(client, ids.o), ids.riley)["headline"] is None


def test_summary_person_carries_plan_budget_and_totals(client, ids):
    put_plan(client, ids.o, ids.jordan, "joint", 7500)
    mk_gift(client, ids.o, "Book", to=[ids.jordan], frm=[ids.alex], price="25", status="ordered")
    p = person(summary(client, ids.o), ids.jordan)
    assert {k: p[k] for k in ("name", "plan", "budget_cents", "spent_cents", "planned_cents", "gift_count")} == \
        {"name": "Jordan", "plan": "joint", "budget_cents": 7500, "spent_cents": 2500, "planned_cents": 0, "gift_count": 1}
    assert set(p) == {"id", "name", "color", "plan", "budget_cents", "spent_cents", "planned_cents", "gift_count", "headline"}
    assert person(summary(client, ids.o), ids.riley)["plan"] is None


def test_summary_groups_in_order_with_ungrouped_last(client, ids):
    b = client.post("/api/groups", json={"name": "Side B", "sort_order": 2}, headers=mut()).get_json()["id"]
    a = client.post("/api/groups", json={"name": "Side A", "sort_order": 1}, headers=mut()).get_json()["id"]
    pa, pb = mk_person(client, "Pat", group_id=a), mk_person(client, "Morgan", group_id=b)
    s = summary(client, ids.o)
    assert [(g["id"], g["name"]) for g in s["groups"]] == [(a, "Side A"), (b, "Side B"), (None, "Ungrouped")]
    assert [p["id"] for p in s["groups"][0]["people"]] == [pa]
    assert [p["id"] for p in s["groups"][1]["people"]] == [pb]
    assert [p["id"] for p in s["groups"][2]["people"]] == [ids.jordan, ids.riley, ids.casey]


def test_summary_leaves_out_the_owners_unless_they_are_in_the_occasion(client, ids):
    everyone = {p["id"] for g in summary(client, ids.o)["groups"] for p in g["people"]}
    assert ids.alex not in everyone and ids.sam not in everyone
    put_plan(client, ids.o, ids.alex, "individual", None)
    assert ids.alex in {p["id"] for g in summary(client, ids.o)["groups"] for p in g["people"]}


def test_summary_inactive_person_appears_only_with_a_plan_or_gift(client, ids):
    client.patch(f"/api/people/{ids.casey}", json={"active": False}, headers=mut())
    assert ids.casey not in {p["id"] for g in summary(client, ids.o)["groups"] for p in g["people"]}
    mk_gift(client, ids.o, "Mug", to=[ids.casey])
    assert ids.casey in {p["id"] for g in summary(client, ids.o)["groups"] for p in g["people"]}


def test_summary_puts_protected_people_only_in_the_protected_section(client, ids, unlock):
    unlock()
    s = summary(client, ids.o)
    in_groups = {p["id"] for g in s["groups"] for p in g["people"]}
    assert not in_groups & {ids.kid1, ids.kid2, ids.kid3}
    assert s["protected"]["locked"] is False
    assert [p["id"] for p in s["protected"]["people"]] == [ids.kid1, ids.kid2, ids.kid3]


def test_summary_of_unknown_occasion_is_404(client):
    assert client.get("/api/occasions/999/summary", headers=auth()).status_code == 404


def test_summary_occasion_block(client, ids):
    assert summary(client, ids.o)["occasion"] == {"id": ids.o, "name": "Christmas 2026", "kind": "christmas",
                                                   "date": "2026-12-25", "archived": False}
