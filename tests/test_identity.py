import re

import jwt
import pytest

import app as tracker
import identity
from conftest import LAN_IP, OTHER_KEY, auth, mut, token


def test_lan_request_without_cloudflare_headers_is_forbidden(client):
    for path in ("/", "/api/me", "/api/people"):
        r = client.get(path, environ_base={"REMOTE_ADDR": LAN_IP})
        assert r.status_code == 403, path


def test_every_route_but_healthz_is_forbidden_from_the_lan(client):
    """Review focus 1: walk every rule in url_map, with every method, plus paths that match no rule."""
    seen = 0
    paths = [(re.sub(r"<[^>]+>", "1", rule.rule), sorted(rule.methods - {"HEAD", "OPTIONS"}))
             for rule in tracker.app.url_map.iter_rules()]
    paths += [("/", ["GET"]), ("/nope", ["GET", "POST"]), ("/api/nope", ["GET", "DELETE"]), ("/static/style.css", ["GET"])]
    for path, methods in paths:
        for method in methods:
            seen += 1
            r = client.open(path, method=method, json={}, environ_base={"REMOTE_ADDR": LAN_IP},
                            headers={"X-Tracker": "1", "Origin": "http://localhost"})
            if path == "/healthz":
                assert r.status_code == 200
            else:
                assert (r.status_code, r.get_json()) == (403, {"error": "Forbidden"}), (method, path)
    assert seen > 6


def test_healthz_needs_no_identity(client):
    assert client.get("/healthz", environ_base={"REMOTE_ADDR": LAN_IP}).get_json() == {"ok": True}


def test_forged_email_header_alone_is_forbidden(client):
    r = client.get("/api/me", headers={"Cf-Access-Authenticated-User-Email": "alex@example.com"})
    assert r.status_code == 403


def test_valid_jwt_for_allowed_email(client):
    r = client.get("/api/me", headers=auth("alex@example.com"))
    assert r.status_code == 200 and r.get_json()["email"] == "alex@example.com"


def test_me_returns_display_name_and_app_name(client):
    sam = next(p["id"] for p in client.get("/api/people", headers=auth()).get_json() if p["name"] == "Sam")
    assert client.get("/api/me", headers=auth("sam@example.com")).get_json() == \
        {"email": "sam@example.com", "name": "Sam", "person_id": sam, "app_name": "Ledger", "has_pin": False,
         "unlocked": False, "lock_idle_seconds": 300}


def test_me_person_id_follows_a_rename_of_the_users_person(client):
    """The UI offers "me / the other owner / both" as givers, so it needs the person row, not a name match."""
    sam = client.get("/api/me", headers=auth("sam@example.com")).get_json()["person_id"]
    assert client.patch(f"/api/people/{sam}", json={"name": "Dad"}, headers=mut()).status_code == 200
    me = client.get("/api/me", headers=auth("sam@example.com")).get_json()
    assert (me["name"], me["person_id"]) == ("Sam", sam)


def test_email_is_compared_lowercased(client):
    assert client.get("/api/me", headers=auth("Alex@Example.COM")).get_json()["email"] == "alex@example.com"


def test_valid_jwt_for_other_email_is_forbidden(client):
    assert client.get("/api/me", headers=auth("jordan@example.com")).status_code == 403


@pytest.mark.parametrize("kw", [{"aud": "other"}, {"iss": "https://evil.example.com"}, {"exp": -10}, {"key": OTHER_KEY}])
def test_bad_tokens_are_forbidden(client, kw):
    assert client.get("/api/me", headers=auth("alex@example.com", **kw)).status_code == 403


def test_token_without_expiry_is_forbidden(client):
    h = {"Cf-Ray": "test", "Cf-Access-Jwt-Assertion": token("alex@example.com", exp=None)}
    assert client.get("/api/me", headers=h).status_code == 403


def test_unsigned_token_is_forbidden(client):
    t = jwt.encode({"email": "alex@example.com", "aud": "aud-test", "iss": "https://family.cloudflareaccess.com",
                    "exp": 2_000_000_000}, None, algorithm="none")
    assert client.get("/api/me", headers={"Cf-Ray": "test", "Cf-Access-Jwt-Assertion": t}).status_code == 403


def test_access_cookie_alone_is_not_identity(client):
    client.set_cookie("CF_Authorization", token("alex@example.com"))
    assert client.get("/api/me", headers={"Cf-Ray": "test"}).status_code == 403
    assert client.get("/api/me", environ_base={"REMOTE_ADDR": LAN_IP}).status_code == 403


def test_unconfigured_cloudflare_admits_nobody(client, monkeypatch):
    monkeypatch.setattr(identity, "CF_AUD", "")
    assert client.get("/api/me", headers=auth()).status_code == 403


def test_dev_email_only_from_loopback_without_cf_headers(client, monkeypatch):
    monkeypatch.setattr(identity, "DEV_EMAIL", "alex@example.com")
    assert client.get("/api/me", environ_base={"REMOTE_ADDR": "127.0.0.1"}).status_code == 200
    assert client.get("/api/me", environ_base={"REMOTE_ADDR": "192.0.2.50"}).status_code == 403
    assert client.get("/api/me", environ_base={"REMOTE_ADDR": "127.0.0.1"}, headers={"Cf-Ray": "x"}).status_code == 403


@pytest.mark.parametrize("header", identity.CF_HEADERS)
def test_dev_email_is_off_when_any_cloudflare_header_is_present(client, monkeypatch, header):
    monkeypatch.setattr(identity, "DEV_EMAIL", "alex@example.com")
    assert client.get("/api/me", environ_base={"REMOTE_ADDR": "127.0.0.1"}, headers={header: "x"}).status_code == 403


def test_resolve_user_directly():
    assert identity.resolve_user({}, "127.0.0.1") is None  # DEV_EMAIL is empty in tests
    assert identity.resolve_user(auth("sam@example.com"), "203.0.113.5") == {"email": "sam@example.com", "name": "Sam"}


def test_mutation_needs_header_json_and_origin(client):
    h = auth("alex@example.com")
    assert client.post("/api/groups", json={"name": "A"}, headers=h).status_code == 400            # no X-Tracker
    assert client.post("/api/groups", data="name=A", headers={**h, "X-Tracker": "1"}).status_code == 400
    assert client.post("/api/groups", json={"name": "A"},
                       headers={**h, "X-Tracker": "1", "Origin": "https://evil.example.com"}).status_code == 403


def test_mutation_without_origin_is_forbidden(client):
    assert client.post("/api/groups", json={"name": "A"}, headers={**auth(), "X-Tracker": "1"}).status_code == 403


def test_mutation_with_header_json_and_same_origin_passes(client):
    assert client.post("/api/groups", json={"name": "A"}, headers=mut()).status_code == 201


def test_json_body_must_be_an_object(client):
    assert client.post("/api/groups", json=["A"], headers=mut()).status_code == 400
    assert client.post("/api/groups", data="{not json", headers=mut()).status_code == 400


def test_security_headers(client):
    r = client.get("/api/me", headers=auth())
    assert "script-src 'self'" in r.headers["Content-Security-Policy"]
    assert r.headers["Cache-Control"] == "no-store"
    assert r.headers["Referrer-Policy"] == "no-referrer"


def test_security_headers_on_refusals_too(client):
    r = client.get("/api/me", environ_base={"REMOTE_ADDR": LAN_IP})
    assert r.status_code == 403 and r.headers["Cache-Control"] == "no-store"
    assert "frame-ancestors 'none'" in r.headers["Content-Security-Policy"]
