# The wishlist feed: the contract between the two apps

The Gift Tracker reads wishes from the Family Wishlist and writes reservations back through a private, token-protected
feed on the wishlist. The wishlist side is SDNick484/family-wishlist issue #20; this page is what the tracker is built
against (`wishlist.py`), so the other side can be written, and checked, from it. The wishlist owns each wish and its
reserved and purchased state; the tracker owns everything private (givers, Santa, status, order fields, hiding places,
money) and sends none of it.

## Settings

| App | Setting | Value |
| --- | --- | --- |
| Wishlist | `FEED_TOKEN` | A long random secret. Unset or empty: every `/api/feed` route answers 404 like a missing route. |
| Wishlist | `FEED_PEOPLE` | Comma list of tab names the feed exposes (the children). Empty: none. |
| Tracker | `WISHLIST_URL` | The wishlist's base URL **on the LAN**, e.g. `http://192.0.2.10:8090` (http or https; a path prefix is kept). Unset: the link is off. |
| Tracker | `WISHLIST_TOKEN` | The same value as `FEED_TOKEN`. Required when `WISHLIST_URL` is set. |

The feed refuses any request that carries a Cloudflare header, so the tracker must reach the wishlist directly on the
LAN, never through the tunnel. Over `http://` the token crosses the LAN in clear text: keep both containers on the same
host or a trusted segment, or put TLS in front of the wishlist and use `https://`.

## Requests the tracker makes

Every request carries `Authorization: Bearer <WISHLIST_TOKEN>` and `Accept: application/json`; a POST also carries
`Content-Type: application/json`. Nothing else: no cookie, no `Origin`, no `X-Wishlist` header, no Cloudflare header, no
query string. Paths are fixed text plus an integer item id. The tracker follows no redirect, gives a request 5 seconds
in all, and reads at most 2 MiB of an answer.

### Read

`GET {WISHLIST_URL}/api/feed`

```json
{"people": [{"id": 3, "name": "Kid 1", "items": [
  {"id": 11, "name": "Lego set", "url": "https://example.com/lego", "store": "Toy Shop", "price": 49.99,
   "occasion": "Christmas", "secret": false, "reserved_by": null, "reserved_by_email": null,
   "purchased": false, "purchased_by": null, "purchased_by_email": null, "archived_at": null}]}]}
```

- One entry per tab in `FEED_PEOPLE`, with the items on it, secret ideas and items the owner removed after a reservation
  or purchase included, archived items left out.
- Types the tracker expects: `id` a positive integer (unique across the whole feed); `name`, `url`, `store`,
  `occasion`, `reserved_by`, `purchased_by` text or null; `*_email` text or null; `price` a number (dollars) or null;
  `secret` and `purchased` booleans; `archived_at` an integer or null.
- The tracker treats the answer as unusable ("wishlist unreachable") when it is not 200, not UTF-8 JSON, contains `NaN`
  or `Infinity`, is over 2 MiB, or does not have that shape (a person or item without an integer id, a duplicate item
  id, `people` or `items` not a list). A field of the wrong type inside an item is ignored (read as null). A `url`
  that is not `http://` or `https://` is dropped.

### Write

`POST {WISHLIST_URL}/api/feed/items/<id>/reserve` with `{"by": "<label>"}` reserves the item under that display name
(`reserved_by_email` stays NULL), or re-labels a reservation the feed made. `{"by": null}` releases it.

`POST {WISHLIST_URL}/api/feed/items/<id>/purchase` with `{"by": "<label>", "purchased": true}` marks it purchased
under that name and ends a feed reservation; `{"by": "<label>", "purchased": false}` undoes a purchase the feed made.

| Answer | Meaning to the tracker |
| --- | --- |
| any 2xx | Done. The body is not read. |
| 409 | Someone else holds it: a reservation or purchase by a signed-in shopper, or reserving a purchased item. The tracker changes nothing, answers 409 and refetches the feed. |
| 404 | The item is gone, or its tab is no longer in `FEED_PEOPLE`. The tracker changes nothing, answers 409 and refetches. |
| anything else, or no answer | Unreachable: the tracker changes nothing and answers 503. Nothing is queued or retried. |

## What a body may contain

Only the keys `by` and `purchased`. `by` is the giver household's label, built from the givers alone: a gift from
the owners is written under the owners' household label (their shared household, else `Us`) whether or not it is from
Santa, and whether one owner or both give it. Never a Santa flag, a status, a price, a store, an order field, a hiding
place, a note or any token but the feed's own. `tests/test_wishlist.py` checks every request the tracker makes against
this.

## When the tracker writes

- Assigning a wish to a giver on a board: `reserve` with the new label. Unassigning: `reserve` with `null`. A
  reassignment that keeps the label (Alex alone to both owners) sends nothing.
- A gift from someone else set to `confirmed` or `given`: `purchase` true. Set back to `told`: `purchase` false, then
  `reserve` with the label again (the purchase ended the reservation).
- Deleting a gift that holds a feed reservation: `reserve` with `null`.
- Never for Santa, for a gift's own status track (decided, ordered, arrived, wrapped), or for anything in school mode.

If the tracker's own write fails after the wishlist accepted a change, it sends the change back (the reservation as the
feed last showed it, or a release).

## Not covered by the wishlist issue, assumed here

- The body of a successful write is not specified; the tracker ignores it.
- Releasing an item that is not reserved: assumed to succeed (2xx) and change nothing.
- `purchase` false is assumed not to restore the reservation the purchase ended, so the tracker reserves again.
- A wish deleted on the wishlist, or archived, leaves the feed; the tracker keeps its gift as an ordinary gift (and
  does so only when the tab of one of the gift's recipients is still in the feed).
