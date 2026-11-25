# Ideas and typed givers: design

Drafted 2026-11-24. Names are generic: the owners are Alex and Sam, their children Robin, Skyler and Taylor, the
grandparent households "Pat & Lee" and "Morgan & Drew", and a one-off relative "Uncle Casey".

This extends `2026-11-12-gift-tracker-design.md`; it does not edit it. Where the two disagree, this document is newer.

## Why

Two frictions, both felt while answering relatives who ask what the kids want.

1. **The same idea, typed three times.** An animal tracking bracelet that all three children would like has to be
   entered as three gifts today. A gift with three recipients already exists, but it means one shared present with
   the price split, not "each of these would like one".
2. **Givers who are not people.** Uncle Casey asked for ideas for the girls. Recording what he picked means adding him
   as a person, with a group and a household, for one gift. Most givers outside the grandparents are one-offs like
   him.

Behind both is one job: an owner copies a list of what each child still needs, pastes it into a text to a relative,
and later records what that relative took, so the item drops off the list for that child and not for the others.

## What success looks like

- The bracelet is entered once and tagged Robin, Skyler, Taylor.
- The owner copies "open, shareable ideas for the girls" (or one girl, or everyone) as plain text with what each item
  is and where to buy it, and pastes it into a message. Nothing in that text says who gives what, Santa, status or
  price.
- Uncle Casey says he will get the bracelet for Skyler. The owner records that, typing "Uncle Casey" as the giver
  without creating a person. Skyler's bracelet leaves her list; Robin's and Taylor's stay.
- An idea nobody took at Christmas is still on the list at the next birthday.

## Goals

- Ideas that stand apart from occasions, tagged with one or more people, each tagged person's copy open or taken on
  its own.
- A share flag per idea; only shared ideas go on the copied list.
- A copyable list, per person or for everyone, of open shared ideas.
- Typed giver names on a gift, remembered as suggestions.

## Non-goals

- A link a relative can open. The list is pasted text; the Cloudflare Access application and the identity rules do
  not change.
- Relatives entering anything themselves.
- Showing every open Family Wishlist wish on the shared list. Only wishes the owner has pulled over and flagged are
  shared.
- A screen to manage typed names (rename, merge, delete).
- Ideas or typed givers in the agent API (`/api/v1`) or the MCP tools. See "Left open".

## Anti-goals (a failure even if it works)

- Anything a parent plans to give showing up on a copied list because the flag defaulted on. **The share flag starts
  off.**
- A copied list, or anything a locked caller can read, revealing a giver, Santa, a status, a price, or that a
  protected child's item exists while locked.
- Marking an idea taken for one child taking it for the others.
- A typed giver becoming a person behind the owner's back.

## Constraints

Every rule in `CLAUDE.md` keeps holding: Alpine `py3-*` dependencies only, no frontend build step, `esc()` on all
typed text, no inline scripts or styles, schema changes through `migrate()` with a bumped `SCHEMA_VERSION`, money in
integer cents, Conventional Commits, and above all the privacy rule under the idle lock.

## The design

### Ideas

An **idea** has: a title, an optional link, store, price and notes, a **share** flag (default off), who made it, and
one or more **tagged people**. It has no occasion and no giver.

Each tagged person has their own **copy** of the idea, which is **open** or **taken**. A copy is taken when a gift
was made from it for that person; it points at that gift. Deleting that gift reopens the copy.

- Ideas can be for anyone (children and adults), not only protected people. The owners are not offered as people to
  tag, as on today's quick idea screen.
- An idea with any protected person tagged is **protected**, as a gift is. While locked it does not exist for any
  response: not listed, not counted, not in suggestions, 404 by id, and a write that would tag a protected person, or
  edit or delete a protected idea, is 423. Taking a protected idea needs the unlock.
- Untagging a person whose copy is taken leaves the gift alone and only drops the copy. An idea with no tagged people
  left is deleted. *(Builder's call is fine on whether untagging a taken copy is allowed at all.)*

### Taking an idea

Taking an idea for one tagged person, with a giver, creates a gift:

- in the current occasion (`occasions.current()`), with a picker to choose another unarchived occasion;
- with that one person as the recipient;
- with the idea's title, link, store, price and notes copied in;
- with the chosen giver: an owner, both owners, a household, a person, or a typed name (below);
- through `gifts.create`, so tracks, Santa and status follow the usual rules (from us: `idea`; from others: `told`).

The idea itself does not change; that person's copy points at the new gift. Copied fields are copied: editing the
gift later does not change the idea, and the other way round.

Taking happens from the idea's own screen ("Take for Skyler…") and from the person's board and person screen, which
list that person's open ideas.

**When a taken copy reopens.** *(My calls, all following "handed back reopens".)*

- The gift is deleted.
- On a gift taken from an idea, **Unassign** becomes **Back to ideas**: the gift is deleted (through the wishlist
  writer, so a reservation is released) and the copy reopens. A gift from an idea never sits unassigned on a board.
  Clearing every giver on the gift screen is refused for such a gift with the same pointer to Back to ideas.
- The person is removed from the gift's recipients: the link is dropped and the copy reopens. Adding recipients keeps
  it.
- These are maintained inside the writers' transactions (`gifts.update`, the assign and delete paths, and their
  `wishlist` wrappers), not only by `ON DELETE SET NULL`.

**Privacy of a taken copy.** An idea is also protected while any of its copies points at a protected gift (for
example when a protected co-recipient was added to the gift). That way "taken" never hints at a hidden gift, and a
copy is never shown as open while it is really taken.

**Move to ideas** is only for gifts with no giver that did not come from an idea. On a wish-linked gift with more than
one recipient it is 400 ("remove the other recipients first").

### The quick idea screen (`#/idea`)

- **For** becomes a multi-select of people (recent first, as today), starting on the person you came from.
- **What**, then optional **Link** and **Store**, then a **Share with relatives** switch, off.
- **From** goes away. An idea has no giver until it is taken.
- Save creates an idea, not a gift.

### An ideas screen (`#/ideas`)

Lists ideas, filterable by person, with each tagged person's copy shown as open or taken (and, when taken and
unlocked, which occasion). From here: open an idea to edit it, take it, flip share, delete it, and **Copy list**.

### The copied list

"Copy list" for one person or for everyone. Plain text, for each person in order with at least one open shared idea:

```
Skyler
- Animal tracking bracelet - Store Name - https://example.com/bracelet
- Logic puzzle book - https://example.com/puzzle

Taylor
- Animal tracking bracelet - Store Name - https://example.com/bracelet
```

- Only open copies of shared ideas. An idea tagged for three children appears under each child whose copy is still
  open.
- Title, then the store and the link when present. No price, giver, Santa, status, occasion or notes.
- A list that includes any protected person needs the unlock; while locked, "everyone" covers unprotected people
  only, as any other list does.
- A shortcut for "all the protected people" (the kids) next to "everyone". *(My call.)*

### The Family Wishlist pull-over

Today "add to board" on a wish creates an unassigned gift on one occasion's board. It now creates an **idea** tagged
with the one child whose wish it is, linked to the wish (`wishlist_item_id`, one idea per wish), share off.

- Taking that idea creates the gift linked to the same wish, and assigning its giver reserves the wish exactly as
  assigning does today (`wishlist.assign` and its rules). The giver label written back for a typed name is the typed
  name.
- A wish linked to an idea is no longer listed as an open wish on the board.
- Deleting the gift releases the reservation as today and reopens the copy.
- A wish-linked idea has exactly one tagged person, the child whose tab the wish is on. Tagging anyone else is 400.
  *(My call: a wish is one child's, and one gift per wish.)*
- **Held on the wishlist.** While no gift exists, someone can reserve or buy the wish in the wishlist app itself. Then
  the child's copy counts as **held**: not open, so it is not on the copied list, and taking it is 409 as `add_wish` is
  today. The idea and board show who holds it, from the feed. If the wishlist releases it, the copy is open again.
  Held is read from the cached feed, never stored. *(My call: the whole point is not to send a relative after
  something already spoken for.)*
- **Gone from the feed.** When the wish leaves the feed, the idea loses its wish link and stays, like
  `_unlink_gone` does for gifts.

### Boards and existing unassigned gifts

- A child's board shows that child's open ideas where "Unassigned" was, with Assign on each (which takes the idea).
- Existing unassigned gifts (no giver) stay where they are and gain one action, **Move to ideas**: the gift becomes an
  idea tagged with its recipients, linked to its wish if it had one, and the gift is deleted. Nothing is converted
  automatically.

### Typed givers

A gift's givers become people (as today) **plus at most one typed name**.

- On the gift screen, From keeps the checkboxes for owners and other people, and adds a text box with suggestions.
  The board's Assign menu and the take-idea flow offer the same: the fixed giver list, then "Someone else…" which asks
  for a name.
- A typed name is trimmed, 1 to 80 characters (the wishlist's `by` limit).
- Suggestions are the distinct typed names on gifts, most recently used first, case-insensitively deduplicated. Only
  names on gifts the caller can see count, so a locked caller is never offered a name used only on a hidden gift.
- **Track:** a gift with a typed name and no owner among its givers is on the theirs track (told → confirmed →
  given), not counted in spending, Santa not allowed. With an owner among its givers it is ours, as today.
- **Board:** its giver group's label is the people's label and the typed name joined by " & ", with its own Copy
  their list.
- **Wishlist label:** `wishlist.label` includes the typed name as one more part. Still nothing but givers goes into
  `by`.
- **Exchanges:** a gift with `exchange_id` refuses a typed name (400): a draw's gift is from the drawer.
- **School mode:** typed givers are family-only; a body naming one in school mode is 400. *(My call.)*
- **Agent API:** a typed name is giver information, so it is shown only where `giver_ids` is (the `full` scope), and
  nothing in `/api/v1` writes it.

### Schema (version 8)

My call on shape, for the builder to adjust:

- `ideas (id, title, url, store, price_cents, notes, share, wishlist_item_id UNIQUE NULL, created_by, created_at,
  updated_at)`
- `idea_people (idea_id → ideas ON DELETE CASCADE, person_id → people, gift_id → gifts ON DELETE SET NULL,
  PRIMARY KEY (idea_id, person_id))`. Open means `gift_id IS NULL`.
- `gifts.giver_name TEXT`.

No existing row changes. `ideas.wishlist_item_id` and `gifts.wishlist_item_id` may both name the same wish (the idea
and the gift taken from it); the wishlist code treats a wish as linked when either does.

## Decided, in Nick's words or as confirmed

- One idea, several people, each a separate possible present.
- The shared list carries no giver information and omits anything already recorded as taken for that person; the
  other people's copies stay.
- Ideas outlive occasions.
- Only pulled-over wishes are shared, and only when flagged; parents' own plans stay off the list.
- Typed givers are not added as people; owners and grandparents stay one-tap picks; typed names are suggested again.
- The list is pasted text, not a link.

## My calls (please check)

- Share defaults off.
- No "From" on the quick idea screen.
- Ideas work for adults too.
- A deleted gift reopens its copy; existing unassigned gifts get "Move to ideas" rather than an automatic migration.
- One typed name per gift.
- The copied list format above, including the "kids" shortcut.
- Typed givers are family-only; ideas, if school mode is cheap to support, may exist there too, otherwise family-only.
- A wish reserved or bought in the wishlist app counts as held: off the list and not takeable until released.
- Unassign on a gift from an idea is Back to ideas; dropping the person from the gift also reopens the copy.
- A wish-linked idea is for its one child only.

## Left to the builder

- Screen layout and wording, within the existing style.
- Route names and module split (likely `ideas.py`, `static/js/ideas.js`).
- Whether ideas exist in school mode (family-only is acceptable).
- Untagging a taken copy: allow or refuse.

## Left open

- Agent API and MCP: the `shop` scope reads gifts in status `idea` today. New ideas are not gifts, so an agent stops
  seeing them. Exposing ideas in `/api/v1` is a later change.
