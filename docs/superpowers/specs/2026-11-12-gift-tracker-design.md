# Gift Tracker: Design

Approved 2026-11-12. Names in this document are generic: the two owners are Alex and Sam, their children are Robin, Skyler and Taylor, and the two grandparent households are "Pat & Lee" and "Morgan & Drew".

## Intent and why

The Gift Tracker is the two owners' private record of every gift they give and every gift their children receive: who it is for, who it is from, where it stands, and where it is hidden. It replaces a yearly spreadsheet and sits beside the Family Wishlist (`SDNick484/family-wishlist`), which stays the outward-facing app for relatives.

The main use: tracking who is getting what for the children, so two sets of grandparents don't buy the same thing. Second: a place to hold ideas for other people before buying. Recording is insurance and memory.

What the spreadsheets showed:

- Status lived in cell position (one column meant decided, the next meant maybe), and "Done" overwrote links.
- The giver was almost never recorded, and Santa appeared nowhere.
- A name suffix marked a gift from only one owner, which doubled every row; a joint gift to a couple had no way to be one row.
- Each year was a copy of the last, typos included. People dropped off silently.
- The most recent year was sparse: gifts were bought but not written down.
- Children's gifts were typed twice, once as ideas and again as purchases.

The lesson that shapes the design: entry has to take seconds, or be done by an agent as a side effect of buying, or the record lapses.

## Goals, non-goals, anti-goals

**Goals**

1. No double-ups on the children's gifts. Every wish is unassigned or assigned to exactly one giver, and the wishlist agrees.
2. An idea for anyone can be captured in a few seconds from a phone.
3. From purchase to unwrapping, each gift shows seller, ship-to address, expected delivery, hiding place and wrapped state.
4. A wrong ship-to address is caught while the order can still be cancelled.
5. Santa is a label that can be set or changed on any gift up to Christmas Eve.
6. Each year starts from last year's people. Past years stay as history per person.
7. An agent can read what to buy and write back what was ordered.
8. Works for birthdays and graduations with no extra machinery.

**Non-goals**

- Coordinating a gift exchange for the wider family (pairings, everyone's lists).
- School and teacher gifts mixed into the family instance. They get their own instance.
- Spending analysis beyond a budget per person and a side-by-side for the children.
- Gifts between the two owners. They stay off-list.

**Anti-goals: it has failed if any of these happen**

- A child, or any relative, sees a giver, a Santa label or a gift's status. This holds even after the children start signing into the wishlist.
- A Santa label reaches the wishlist, a notification, a page title or a home-screen name.
- Recording a gift takes long enough that it gets skipped.
- The tracker and the wishlist disagree about who has an item.
- A session left open on a shared device shows the children's section.

## Constraints

- Flask and SQLite, plain HTML/CSS/JS with no build step, gunicorn under OpenRC on an Alpine LXC, Cloudflare Tunnel and Access in front, installable on a phone. Same as the wishlist.
- Split into small modules (`app.py`, `db.py`, feature modules), not one large `app.py`, so the privacy checks are easy to review.
- Any change to the wishlist follows that repo's `CLAUDE.md`: spoiler protection with a test in `TestSpoilersMore`, identity only from a verified Cloudflare JWT, CSRF header, `json_body()` and `text()`, schema migration in `init_db()`, Alpine `py3-*` dependencies only.
- No personal data in either repo. Generic names and example.com emails. The history import runs against the live database and its data never enters git.
- Two users with equal rights, on phone and desktop, at home and in stores.

## Approaches

Option 2 was picked: a standalone tracker with a live link to the wishlist.

| | 1. Extend the wishlist | 2. Standalone, live link (picked) | 3. Standalone, no link |
| --- | --- | --- | --- |
| Where it runs | Private section of the wishlist app and database | Own app, database, container and Access policy | Own app, database, container and Access policy |
| Children's items | One copy, no sync | Wishlist owns wishes and reserved/purchased; tracker stores only private fields | Copied in by import |
| Privacy | Depends on every wishlist endpoint adding one more check; the site must admit the whole family | Enforced by Cloudflare before the app runs; a bug in one app cannot expose the other | Same as option 2 |
| Double-up protection | Strong | Strong: assignments reserve the item on the wishlist | Weak: the two apps drift |
| Cost | Least code, highest risk | Two deploys, one small wishlist change, an outage case to handle | Simplest, no wishlist change |

Option 2 is the only one where the privacy wall is structural and both apps agree on who has what. It degrades well: the tracker works with manual entry if the link is not built or the wishlist is down.

## Data model

A gift has one or more recipients and one or more givers, belongs to one occasion, and carries everything else as plain fields.

| Table | Holds | Notes |
| --- | --- | --- |
| `people` | Everyone who gives or receives: name, group, household, color, optional wishlist tab id | The owners and their children are people too. A household ("Pat & Lee") is a label shared by its members, offered as one choice when assigning. |
| `occasions` | Name, kind (christmas, birthday, graduation, other), date | "Christmas 2026", "Birthday 2027". This is the year dimension. |
| `occasion_people` | Per occasion and person: plan (individual, joint, exchange only, skip) and optional budget | Who you buy for is a per-year decision. A new Christmas copies last year's rows. |
| `gifts` | Title, occasion, status, Santa flag, link, store, price, notes, wishlist item id, order fields, location, exchange id | One row per gift, however many people give or receive it. |
| `gift_recipients`, `gift_givers` | Gift to person links | Replaces name suffixes and "All". |
| `locations` | `parent_id`, `name`, `sort_order` | A self-referencing tree: Room → Closet → Shelf, any depth, no layer required. |
| `addresses` | Label and an `is_home` flag | Home, each grandparent household, other. Used for the ship-to check. |
| `exchanges`, `exchange_draws` | Name, budget, date; which household member drew whom | Only this household's side. Never the full pairing table. |
| `api_tokens` | Name, scope, hash, last used, revoked | For agents. |
| `wishlist_cache` | Last feed from the wishlist with its fetch time | Shown when the wishlist is unreachable. |

**Status.** A gift from an owner moves through `idea → decided → ordered → arrived → wrapped → given`. Something bought in a store goes from decided straight to arrived. A gift from someone else uses a short track: `told → confirmed → given`, where told means an owner suggested it and confirmed means the giver said they bought it.

**Santa.** A boolean on the gift, allowed only when the givers are the owners. It defaults to off and can be flipped at any time.

**Order fields.** Seller, order number, ship-to address, expected delivery date, arrived date, and who placed it (an owner or an agent token).

**Sample rows**

| Gift | Occasion | To | From | Santa | Status |
| --- | --- | --- | --- | --- | --- |
| Retro console + controller | Christmas 2025 | Jordan, Riley (a couple) | Alex, Sam | no | given |
| Kids eReader | Christmas 2025 | Robin, Skyler, Taylor | Pat, Lee | no | given |
| Digital camera | Christmas 2025 | Robin, Skyler, Taylor | Alex, Sam | yes | given |
| Luggage | Christmas 2024 | Pat | Sam | no | given |
| Shoes? | Christmas 2025 | Pat | Alex | no | idea |
| Knife | Christmas 2025, family exchange | Casey | Alex (drew Casey) | no | given |

## Screens

Phone-first with a wider desktop layout. Each person's row carries their color.

**1. Home: one occasion at a glance**

```
Christmas 2026 ▾                      43 days
─────────────────────────────────────────────
[+ Idea]   Not here yet 3 · To wrap 9 · Where is it?

THE KIDS  (locked · tap to unlock)

SIDE A
  Pat            Shoes? (idea)             $0 / $75
  Lee            nothing yet               $0 / $75
  Jordan + Riley joint · nothing yet

SIDE B
  Morgan, Drew   exchange only

EXCHANGES
  Family adults  $50 · Dec 20     Alex → Casey: idea
```

**2. A child's board: the main screen**

```
Robin · Christmas 2026               7 gifts · 2 unassigned
────────────────────────────────────────────────────────
UNASSIGNED  (from wishlist)
  Logic puzzle book                  [Assign ▾] [Copy link]
  Chess set                          [Assign ▾]

PAT & LEE                   2 gifts      [Copy their list]
  Kids eReader (all three)           confirmed
  Hardcover novel                    told 11/20

MORGAN & DREW               1 gift       [Copy their list]
  Tracking bracelet                  told 11/20

US                          2 gifts · $63 of $150
  Belt bag                           wrapped      Santa ☐
  Curling iron                       arrived      Santa ☑

Split: 1 Santa · 1 Mom & Dad · 3 grandparents
All three:  Robin $63 · Skyler $58 · Taylor $61
```

Assign picks from a fixed list of givers, so an item cannot go to two. "Copy their list" puts names and links on the clipboard, ready to paste into a text. An item a relative reserved themselves on the wishlist appears under their name without anyone typing it.

**3. Quick idea**

```
+ Idea
  For    [Pat ▾]          (last used first)
  What   [shoes?                         ]
  From   Alex · Sam · (Both)
  [Save]            link, price, store: optional, later
```

**4. Gift detail**

```
Digital camera              to: all three · from: Us   Santa ☐
───────────────────────────────────────────────────────────
idea → decided → ordered → ARRIVED → wrapped → given
Bought   Camera shop · $89 · order #1234        by Alex
Ship to  Home ✓                 ETA Dec 9 · arrived Dec 8
Hidden   Garage › Top shelf › Blue bin
[Mark wrapped]
```

Marking a gift ordered asks for seller, ship-to and expected date. Ship-to is a required pick, and any address other than Home shows a warning unless the gift is marked as meant to ship there.

**5. Three working lists**

- **Not here yet:** ordered gifts sorted by expected date; a row turns red when its date passes.
- **To wrap:** arrived and not wrapped, grouped by recipient, with the hiding place on each row.
- **Where is it?:** everything in the house grouped by the location tree.

A person's page lists their gifts across every occasion, newest first.

## Access, the wishlist link and agents

The tracker and the wishlist are separate sites with separate sign-in rules, joined by one narrow private channel. Relatives only ever reach the wishlist. The tracker reads wishes from it and writes reservations back; nothing private flows the other way.

**Who can reach the tracker**

- Its own Cloudflare Access application whose policy lists the two owners' emails. The app also verifies the Access JWT and checks the email against its own allowlist.
- **No LAN role.** The wishlist treats LAN requests as an editor by default. The tracker never does, because the children are on the home network. It listens only for the tunnel.
- **Timeout.** A short Access session, plus an in-app idle lock: after a few idle minutes the children's boards and anything showing a Santa label need a PIN to reopen. Adults' gift lists stay visible within the session.
- **Nothing identifying outside the app.** Neutral subdomain, home-screen name and icon. Page titles never contain a gift or a name. No notifications.
- Backups of the tracker database hold every secret.

**The wishlist link**

- **The wishlist owns** each wish and its reserved and purchased state. **The tracker owns** giver identity, Santa, status past purchased, order fields, location and budget, keyed by wishlist item id.
- **Read.** The wishlist gains one endpoint returning the items on configured tabs with reservation and purchase state, including secret ideas. It requires a bearer token, refuses any request carrying Cloudflare headers, and is off when no token is configured. It gets a `TestSpoilersMore` test proving a tab owner and a tokenless caller learn nothing.
- **Write.** Assigning a giver in the tracker reserves the item on the wishlist with the giver's display name in the existing `by` field. Unassigning releases it. "Confirmed" marks it purchased.
- **Santa never crosses.** A gift from the owners is written to the wishlist as reserved by the owners' household label whether or not it is from Santa.
- **Relatives who use the wishlist directly** appear in the tracker under the matching person, matched by wishlist member email. An unmatched free-text name is mapped to a person once and remembered.
- **Off-list gifts** exist only in the tracker.
- **Wishlist unreachable:** the tracker shows the cached feed with its age and disables Assign. It does not queue writes.
- If a wish is deleted on the wishlist after it was assigned, the tracker keeps the gift as an off-list row.

**Agents**

A small REST API under `/api/v1`, authenticated by tokens created and revoked in Settings, with an MCP server over the same API (a must-have: it is how the owner's agent connects). Each token has one scope:

| Scope | Can do | Intended for |
| --- | --- | --- |
| `delivery` | List gifts in decided or ordered status (title, recipient, store); set seller, order number, ship-to, expected date, arrived | A shipping channel reading order emails |
| `shop` | The above, plus read ideas and budgets for adults and mark a gift ordered | An agent doing the buying |
| `full` | Everything the web app can do | The owner's own tools |

No scope below `full` can read Santa flags or the children's boards. Every write by a token is recorded on the gift with the token's name.

## School instance

Teacher and staff gifts run on a second instance of the same code, with its own database, subdomain and Access policy. It is used before Christmas (gifts go out when school lets out in mid-December) and again at the end of the school year.

**What it reuses.** People (staff, each with a role: homeroom, assistant, special subject, administrator), occasions ("Christmas 2026", "End of year 2027", "Teacher Appreciation"), gifts with many recipients, budgets, and per-person history.

**What it adds**

- A school mode setting that hides the children's boards, Santa, the wishlist link and hiding places.
- Two fields on each recipient of a group gift: a detail ("$35 · coffee shop") and a done checkbox.
- A matrix view, staff down the side and gifts across:

```
Christmas 2025     Card $35  Card $25  Apprec.  Granola  Cookies
Ms. Avery             ✓                  ✓
Ms. Blake                       ✓        ✓
Ms. Chen                        ✓        ✓        ✓
Mr. Diaz                        ✓
Ms. Ellis                                         ✓        ✓
… 22 staff            1         3        7        9        8
```

- Tap a cell to add or remove that person. A column header offers "add everyone with role …".
- Each column shows made and delivered counts.
- A new occasion starts from the previous one's staff list.

**Not included.** Collecting from other families.

## Features, sorted

**Must-have, in build order.** Each stage is usable without the next.

1. **Core, manual entry.** People with groups and households; an occasion created from the previous one with a plan per person; quick idea capture; gifts with several recipients and givers; the full status track; Santa toggle; budget per person and the children's side-by-side total.
2. **Privacy.** Access policy for two emails, no LAN role, idle lock on the children's section, neutral name and icon. Ships with stage 1.
3. **The children's boards.** Assign to a fixed giver, told and confirmed, copy a giver's list as text. Works with items typed in by hand until stage 6.
4. **School instance.** School mode, per-recipient detail and done, the matrix view.
5. **After purchase.** Order fields with the ship-to check, the location tree, and the three lists.
6. **Wishlist link.** The private feed on the wishlist and reserve/release/purchase write-back.
7. **Agent API and MCP server.** Scoped tokens, then an MCP server over the same API.
8. **Exchanges.** Budget, date and this household's draws.
9. **History import.** Two past years from the spreadsheets.

**Later**

- Order details filled from confirmation emails by an agent, using the MCP server.
- Thank-you tracking: a "thanked" checkbox on gifts the children received.
- "Only me" gifts between the owners. This hides rows in the app, not from whoever administers the database.
- Link autofill for name, price and photo, reusing the wishlist's preview code.
- Reading exchange draws from a coordinator app, if one is built.

**Not doing**

- Exchange coordination for the wider family.
- Collecting money or gifts from other families.
- Accounts for the children or relatives.
- Category budgets, spending reports, receipts or returns.
- Push or email notifications.
- Storing tracker data in the wishlist database.

## Left to the builder

- Exact schema, indexes and migrations.
- How the tracker authenticates its write-back to the wishlist. The existing reserve and purchase routes expect a browser Origin and the LAN role; the same bearer token as the feed is the likely answer.
- Visual design within the family colors and the look of the wishlist.
- How the spreadsheet import maps free-text cells to gifts, with a dry run the owner reviews before it writes.
- Installer, backups and the Access application, following the wishlist's `deploy/` scripts.
