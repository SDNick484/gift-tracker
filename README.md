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

`DEV_EMAIL` admits requests from loopback that carry no Cloudflare headers, as that user. It is for development only and is never set on the server. Settings are documented at the top of `settings.py`. Add `MODE=school` (and a separate `DATA_DIR`) to try the school instance described under Deployment.

The frontend is plain ES modules in `static/js/` served as they are: no build step, no npm. Open `http://127.0.0.1:8767/` (with `DEV_EMAIL` set, from the same machine) to use it; the screens are hash routes (`#/`, `#/idea`, `#/person/<id>`, `#/gift/<id>`, `#/board/<id>`, `#/lists/<pending|to-wrap|where>`, `#/places`, `#/settings`).

Tests and lint:

```sh
python -m pytest -q && ruff check
for f in static/js/*.js; do node --check "$f"; done
node --test tests/frontend.test.mjs
```

```sh
shellcheck -s sh installer/setup-head.sh installer/build.sh ops/tracker-backup
```

## After purchase

A gift from us moves `decided` to `ordered` to `arrived` to `wrapped`. Marking it ordered asks for the seller, a ship-to address and the expected date; an address other than Home shows a warning on the gift until you tick "Meant to ship there", so an order headed to the wrong house is caught while it can still be cancelled. Addresses are labels (Home, a relative's house), not street addresses, and exactly one is home. Settings links to the Places screen, where you keep the addresses and a tree of hiding places (Garage, Top shelf, Blue bin).

Three lists, linked from the home screen with their counts: Not here yet (ordered, by expected date, red when overdue), To wrap (arrived, by recipient, with the hiding place) and Where is it? (arrived or wrapped, by hiding place). While the app is locked, a gift for a protected person is in none of them. In a school instance there are no hiding places and no Where is it? list.

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

### A second instance: school

`MODE=school` turns an instance into the staff-gift tracker: staff with a role (homeroom, assistant, special subject, administrator, or anything you type), gifts with many recipients, a detail and a done tick for each recipient, and a staff-by-gift grid as the home screen. It is the same code with its own database, hostname and Access policy, so a staff list never sits beside the children's boards. In school mode there is no PIN or idle lock, no Santa, no protected people and no boards; the API answers 404 for those routes. It is not for collecting money or gifts from other families.

Install it beside the family instance, in the same container or another one. A named instance gets its own service, user, folders, config, log, backup job and port, all called `tracker-<name>`; an install with no name is exactly the one above.

```sh
cp deploy/deploy.env.example deploy/school.env   # git-ignored; vim deploy/school.env
# set MODE=school, ALLOWED_EMAILS, USERS, APP_NAME, and the CF_AUD of the school's own Access application
DEPLOY_ENV=deploy/school.env sh installer/build.sh
# copy dist/setup-tracker.sh into the container, then in it:
sh setup-tracker.sh --instance school             # or TRACKER_INSTANCE=school
```

The family build bakes the family's `CF_AUD` and emails in, so do not reuse its `dist/setup-tracker.sh` for the school without overriding them: `MODE=school ALLOWED_EMAILS=... CF_AUD=... TRACKER_PORT=8768 sh setup-tracker.sh --instance school` does it from the command line. The settings are written to `/etc/conf.d/tracker-school` on the first install only, and never overwritten. The default port for a named instance is 8768 (an existing install keeps its own); the installer refuses a port that is taken.

| | Family (no name) | `--instance school` |
| --- | --- | --- |
| Service | `tracker` | `tracker-school` |
| App, data, backups | `/opt/tracker`, `/var/lib/tracker`, `/var/backups/tracker` | the same with `tracker-school` |
| Config, log | `/etc/conf.d/tracker`, `/var/log/tracker.log` | `/etc/conf.d/tracker-school`, `/var/log/tracker-school.log` |
| Daily backup job | `/etc/periodic/daily/tracker-backup` | `/etc/periodic/daily/tracker-school-backup` |
| Port | 8767 | 8768 |

```sh
rc-service tracker-school restart
tail -n 40 /var/log/tracker-school.log
sh setup-tracker.sh --instance school --uninstall    # keeps its data, config and backups
```

Cloudflare, as for the family instance but on its own: a separate Access application for `school.example.com` whose one Allow policy lists only the people who plan the school gifts, its AUD tag in `CF_AUD` in `/etc/conf.d/tracker-school`, and a second public hostname on the existing tunnel pointing at `http://127.0.0.1:8768`. Do not add these people to the family application, and do not point both hostnames at one port.

Using it: add each staff member in Settings with a role; everyone active appears in the grid (set a person's plan to skip in an occasion to leave them out, or make them inactive when they leave). Start each occasion ("Christmas 2026", "End of year 2027") from the previous one to carry plans and budgets forward. Add a gift (a title and who it is for: everyone with a role, or one person), then tap grid cells to add or remove people. Tap a filled cell to set its detail ("$35 · coffee shop") and tick it done; a column header has "Add role" to add everyone with that role, and shows how many people the gift is for and how many are done. A gift always keeps one recipient: to drop the last one, delete the gift.

### Backups

`ops/tracker-backup` is installed as `/etc/periodic/daily/tracker-backup`. It uses `sqlite3 ... ".backup"` to write `/var/backups/tracker/tracker-<date>.db` with mode 0600 (directory 0700), checks integrity, keeps 14 days, and logs to `/var/log/tracker-backup.log`. The installer takes a first backup.

The backups contain every secret in the database: what each child is getting and what is from Santa. Keep them private. Do not copy them to a share the children or other family can read, and treat them like the live database.

To restore, `rc-service tracker stop`, copy a backup over `/var/lib/tracker/tracker.db` (owner `tracker`, mode 0600), remove any `tracker.db-wal` and `tracker.db-shm` beside it, then `rc-service tracker start`.
