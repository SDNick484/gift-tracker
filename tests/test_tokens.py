"""Agent tokens: made, listed and revoked by an unlocked owner; shown once, stored as a hash; and /api/v1 admits a
live bearer token and nothing else."""
import hashlib
import logging

from conftest import LAN_IP, auth, bearer, con_rows, mk_gift, mk_token, mut

T = 1_800_000_000  # the `clock` fixture's instant


def post_token(client, name, scope, **extra):
    r = client.post("/api/tokens", json={"name": name, "scope": scope, **extra}, headers=mut())
    assert r.status_code == 201, r.get_json()
    return r.get_json()


def test_a_token_is_shown_once_and_only_its_hash_is_stored(client, unlock, clock):
    unlock()
    j = post_token(client, "Shipping", "delivery")
    assert j["token"].startswith("gt_") and len(j["token"]) == 46
    assert {k: v for k, v in j.items() if k != "token"} == {"id": j["id"], "name": "Shipping", "scope": "delivery", "unlocked": False,
                                                            "created_at": T, "last_used_at": None, "revoked_at": None}
    assert con_rows("SELECT token_hash FROM api_tokens") == [(hashlib.sha256(j["token"].encode()).hexdigest(),)]
    r = client.get("/api/tokens", headers=auth())
    assert r.get_json() == [{"id": j["id"], "name": "Shipping", "scope": "delivery", "unlocked": False,
                             "created_at": T, "last_used_at": None, "revoked_at": None}]
    assert j["token"] not in r.get_data(as_text=True) and j["token"][3:] not in r.get_data(as_text=True)
    two = post_token(client, "Owner", "full", unlocked=True)
    assert two["unlocked"] is True and two["token"] != j["token"]
    assert [t["name"] for t in client.get("/api/tokens", headers=auth()).get_json()] == ["Owner", "Shipping"]  # newest first


def test_managing_tokens_needs_the_unlock(client, ids):
    locked = (423, {"error": "Locked", "locked": True})
    r = client.get("/api/tokens", headers=auth())
    assert (r.status_code, r.get_json()) == locked
    r = client.post("/api/tokens", json={"name": "Shipping", "scope": "delivery"}, headers=mut())
    assert (r.status_code, r.get_json()) == locked
    mk_token("delivery", "Shipping")
    r = client.delete("/api/tokens/1", headers=mut())
    assert (r.status_code, r.get_json()) == locked
    assert con_rows("SELECT name, revoked_at FROM api_tokens") == [("Shipping", None)]


def test_token_validation(client, unlock):
    unlock()
    for body in ({"name": "", "scope": "delivery"}, {"name": "x" * 61, "scope": "delivery"}, {"name": 5, "scope": "shop"},
                 {"name": "A", "scope": "admin"}, {"name": "A", "scope": None}, {"name": "A"},
                 {"name": "A", "scope": "full", "unlocked": "yes"}):
        assert client.post("/api/tokens", json=body, headers=mut()).status_code == 400, body
    for scope in ("delivery", "shop"):
        r = client.post("/api/tokens", json={"name": "A", "scope": scope, "unlocked": True}, headers=mut())
        assert (r.status_code, r.get_json()) == (400, {"error": "Only a full token can see protected gifts."})
    assert con_rows("SELECT COUNT(*) FROM api_tokens") == [(0,)]
    made = post_token(client, "x" * 60, "shop")
    assert client.delete(f"/api/tokens/{made['id']}", headers=mut()).status_code == 200
    r = client.post("/api/tokens", json={"name": "x" * 60, "scope": "full"}, headers=mut())   # taken, even revoked
    assert r.status_code == 409
    assert client.delete("/api/tokens/999", headers=mut()).status_code == 404


def test_bearer_token_admits_v1(client, clock):
    t = mk_token("delivery", "Shipping")
    clock.advance(60)
    r = client.get("/api/v1/me", headers=bearer(t))
    assert (r.status_code, r.get_json()) == (200, {"name": "Shipping", "scope": "delivery", "unlocked": False, "mode": "family"})
    assert con_rows("SELECT last_used_at FROM api_tokens") == [(T + 60,)]
    assert client.get("/api/v1/me", headers={"Authorization": f"bearer {t}"}).status_code == 200   # the scheme in any case
    full = mk_token("full", "Owner", unlocked=True)
    assert client.get("/api/v1/me", headers=bearer(full)).get_json()["unlocked"] is True


def test_cloudflare_identity_without_a_token_is_401_on_v1(client):
    for headers in (auth(), mut(), {**auth(), "Authorization": "Bearer gt_nope"}):
        r = client.get("/api/v1/me", headers=headers)
        assert (r.status_code, r.get_json()) == (401, {"error": "Unauthorized"}), headers
        assert r.headers["WWW-Authenticate"].startswith("Bearer")
    r = client.post("/api/v1/gifts", json={}, headers=mut())
    assert r.status_code == 401


def test_presenting_nothing_is_the_usual_403(client):
    for env in ({"REMOTE_ADDR": LAN_IP}, {"REMOTE_ADDR": "127.0.0.1"}):
        r = client.get("/api/v1/me", environ_base=env)
        assert (r.status_code, r.get_json()) == (403, {"error": "Forbidden"})
        assert "WWW-Authenticate" not in r.headers


def test_unknown_malformed_and_revoked_tokens_are_401(client, unlock):
    t = mk_token("delivery", "Shipping")
    for header in ("Bearer gt_nope", f"Basic {t}", "Bearer", "Bearer ", f"Bearer {t} {t}", t, f"Bearer {t[:-1]}",
                   f"Bearer {t[3:]}"):
        r = client.get("/api/v1/me", headers={"Authorization": header})
        assert (r.status_code, r.get_json()) == (401, {"error": "Unauthorized"}), header
    assert client.get("/api/v1/me", headers=bearer(t)).status_code == 200
    unlock()
    assert client.delete("/api/tokens/1", headers=mut()).get_json()["revoked_at"] is not None
    first = con_rows("SELECT revoked_at FROM api_tokens")
    assert client.delete("/api/tokens/1", headers=mut()).status_code == 200    # again: keeps the first time
    assert con_rows("SELECT revoked_at FROM api_tokens") == first
    r = client.get("/api/v1/me", headers=bearer(t))
    assert (r.status_code, r.get_json()) == (401, {"error": "Unauthorized"})


def test_a_token_gets_nothing_outside_v1(client, ids):
    t = mk_token("full", "Owner", unlocked=True)
    g = mk_gift(client, ids.o, "Lamp", to=[ids.jordan], frm=[ids.alex])
    for path in ("/", "/api/me", f"/api/gifts/{g}", "/api/tokens", "/api/people", "/manifest.json", "/api/v1x", "/static/style.css"):
        for env in ({}, {"REMOTE_ADDR": LAN_IP}):
            r = client.get(path, headers=bearer(t), environ_base=env)
            assert (r.status_code, r.get_json()) == (403, {"error": "Forbidden"}), path
    r = client.patch(f"/api/gifts/{g}", json={"title": "x"}, headers=bearer(t, **{"X-Tracker": "1", "Origin": "http://localhost"}))
    assert r.status_code == 403
    assert con_rows("SELECT title FROM gifts") == [("Lamp",)]


def test_the_token_never_appears_in_logs(client, ids, caplog):
    caplog.set_level(logging.DEBUG)
    t = mk_token("delivery", "Shipping")
    client.get("/api/v1/me", headers=bearer(t))
    client.get("/api/v1/me", headers=bearer(t + "x"))
    client.get("/api/v1/me", headers={**auth(), **bearer(t)})
    assert t not in caplog.text and t[3:] not in caplog.text
