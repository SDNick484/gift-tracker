# Agent API and MCP server

The tracker has a small REST API under `/api/v1` for agents, authenticated by API tokens, and an MCP server over that
API. An agent that already reads order confirmations can read what to buy and write back what was ordered, so the
record keeps itself. No token below `full` can ever see a protected person's gifts, a Santa flag or a board.

```
Claude app -> Anthropic cloud -> https://mcp.example.com/tracker/mcp
  -> Cloudflare Access (sign-in, Managed OAuth) -> tunnel route ^/tracker/ -> http://127.0.0.1:8779  (tracker-mcp)
  -> checks the Access JWT again               -> http://127.0.0.1:8767/api/v1/...  (the tracker, Bearer <token>)
```

The MCP service never opens the database. It holds one token, and that token's scope decides what it can do.

---

## 1. Tokens

Make tokens in the app: **Settings > Agent tokens** (`#/tokens`). Making, listing and revoking tokens needs the unlock
(the PIN), because a `full` token is as strong as the PIN. A new token is shown once; the tracker stores only its
SHA-256 hash, so a lost token cannot be shown again: revoke it and make another. A revoked token keeps its name, so
the notes it left on gifts still say who wrote them.

| Scope | Reads | Lists by default | May set status | Writes | Intended for |
| --- | --- | --- | --- | --- | --- |
| `delivery` | gifts that are decided, ordered or arrived | decided, ordered | arrived | order fields | a shipping channel reading order emails |
| `shop` | also ideas; people; budgets | idea, decided, ordered | ordered, arrived | order fields | an agent doing the buying |
| `full` | every gift, any status, both tracks | everything | any | order fields, new gifts | the owners' own tools |

- **Every token is locked.** Like a browser before the PIN: a gift with any protected recipient does not exist for it
  (404, the same as a missing gift, for reads and writes alike), protected people are left out of people and budgets,
  and no answer has a `santa` key or a hiding place.
- **`full` with "may see protected gifts".** Only a `full` token made with that box ticked sees protected gifts and
  Santa, as an unlocked owner does. Make one only for a tool you would hand the PIN to. There is no such option in
  school mode (it has no lock).
- **`full` is the agent API, not the whole web app.** It can read every gift, set any status, record orders and add
  gifts. People, occasions, boards, assignment and hiding places stay in the web app.
- **Every write leaves a note** on the gift with the token's name (shown under "Agent notes" on the gift's order
  card), and marking a gift ordered sets "Ordered by" to the token's name.
- Marking a gift ordered needs a seller, a ship-to address and an expected date, exactly as in the app.

## 2. The REST API (`/api/v1`)

Every request sends `Authorization: Bearer gt_...`. Nothing else is accepted on `/api/v1`: no Cloudflare identity,
no cookie. Writes send a JSON body (`Content-Type: application/json`) and need no other header. A request with no
credentials at all is `403 {"error": "Forbidden"}`; any credential that is not a live token (a browser's Cloudflare
identity, a mistyped or revoked token) is `401 {"error": "Unauthorized"}` with `WWW-Authenticate: Bearer`. An
operation outside the token's scope is `403 {"error": "This token's scope does not allow that.", "scope": "delivery"}`.
A token is never accepted anywhere outside `/api/v1`.

| Method and path | Scopes | Does |
| --- | --- | --- |
| `GET /api/v1/me` | all | `{name, scope, unlocked, mode}` |
| `GET /api/v1/occasions` | all | occasions, `current` marks the default |
| `GET /api/v1/addresses` | all | ship-to labels; exactly one `is_home` |
| `GET /api/v1/gifts?occasion_id=&status=a,b&person_id=` | all | gifts of one occasion (default: current) |
| `GET /api/v1/gifts/<id>` | all | one gift |
| `PATCH /api/v1/gifts/<id>/order` | all | `seller, order_number, ship_to_id, ship_elsewhere_ok, eta, arrived_on` only |
| `POST /api/v1/gifts/<id>/status` | all | `{status, ...order fields}`, statuses by scope |
| `GET /api/v1/people` | shop, full | active people, owners marked |
| `GET /api/v1/occasions/<id>/budgets` | shop, full | plan, budget, spent and planned cents per person |
| `POST /api/v1/gifts` | full | a new gift (the web app's body; `occasion_id` defaults to the current one) |

Example: a delivery agent records an order and then the delivery.

```sh
T=gt_...   # the token
curl -s https://tracker.example.com/api/v1/gifts -H "Authorization: Bearer $T"
curl -s -X PATCH https://tracker.example.com/api/v1/gifts/12/order -H "Authorization: Bearer $T" \
  -H 'content-type: application/json' -d '{"seller": "Camera shop", "order_number": "1234", "ship_to_id": 1, "eta": "2026-12-09"}'
curl -s -X POST https://tracker.example.com/api/v1/gifts/12/status -H "Authorization: Bearer $T" \
  -H 'content-type: application/json' -d '{"status": "arrived"}'
```

### 2.1 Reaching `/api/v1` through Cloudflare

The tracker's hostname sits behind its own Access application, which lets in the two owners only. An agent outside
the container needs its own way through, for the path `/api/v1` only:

- **Preferred: a service token.** In Zero Trust, create a service token, then in the tracker's Access application add
  a policy with the **Service Auth** action whose Include is that service token, scoped to the path `/api/v1` (a
  separate Access application for `tracker.example.com/api/v1` with that one policy does the same). The agent then
  sends `CF-Access-Client-Id` and `CF-Access-Client-Secret` as well as `Authorization: Bearer`. Access stops anyone
  without the service token at the edge, and the tracker still checks the bearer token.
- **Or Bypass** for `tracker.example.com/api/v1`. The bearer token is then the only gate. Use it only if the agent
  cannot send the Access headers.

Do not widen the path: everything outside `/api/v1` must keep the owners-only policy. The MCP service below reaches
the tracker on loopback and needs neither.

## 3. The MCP service (`tracker-mcp`)

The installer adds a second service per install, `tracker-mcp` (`tracker-<name>-mcp` for a named instance): its own
user (which cannot read the database), a pip venv in `/opt/tracker-mcp/venv` (the `mcp` package is not an Alpine
package; the Flask app stays apk-only), and its own config `/etc/conf.d/tracker-mcp` (root, 0600: it holds the
token). If pip cannot reach PyPI the installer warns, leaves the MCP service off, and installs the app as usual.

### 3.1 Settings (`/etc/conf.d/tracker-mcp`)

The installer adds any missing key and never overwrites one you edited.

| Setting | Default | Meaning |
| --- | --- | --- |
| `TRACKER_API_TOKEN` | *(required)* | A token from Settings > Agent tokens. Its scope decides the tools (below). |
| `TRACKER_URL` | `http://127.0.0.1:8767` | The tracker on loopback. |
| `MCP_HTTP_BIND` | `127.0.0.1` | Where the service listens. cloudflared runs in this container, so loopback is enough. |
| `MCP_HTTP_PORT` | `8779` (`8780` for a named instance) | Port. |
| `MCP_HTTP_PATH` | `/tracker/mcp` | Endpoint path. The tunnel does not strip the prefix. |
| `MCP_PUBLIC_HOSTS` | *(required)* | `mcp.example.com`. Without it requests through the tunnel get HTTP 421. |
| `CF_ACCESS_TEAM_DOMAIN` | the tracker's `CF_TEAM_DOMAIN` | Access team domain. |
| `CF_ACCESS_AUD` | *(required)* | AUD tag of the Access application for `mcp.example.com`. Not the tracker's `CF_AUD`. |
| `MCP_ALLOWED_EMAILS` | the tracker's `ALLOWED_EMAILS` | Only these Access emails may call tools; empty means anyone Access lets in. |

The service refuses to start (one line in `/var/log/tracker-mcp.log`) without a token, with a token the tracker
rejects, or on a non-loopback address without the Access settings. It asks the tracker which scope its token has at
start, so after changing the token: `rc-service tracker-mcp restart`.

```sh
vim /etc/conf.d/tracker-mcp      # TRACKER_API_TOKEN, MCP_PUBLIC_HOSTS, CF_ACCESS_AUD
rc-service tracker-mcp start && rc-update add tracker-mcp default
tail -n 20 /var/log/tracker-mcp.log
```

### 3.2 Tunnel and Cloudflare

In the tunnel's published application routes add:

| Field | Value |
| --- | --- |
| Hostname | `mcp.example.com` |
| Path | `^/tracker/` |
| Service | `http://127.0.0.1:8779` |
| HTTP Host header override | unset |

Use an Access application for `mcp.example.com` with Managed OAuth on (the one other MCP servers on that hostname
already use is fine), an Allow policy for the owners, and copy its AUD tag into `CF_ACCESS_AUD`. Check that no Bypass
policy covers a path under `/tracker`.

### 3.3 Tools

| Tool | Scopes | Calls |
| --- | --- | --- |
| `whoami` | all | `GET /api/v1/me` |
| `list_occasions` | all | `GET /api/v1/occasions` |
| `list_addresses` | all | `GET /api/v1/addresses` |
| `list_gifts` | all | `GET /api/v1/gifts` |
| `get_gift` | all | `GET /api/v1/gifts/<id>` |
| `update_order` | all | `PATCH /api/v1/gifts/<id>/order` |
| `set_status` | all | `POST /api/v1/gifts/<id>/status` (which statuses: by scope) |
| `list_people` | shop, full | `GET /api/v1/people` |
| `get_budgets` | shop, full | `GET /api/v1/occasions/<id>/budgets` |
| `add_gift` | full | `POST /api/v1/gifts` |

A tool the scope cannot use is not listed at all. Errors reach the model as one sentence and never contain the token.

### 3.4 Connect an agent

In Claude, **Customize > Connectors > Add custom connector**, enter `https://mcp.example.com/tracker/mcp`, choose
**Sign in now** and sign in through Cloudflare Access. For Claude Code:
`claude mcp add --transport http gift-tracker https://mcp.example.com/tracker/mcp`.

Locally over stdio (a laptop that can reach the tracker), with the MCP packages installed
(`python3 -m venv .venv-mcp && .venv-mcp/bin/pip install -r requirements-mcp.txt`):

```json
{
  "mcpServers": {
    "gift-tracker": {
      "command": "/path/to/gift-tracker/.venv-mcp/bin/python",
      "args": ["/path/to/gift-tracker/mcp_server.py"],
      "env": {"TRACKER_URL": "http://127.0.0.1:8767", "TRACKER_API_TOKEN": "gt_..."}
    }
  }
}
```

Check that the endpoint refuses anonymous calls:

```sh
curl -si -X POST https://mcp.example.com/tracker/mcp -H 'content-type: application/json' \
  -H 'accept: application/json, text/event-stream' -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}' | head -5
# expect a 401 from Access, never a 200
```

### 3.5 Rollback

| Goal | Action |
| --- | --- |
| Stop the MCP service | `rc-service tracker-mcp stop && rc-update del tracker-mcp default`. The app is unaffected. |
| Cut one agent off | Revoke its token in Settings > Agent tokens. It gets 401 at once. |
| A token leaked | Revoke it, make a new one, put it in `/etc/conf.d/tracker-mcp`, `rc-service tracker-mcp restart`. |
| Remove public exposure | Delete the `^/tracker/` tunnel route and the connector, and any `/api/v1` Service Auth or Bypass policy. |

## 4. Development

`.venv-mcp/` (git-ignored) holds the MCP packages for local work and for `tests/test_mcp_*.py`; the app's own venv
does not get them. Without them the MCP server tests that need the package skip; CI installs `requirements-mcp.txt`
and fails if any of them is skipped.

```sh
python3 -m venv .venv-mcp && .venv-mcp/bin/pip install -r requirements-mcp.txt -r requirements-dev.txt httpx
.venv-mcp/bin/python -m pytest -q tests/test_mcp_server.py tests/test_mcp_http.py
```
