import os
import stat

import pytest

import app as tracker
import lock
import settings
from conftest import PIN, auth, con_rows, mk_gift, mk_protected_gift, mut, set_pin


def unlock_status(client, pin, email="alex@example.com"):
    return client.post("/api/lock/unlock", json={"pin": pin}, headers=mut(email)).status_code


def me(client, email="alex@example.com"):
    return client.get("/api/me", headers=auth(email)).get_json()


# ---------------------------------------------------------------- the plan's tests
def test_unlock_flow_and_expiry(client, ids, clock):
    set_pin(client, "4821")
    g = mk_protected_gift(ids)                                   # inserted directly through gifts.create
    assert client.get(f"/api/gifts/{g}", headers=auth()).status_code == 404
    assert client.post("/api/lock/unlock", json={"pin": "4821"}, headers=mut()).status_code == 200
    assert client.get(f"/api/gifts/{g}", headers=auth()).status_code == 200
    clock.advance(299); assert client.get(f"/api/gifts/{g}", headers=auth()).status_code == 200   # slides
    clock.advance(301); assert client.get(f"/api/gifts/{g}", headers=auth()).status_code == 404


def test_five_wrong_pins_lock_out_for_60_seconds(client, clock):
    set_pin(client, "4821")
    for _ in range(5):
        assert client.post("/api/lock/unlock", json={"pin": "0000"}, headers=mut()).status_code == 403
    assert client.post("/api/lock/unlock", json={"pin": "4821"}, headers=mut()).status_code == 429
    clock.advance(61)
    assert client.post("/api/lock/unlock", json={"pin": "4821"}, headers=mut()).status_code == 200


@pytest.mark.parametrize("pin", ["123", "123456789", "12a4", "", " 1234", "1234 ", "١٢٣٤", 1234, None, ["1234"]])
def test_bad_pin_shape_is_400(client, pin):
    assert client.post("/api/lock/pin", json={"pin": pin}, headers=mut()).status_code == 400
    assert me(client)["has_pin"] is False


def test_unlock_cookie_is_bound_to_its_user(client):
    set_pin(client, "4821", email="alex@example.com")
    client.post("/api/lock/unlock", json={"pin": "4821"}, headers=mut("alex@example.com"))
    assert client.get("/api/me", headers=auth("sam@example.com")).get_json()["unlocked"] is False


def test_gift_shared_with_a_protected_person_is_hidden_everywhere_when_locked(client, ids):
    g = mk_protected_gift(ids, title="Family trip", to=[ids.kid1, ids.jordan], frm=[ids.alex], price=90000, status="arrived")
    lst = client.get(f"/api/occasions/{ids.o}/people/{ids.jordan}/gifts", headers=auth()).get_json()
    assert g not in [x["id"] for x in lst]
    s = client.get(f"/api/occasions/{ids.o}/summary", headers=auth()).get_json()
    jordan = next(p for grp in s["groups"] for p in grp["people"] if p["id"] == ids.jordan)
    assert jordan["spent_cents"] == 0 and jordan["gift_count"] == 0 and jordan["headline"] is None
    assert client.get(f"/api/people/{ids.jordan}/history", headers=auth()).get_json()["gifts"] == []


def test_writes_to_protected_gifts_are_refused_when_locked(client, ids):
    g = mk_protected_gift(ids)
    assert client.patch(f"/api/gifts/{g}", json={"title": "x"}, headers=mut()).status_code == 404
    assert client.post("/api/gifts", json={"occasion_id": ids.o, "title": "x", "recipient_ids": [ids.kid1]},
                       headers=mut()).status_code == 423


# ---------------------------------------------------------------- PIN storage and checks
def test_pin_is_stored_as_scrypt_with_a_16_byte_salt(client, con):
    set_pin(client, "4821")
    row = con.execute("SELECT pin_hash, pin_salt, pin_fails, pin_locked_until FROM users WHERE email='alex@example.com'").fetchone()
    assert len(row["pin_salt"]) == 16 and b"4821" not in row["pin_hash"]
    import hashlib
    assert row["pin_hash"] == hashlib.scrypt(b"4821", salt=row["pin_salt"], n=2**14, r=8, p=1)
    assert lock.check_pin(con, "alex@example.com", "4821") is True
    assert lock.check_pin(con, "alex@example.com", "4822") is False
    assert lock.check_pin(con, "sam@example.com", "4821") is False      # no PIN yet


def test_same_pin_gets_a_different_salt_per_user(client):
    set_pin(client, "4821", "alex@example.com"); set_pin(client, "4821", "sam@example.com")
    (a, b) = con_rows("SELECT pin_salt, pin_hash FROM users ORDER BY email")
    assert a[0] != b[0] and a[1] != b[1]


def test_changing_a_pin_needs_the_current_one(client):
    set_pin(client, "4821")
    assert client.post("/api/lock/pin", json={"pin": "1111"}, headers=mut()).status_code == 400
    assert client.post("/api/lock/pin", json={"pin": "1111", "current": "0000"}, headers=mut()).status_code == 403
    assert client.post("/api/lock/pin", json={"pin": "1111", "current": "4821"}, headers=mut()).status_code == 200
    assert unlock_status(client, "4821") == 403 and unlock_status(client, "1111") == 200


def test_wrong_current_pins_count_toward_the_lock_out(client, clock):
    """Otherwise /api/lock/pin would be a brute-force oracle that skips the five-try limit."""
    set_pin(client, "4821")
    for _ in range(5):
        assert client.post("/api/lock/pin", json={"pin": "1111", "current": "0000"}, headers=mut()).status_code == 403
    assert client.post("/api/lock/pin", json={"pin": "1111", "current": "4821"}, headers=mut()).status_code == 429
    assert unlock_status(client, "4821") == 429


def test_setting_a_pin_does_not_unlock(client):
    set_pin(client)
    assert me(client)["unlocked"] is False


def test_unlock_without_a_pin_is_409_and_the_user_stays_locked(client):
    assert unlock_status(client, "4821") == 409
    alex = next(p["id"] for p in client.get("/api/people", headers=auth()).get_json() if p["name"] == "Alex")
    assert me(client) == {"email": "alex@example.com", "name": "Alex", "person_id": alex, "app_name": "Ledger",
                          "has_pin": False, "unlocked": False, "lock_idle_seconds": 300}


@pytest.mark.parametrize("body", [{}, {"pin": 4821}, {"pin": None}])
def test_unlock_with_a_malformed_pin_is_400(client, body):
    set_pin(client)
    assert client.post("/api/lock/unlock", json=body, headers=mut()).status_code == 400


def test_a_correct_pin_resets_the_count(client, clock):
    set_pin(client)
    for _ in range(4):
        assert unlock_status(client, "0000") == 403
    assert unlock_status(client, PIN) == 200
    for _ in range(4):
        assert unlock_status(client, "0000") == 403
    assert unlock_status(client, PIN) == 200


def test_after_a_lock_out_each_wrong_pin_locks_out_again(client, clock):
    set_pin(client)
    for _ in range(5):
        unlock_status(client, "0000")
    clock.advance(61)
    assert unlock_status(client, "0000") == 403
    assert unlock_status(client, PIN) == 429                   # one guess per minute until a correct PIN
    clock.advance(61)
    assert unlock_status(client, PIN) == 200
    assert con_rows("SELECT pin_fails, pin_locked_until FROM users WHERE email='alex@example.com'") == [(0, 0)]


def test_lock_out_is_per_user(client, clock):
    set_pin(client, PIN, "alex@example.com"); set_pin(client, PIN, "sam@example.com")
    for _ in range(5):
        unlock_status(client, "0000", "alex@example.com")
    assert unlock_status(client, PIN, "sam@example.com") == 200


# ---------------------------------------------------------------- the cookie
def _cookie_header(resp):
    return next(h for h in resp.headers.getlist("Set-Cookie") if h.startswith(lock.COOKIE + "="))


def test_unlock_cookie_flags(client):
    set_pin(client)
    r = client.post("/api/lock/unlock", json={"pin": PIN}, headers=mut())
    h = _cookie_header(r)
    for part in ("HttpOnly", "Secure", "SameSite=Strict", "Path=/", "Max-Age=300"):
        assert part in h, h


def test_every_unlocked_response_reissues_the_cookie(client, ids, unlock):
    unlock()
    for path in ("/api/me", "/api/people", f"/api/occasions/{ids.o}/summary"):
        assert lock.COOKIE + "=" in " ".join(client.get(path, headers=auth()).headers.getlist("Set-Cookie")), path


def test_lock_ends_the_unlock(client, ids, unlock):
    g = mk_protected_gift(ids)
    unlock()
    assert client.get(f"/api/gifts/{g}", headers=auth()).status_code == 200
    r = client.post("/api/lock/lock", json={}, headers=mut())
    assert r.status_code == 200 and "Max-Age=0" in _cookie_header(r)
    assert client.get(f"/api/gifts/{g}", headers=auth()).status_code == 404
    assert me(client)["unlocked"] is False


def test_a_tampered_or_foreign_cookie_is_locked(client, ids, unlock):
    g = mk_protected_gift(ids)
    unlock()
    good = client.get_cookie(lock.COOKIE).value
    client.set_cookie(lock.COOKIE, good[:-2] + ("AA" if not good.endswith("AA") else "BB"))
    assert client.get(f"/api/gifts/{g}", headers=auth()).status_code == 404
    from itsdangerous import URLSafeTimedSerializer
    forged = URLSafeTimedSerializer(b"x" * 32, salt=lock.SALT).dumps({"e": "alex@example.com"})
    client.set_cookie(lock.COOKIE, forged)
    assert client.get(f"/api/gifts/{g}", headers=auth()).status_code == 404


def test_a_cookie_from_the_future_is_locked(client, ids, unlock, clock):
    g = mk_protected_gift(ids)
    unlock()
    clock.advance(-10)
    assert client.get(f"/api/gifts/{g}", headers=auth()).status_code == 404


def test_secret_is_32_bytes_mode_0600_and_survives_a_restart(client, ids, unlock):
    path = os.path.join(settings.DATA_DIR, "secret.key")
    assert os.path.getsize(path) == 32 and stat.S_IMODE(os.stat(path).st_mode) == 0o600
    g = mk_protected_gift(ids)
    unlock()
    tracker.init_db()                                           # a restart reads the same secret
    assert client.get(f"/api/gifts/{g}", headers=auth()).status_code == 200


def test_a_truncated_secret_refuses_to_start(tmp_path):
    (tmp_path / "secret.key").write_bytes(b"short")
    with pytest.raises(RuntimeError):
        lock.load_secret(str(tmp_path))


def test_load_secret_creates_once(tmp_path):
    lock.load_secret(str(tmp_path)); first = (tmp_path / "secret.key").read_bytes()
    lock.load_secret(str(tmp_path))
    assert (tmp_path / "secret.key").read_bytes() == first and sorted(os.listdir(tmp_path)) == ["secret.key"]
    lock.load_secret(settings.DATA_DIR)                         # put the test database's secret back


def test_lock_idle_seconds_setting_is_used(client, ids, unlock, clock, monkeypatch):
    monkeypatch.setattr(settings, "LOCK_IDLE_SECONDS", 60)
    g = mk_protected_gift(ids)
    unlock()
    assert me(client)["lock_idle_seconds"] == 60
    clock.advance(61)
    assert client.get(f"/api/gifts/{g}", headers=auth()).status_code == 404


# ---------------------------------------------------------------- Santa behind the lock
def test_no_santa_in_any_gift_response_while_locked(client, ids):
    r = client.post("/api/gifts", json={"occasion_id": ids.o, "title": "Mug", "recipient_ids": [ids.jordan],
                                        "giver_ids": [ids.alex]}, headers=mut())
    assert r.status_code == 201 and "santa" not in r.get_json()
    g = r.get_json()["id"]
    assert "santa" not in client.get(f"/api/gifts/{g}", headers=auth()).get_json()
    assert "santa" not in client.patch(f"/api/gifts/{g}", json={"title": "Cup"}, headers=mut()).get_json()
    assert "santa" not in client.post(f"/api/gifts/{g}/status", json={"status": "decided"}, headers=mut()).get_json()


def test_santa_writes_need_the_unlock(client, ids, unlock):
    g = mk_gift(client, ids.o, "Mug", to=[ids.jordan], frm=[ids.alex])
    assert client.patch(f"/api/gifts/{g}", json={"santa": True}, headers=mut()).status_code == 423
    assert client.patch(f"/api/gifts/{g}", json={"santa": False}, headers=mut()).status_code == 423
    body = {"occasion_id": ids.o, "title": "X", "recipient_ids": [ids.jordan], "giver_ids": [ids.alex], "santa": True}
    assert client.post("/api/gifts", json=body, headers=mut()).status_code == 423
    unlock()
    r = client.patch(f"/api/gifts/{g}", json={"santa": True}, headers=mut())
    assert r.status_code == 200 and r.get_json()["santa"] is True


def test_locked_body_is_the_423_shape(client, ids):
    r = client.post("/api/gifts", json={"occasion_id": ids.o, "title": "x", "recipient_ids": [ids.kid1]}, headers=mut())
    assert (r.status_code, r.get_json()) == (423, {"error": "Locked", "locked": True})
