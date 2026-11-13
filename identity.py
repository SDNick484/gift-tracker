"""Who is asking: a verified Cloudflare Access JWT whose email is in ALLOWED_EMAILS, and nothing else.

There is no LAN role. The children are on the home network, so an address says nothing about who is asking.
The one exception is development: DEV_EMAIL is set, the request comes from loopback, and it carries none of
CF_HEADERS (a request through the tunnel always carries Cf-Ray, so the exception can never apply to it).

The JWT is read from the Cf-Access-Jwt-Assertion header only, which Cloudflare adds to every request it lets
through. Unlike the wishlist, the CF_Authorization cookie is not a fallback: one source of identity is easier to
reason about.
Ported from family-wishlist's _verify_cf_token and jwk_client()."""
import ipaddress
import logging

import jwt
from jwt import PyJWKClient

from settings import ALLOWED_EMAILS, CF_AUD, CF_TEAM_DOMAIN, DEV_EMAIL, USERS

CF_HEADERS = ("Cf-Ray", "Cf-Connecting-Ip", "Cf-Access-Jwt-Assertion", "Cf-Access-Authenticated-User-Email")

log = logging.getLogger(__name__)

_jwk_client = None  # tests replace this with a stub that returns their own key


def jwk_client():
    global _jwk_client
    if _jwk_client is None and CF_TEAM_DOMAIN:
        _jwk_client = PyJWKClient(f"https://{CF_TEAM_DOMAIN}/cdn-cgi/access/certs", cache_keys=True, lifespan=3600)
    return _jwk_client


def _verify_cf_token(token):
    """The token's email, lowercased, if the token is valid for this Access application; else None."""
    client = jwk_client()
    if not (client and CF_AUD and CF_TEAM_DOMAIN):
        log.warning("Access JWT rejected: CF_TEAM_DOMAIN or CF_AUD is not configured")
        return None
    try:
        key = client.get_signing_key_from_jwt(token).key
        claims = jwt.decode(token, key, algorithms=["RS256"], audience=CF_AUD, issuer=f"https://{CF_TEAM_DOMAIN}",
                            options={"require": ["exp", "aud", "iss"]})
    except Exception as exc:  # noqa: BLE001 (any failure means "not authenticated")
        log.warning("Access JWT rejected: %s", type(exc).__name__)
        return None
    email = claims.get("email")
    if not isinstance(email, str):
        return None
    return email.strip().lower() or None


def _is_loopback(addr):
    try:
        return ipaddress.ip_address(addr).is_loopback
    except ValueError:
        return False


def _user(email):
    return {"email": email, "name": USERS.get(email, email)}


def resolve_user(headers, remote_addr):
    """{"email", "name"} for an admitted request, else None. `headers` is request.headers (case-insensitive)."""
    if any(h in headers for h in CF_HEADERS):
        token = headers.get("Cf-Access-Jwt-Assertion")
        email = _verify_cf_token(token) if token else None
        if email and email in ALLOWED_EMAILS:
            return _user(email)
        if email:
            log.warning("Access JWT rejected: email is not in ALLOWED_EMAILS")  # the address itself is not logged
        return None
    if DEV_EMAIL and _is_loopback(remote_addr or ""):
        return _user(DEV_EMAIL)
    return None
