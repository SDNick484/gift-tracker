"""The wishlist link (wishlist.py, docs/wishlist-feed.md): settings, the guarded transport, the cached feed, wishes on
the board, reservations written back, and what must never cross.

The network is stubbed: `wishlist._open` is the one function that touches it, and the `wl` fixture replaces it with
a scripted wishlist that records every request. Every request any test in this file makes is checked against the
allowlist as it is made (StubWishlist.open) and again, all together, by the last test in the file."""
import os
import subprocess
import sys

import pytest

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# ---------------------------------------------------------------- settings
def _import_settings(**env):
    full = {**os.environ, **env}
    return subprocess.run([sys.executable, "-c", "import settings; print(settings.WISHLIST_URL)"], cwd=ROOT_DIR,
                          env=full, capture_output=True, text=True)


@pytest.mark.parametrize("raw", ["ftp://192.0.2.10/", "file:///etc/passwd", "javascript:alert(1)", "192.0.2.10:8090",
                                 "http://", "https://user:pw@wishlist.example.com", "http://wishlist.example.com/?a=1",
                                 "http://wishlist.example.com/#x", "http://wishlist.example.com:99999",
                                 "http://wishlist.example.com/a b", "http://wishlist.example.com/%2e%2e"])
def test_a_bad_wishlist_url_stops_the_app_at_start_up(raw):
    r = _import_settings(WISHLIST_URL=raw, WISHLIST_TOKEN="wl-test-token")
    assert r.returncode != 0 and "WISHLIST_URL must be an http:// or https:// address" in r.stderr, r.stderr


@pytest.mark.parametrize("token", ["", "  ", "a b", "tab\there"])
def test_a_wishlist_url_needs_a_token(token):
    r = _import_settings(WISHLIST_URL="http://192.0.2.10:8090", WISHLIST_TOKEN=token)
    assert r.returncode != 0 and "WISHLIST_TOKEN must be set" in r.stderr, r.stderr


@pytest.mark.parametrize("raw,url", [("http://192.0.2.10:8090", "http://192.0.2.10:8090"),
                                     (" https://wishlist.example.com/ ", "https://wishlist.example.com"),
                                     ("http://192.0.2.10/wishlist/", "http://192.0.2.10/wishlist"),
                                     ("HTTP://192.0.2.10", "http://192.0.2.10"), ("", "")])
def test_good_wishlist_urls_are_normalized(raw, url):
    r = _import_settings(WISHLIST_URL=raw, WISHLIST_TOKEN="wl-test-token")
    assert (r.returncode, r.stdout.strip()) == (0, url), r.stderr


def test_without_a_url_the_token_is_not_required():
    r = _import_settings(WISHLIST_URL="", WISHLIST_TOKEN="")
    assert (r.returncode, r.stdout.strip()) == (0, "")


def test_the_start_up_error_never_contains_the_token():
    r = _import_settings(WISHLIST_URL="gopher://192.0.2.10", WISHLIST_TOKEN="wl-secret-in-error")
    assert r.returncode != 0 and "wl-secret-in-error" not in r.stderr + r.stdout
