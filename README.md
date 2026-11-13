# Gift Tracker

A private, self-hosted planner for household gift giving: who each gift is for, who it is from, where it stands, and where it is hidden. Sibling to [family-wishlist](https://github.com/SDNick484/family-wishlist).

Flask + SQLite with a plain HTML/JS frontend, run by gunicorn on an Alpine container behind a Cloudflare Tunnel. Only the two email addresses in its own Cloudflare Access application (and in `ALLOWED_EMAILS`) get in; there is no LAN access.

Status: the installer and backup exist (see Deployment); the app skeleton, schema, identity guard, API and phone-first frontend exist; the remaining features are being built from `docs/superpowers/plans/`. The design is in `docs/superpowers/specs/`. Work is queued as issues titled `Prompt:`.

## Development

```sh
python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
DEV_EMAIL=alex@example.com ALLOWED_EMAILS=alex@example.com,sam@example.com \
  USERS="alex@example.com=Alex,sam@example.com=Sam" .venv/bin/python app.py
```

`DEV_EMAIL` admits requests from loopback that carry no Cloudflare headers, as that user. It is for development only and is never set on the server. Settings are documented at the top of `settings.py`.

The frontend is plain ES modules in `static/js/` served as they are: no build step, no npm. Open `http://127.0.0.1:8767/` (with `DEV_EMAIL` set, from the same machine) to use it; the screens are hash routes (`#/`, `#/idea`, `#/person/<id>`, `#/gift/<id>`, `#/board/<id>`, `#/settings`).

Tests and lint:

```sh
python -m pytest -q && ruff check
for f in static/js/*.js; do node --check "$f"; done
node --test tests/frontend.test.mjs
```

```sh
shellcheck -s sh installer/setup-head.sh installer/build.sh ops/tracker-backup
```

## Deployment

The tracker runs in its own Alpine LXC, listens on `127.0.0.1:8767` only, and is reachable solely through a Cloudflare Tunnel. This matters: the people it hides things from are on the home network, so nothing may listen on the LAN.

Build and install:

```sh
cp deploy/deploy.env.example deploy/deploy.env   # git-ignored; vim deploy/deploy.env
sh installer/build.sh                            # writes dist/setup-tracker.sh
# copy dist/setup-tracker.sh into the container, then in it:
sh setup-tracker.sh
```

`deploy/deploy.env` holds household defaults (`ALLOWED_EMAILS`, `USERS`, `CF_TEAM_DOMAIN`, `CF_AUD`, `APP_NAME`); they are used on the first install only. The installer is safe to re-run: it installs `py3-flask py3-gunicorn py3-jwt py3-cryptography` and `sqlite` with `apk`, replaces `/opt/tracker`, and keeps `/var/lib/tracker` and `/etc/conf.d/tracker`. It adds settings that are missing from the config and never overwrites existing ones, and it never writes `DEV_EMAIL`. Edit settings with `vim /etc/conf.d/tracker`, then:

```sh
rc-service tracker restart
tail -n 40 /var/log/tracker.log
```

Gunicorn runs as the unprivileged `tracker` user under OpenRC (`rc-service tracker status`). `sh setup-tracker.sh --uninstall` removes the app and service and keeps data, config and backups.

### Cloudflare Access

1. In Zero Trust, create a separate Access application for `tracker.example.com`. Do not reuse the wishlist's application.
2. Give it one Allow policy whose Include rule is only the two owners' emails (Emails selector), and nothing else: no email domain, no "everyone".
3. Set a short session duration on the application (for example 1 hour to 24 hours; shorter means more sign-ins). The in-app idle lock is separate and covers the protected boards.
4. Copy the application's AUD tag into `CF_AUD` and your team domain into `CF_TEAM_DOMAIN` in `/etc/conf.d/tracker`, and put the same two emails in `ALLOWED_EMAILS`. The app verifies the Access JWT itself; Access alone is not trusted.

### Tunnel

Run `cloudflared` in the same container, so the origin is `http://127.0.0.1:8767` and nothing has to listen on a LAN address. Point the public hostname `tracker.example.com` at `http://127.0.0.1:8767`. Do not run the tunnel in another container or on another host: that would need the app to listen beyond loopback, which the installer never does.

Check from another machine on the LAN that `http://<container-ip>:8767` is refused at the socket, and that a third email is refused by Access.

### Backups

`ops/tracker-backup` is installed as `/etc/periodic/daily/tracker-backup`. It uses `sqlite3 ... ".backup"` to write `/var/backups/tracker/tracker-<date>.db` with mode 0600 (directory 0700), checks integrity, keeps 14 days, and logs to `/var/log/tracker-backup.log`. The installer takes a first backup.

The backups contain every secret in the database: what each child is getting and what is from Santa. Keep them private. Do not copy them to a share the children or other family can read, and treat them like the live database.

To restore, `rc-service tracker stop`, copy a backup over `/var/lib/tracker/tracker.db` (owner `tracker`, mode 0600), remove any `tracker.db-wal` and `tracker.db-shm` beside it, then `rc-service tracker start`.
