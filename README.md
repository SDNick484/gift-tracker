# Gift Tracker

A private, self-hosted planner for household gift giving: who each gift is for, who it is from, where it stands, and where it is hidden. Sibling to [family-wishlist](https://github.com/SDNick484/family-wishlist).

Flask + SQLite with a plain HTML/JS frontend, run by gunicorn on an Alpine container behind a Cloudflare Tunnel. Only the two email addresses in its own Cloudflare Access application (and in `ALLOWED_EMAILS`) get in; there is no LAN access.

Status: the app skeleton, schema and identity guard exist; the features are being built from `docs/superpowers/plans/`. The design is in `docs/superpowers/specs/`. Work is queued as issues titled `Prompt:`.

## Development

```sh
python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
DEV_EMAIL=alex@example.com ALLOWED_EMAILS=alex@example.com,sam@example.com \
  USERS="alex@example.com=Alex,sam@example.com=Sam" .venv/bin/python app.py
```

`DEV_EMAIL` admits requests from loopback that carry no Cloudflare headers, as that user. It is for development only and is never set on the server. Settings are documented at the top of `settings.py`.

Tests and lint:

```sh
python -m pytest -q && ruff check
```
