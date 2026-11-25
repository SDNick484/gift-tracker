// #/board/<person_id>  A protected person's board: the person's open ideas (Assign takes one), old giverless gifts
// (each can move to ideas), then the gifts grouped by who gives them. Assign picks from a fixed list of givers (the
// households, plus each owner alone) or "Someone else…", a typed name, so a gift cannot go to two people at once: the
// menu sends the gift's current givers and name as expected_giver_ids and expected_giver_name and the server answers
// 409 when someone else got there first. On a gift taken from an idea, Unassign is "Back to ideas" (the gift goes and
// the idea's copy reopens). Told and confirmed are buttons on a gift from someone else; Copy list puts a giver's
// names and links on the clipboard.
//
// With the wishlist link on (board.wishlist present, docs/wishlist-feed.md) the board also shows the wishes on the
// person's wishlist tab: open ones can be added to ideas (then assigned from there, which reserves them), ones
// reserved on the wishlist itself are listed under who has them, and a name the server cannot match is mapped to a
// person once.
// While the wishlist cannot be reached its last answer is shown with its age, and Assign is off for wishes. All
// wishlist text is the wishlist's, so it is escaped like anything typed here.
import { get, send } from "./api.js";
import { currentOccasion, esc, money, renderChecked, renderColor, renderDisabled, renderOptions, state, statusSteps, toast } from "./core.js";
import { takeBody } from "./ideas.js";

// The fixed list of givers: every household (a label shared by its members) except those made only of protected
// people, then each owner alone. Values are comma-joined person ids.
export function assignChoices(households, people) {
  const protectedIds = new Set(people.filter(p => p.protected).map(p => p.id));
  const choices = households
    .filter(h => h.person_ids.some(id => !protectedIds.has(id)))
    .map(h => ({ value: h.person_ids.join(","), label: h.label }));
  for (const o of people.filter(p => p.is_user && p.active)) {
    if (!choices.some(c => c.value === String(o.id))) choices.push({ value: String(o.id), label: `${o.name} only` });
  }
  return choices;
}

// The POST body for choosing `value` in a gift's menu: "none" unassigns, "other" gives it to the typed `name`. The
// gift's current givers and name go along as expected, so a change someone else made in between is a 409.
export function assignBody(gift, value, name = null) {
  const giverIds = value === "none" || value === "other" ? [] : value.split(",").map(Number);
  const giverName = value === "other" ? (name ?? "").trim() || null : null;
  return { giver_ids: giverIds, giver_name: giverName, expected_giver_ids: gift.giver_ids, expected_giver_name: gift.giver_name ?? null };
}

const hasGiver = g => g.giver_ids.length > 0 || Boolean(g.giver_name);

export function countGifts(board) {
  const assigned = board.givers.reduce((n, g) => n + g.gifts.length, 0);
  return { total: assigned + board.unassigned.length, unassigned: board.unassigned.length };
}

export function splitLine(split) {
  return `Split: ${split.santa} Santa · ${split.us} from us · ${split.others} from others`;
}

export function evennessLine(evenness) {
  return evenness.length ? `Side by side: ${evenness.map(e => `${e.name} ${money(e.cents)}`).join(" · ")}` : "";
}

const plural = (n, word) => `${n} ${word}${n === 1 ? "" : "s"}`;

const renderSantaCheck = g => (typeof g.santa === "boolean"
  ? `<label class="check"><input type="checkbox" data-santa="${esc(g.id)}"${renderChecked(g.santa)}> Santa</label>`
  : "");
const renderStatusButton = (g, s) => `<button type="button" class="step" data-gift="${esc(g.id)}" data-status="${esc(s)}" aria-pressed="${esc(String(g.status === s))}">${esc(s)}</button>`;
const renderStatusButtonList = g => statusSteps("theirs").map(s => renderStatusButton(g, s)).join("");
const renderStatusButtons = g => `<span class="steps" role="group" aria-label="Status">${renderStatusButtonList(g)}</span>`;
const renderControl = g => (g.track === "theirs" ? renderStatusButtons(g) : g.giver_ids.length ? renderSantaCheck(g) : "");

const OTHER = { value: "other", label: "Someone else…" };
// Unassign, or for a gift taken from an idea "Back to ideas" (it never sits unassigned on a board).
const unassignChoice = g => (g.from_idea ? { value: "back", label: "Back to ideas" } : { value: "none", label: "Unassign" });
const renderAssignOptions = (g, choices) => renderOptions([
  { value: "", label: hasGiver(g) ? "Reassign…" : "Assign…" }, ...choices, OTHER,
  ...(hasGiver(g) ? [unassignChoice(g)] : []),
], "");
// A gift linked to a wish cannot be reassigned while the wishlist is unreachable (the server would answer 503).
const assignOff = (g, offline) => Boolean(offline && g.wish?.listed);
const renderAssign = (g, choices, offline) => `<select class="assign" data-gift="${esc(g.id)}" aria-label="Assign ${esc(g.title)}"${renderDisabled(assignOff(g, offline))}>${renderAssignOptions(g, choices)}</select>`;

const renderTold = g => (g.track === "theirs" && g.told_at ? ` · told ${esc(g.told_at)}` : "");
const renderStore = g => (g.store ? ` · ${esc(g.store)}` : "");
const renderWishBadge = g => (g.wish ? ` <span class="badge ${esc(wishBadgeClass(g))}">${esc(wishBadge(g))}</span>` : "");
const renderGiftLine = g => `<span class="badge">${esc(g.status)}</span>${renderStore(g)}${renderTold(g)}${renderWishBadge(g)}`;

// An old gift with no giver (not one taken from an idea), still at the idea status, can become an idea for its
// recipients.
const renderToIdea = g => (!hasGiver(g) && !g.from_idea && g.status === "idea"
  ? `<button type="button" class="btn small" data-to-idea="${esc(g.id)}">Move to ideas</button>` : "");
const renderNameSlot = (kind, id) => `<div class="slot" data-name-slot="${esc(kind)}-${esc(id)}"></div>`;

const renderGift = (g, choices, offline) => `<li class="row">
  <a class="main" href="#/gift/${esc(g.id)}"><div class="name">${esc(g.title)}</div><div class="line">${renderGiftLine(g)}</div></a>
  <div class="amt">${esc(money(g.price_cents))}</div>
  <div class="row-actions">${renderControl(g)}${renderAssign(g, choices, offline)}${renderToIdea(g)}</div>
  ${renderNameSlot("gift", g.id)}
</li>`;
const renderGifts = (gifts, choices, offline) => gifts.map(g => renderGift(g, choices, offline)).join("");

const renderCopy = (group, index) => (group.is_us ? "" : `<button type="button" class="btn small" data-copy="${esc(index)}">Copy list</button>`);
const renderGroup = (group, index, choices, offline) => `<section class="card board-group">
  <div class="group-head"><span><span class="title">${esc(group.label)}</span> <span class="muted small">${esc(plural(group.gifts.length, "gift"))}</span></span>${renderCopy(group, index)}</div>
  <div data-copy-box="${esc(index)}"></div>
  <ul class="rows">${renderGifts(group.gifts, choices, offline)}</ul>
</section>`;
const renderGroups = (groups, choices, offline) => groups.map((g, i) => renderGroup(g, i, choices, offline)).join("");

const renderUnassigned = (list, choices, offline) => (list.length
  ? `<section><h3>Unassigned</h3><ul class="rows">${renderGifts(list, choices, offline)}</ul></section>`
  : "");

// "Someone else…": the typed name, with the suggestions of names used before.
export const renderNameForm = (kind, id) => `<form class="fields name-form" data-form="giver-name" data-kind="${esc(kind)}" data-id="${esc(id)}" autocomplete="off">
  <label class="field"><span>Who is giving it?</span><input name="giver_name" type="text" maxlength="80" list="giver-names" placeholder="A name"></label>
  <p class="form-error" role="alert"></p>
  <div class="actions"><button class="btn primary small" type="submit">OK</button><button type="button" class="btn small" data-cancel-name>Cancel</button></div>
</form>`;

const renderNameOption = n => `<option value="${esc(n)}"></option>`;
const renderNameOptions = names => names.map(renderNameOption).join("");
const renderNames = names => `<datalist id="giver-names">${renderNameOptions(names)}</datalist>`;

// ---------------------------------------------------------------- the person's ideas (open ones can be assigned)
const renderIdeaTitle = idea => (safeHref(idea.url)
  ? `<a href="${esc(safeHref(idea.url))}" target="_blank" rel="noopener noreferrer">${esc(idea.title)}</a>`
  : esc(idea.title));
const heldBy = (idea, personId) => idea.people.find(c => c.person_id === personId)?.held_by ?? "someone";
const ideaLine = (idea, personId) => [idea.store, idea.copy_state === "held" ? `on the wishlist: ${heldBy(idea, personId)}` : null]
  .filter(Boolean).join(" · ");
const renderIdeaLine = (idea, personId) => (ideaLine(idea, personId) ? `<div class="line">${esc(ideaLine(idea, personId))}</div>` : "");
const renderIdeaAssign = (idea, choices) => (idea.copy_state === "open"
  ? `<div class="row-actions"><select class="assign" data-idea="${esc(idea.id)}" aria-label="Assign ${esc(idea.title)}">${renderOptions([{ value: "", label: "Assign…" }, ...choices, OTHER], "")}</select></div>`
  : "");
const renderIdeaDim = idea => (idea.copy_state === "open" ? "" : " dim");
const renderIdea = (idea, choices, personId) => `<li class="row${renderIdeaDim(idea)}">
  <div class="main"><div class="name">${renderIdeaTitle(idea)}</div>${renderIdeaLine(idea, personId)}</div>
  <div class="amt">${esc(money(idea.price_cents))}</div>
  ${renderIdeaAssign(idea, choices)}
  ${renderNameSlot("idea", idea.id)}
</li>`;
const renderIdeaRows = (list, choices, personId) => list.map(i => renderIdea(i, choices, personId)).join("");
const renderIdeaSection = (list, choices, personId) => (list?.length
  ? `<section><h3>Ideas</h3><ul class="rows">${renderIdeaRows(list, choices, personId)}</ul></section>`
  : "");

// ---------------------------------------------------------------- the wishlist (only when board.wishlist exists)
// A link from the wishlist is followed only when it is http(s); the server already drops anything else.
export function safeHref(url) {
  return typeof url === "string" && /^https?:\/\//i.test(url) ? url : "";
}

export function ageText(seconds) {
  if (!Number.isInteger(seconds)) return "";
  if (seconds < 60) return "just now";
  if (seconds < 3600) return `${Math.floor(seconds / 60)} min ago`;
  if (seconds < 172800) return `${Math.floor(seconds / 3600)} h ago`;
  return `${Math.floor(seconds / 86400)} days ago`;
}

export function wishlistLine(wl) {
  if (wl.reachable) return `Wishlist checked ${ageText(wl.age_seconds)}.`;
  if (wl.fetched_at === null) return "The wishlist cannot be reached, and nothing has been read from it yet.";
  return `The wishlist cannot be reached. Showing what it said ${ageText(wl.age_seconds)}; Assign is off for wishes.`;
}

// The badge on a gift linked to a wish: whether the wishlist shows what the tracker expects.
export function wishBadge(gift) {
  const w = gift.wish;
  if (!w) return "";
  if (!w.listed) return "Not in the wishlist feed";
  if (w.in_sync) return w.purchased ? "Bought on the wishlist" : "On the wishlist";
  if (w.purchased) return `Wishlist says bought by ${w.purchased_by ?? "someone"}`;
  if (w.reserved_by) return `Wishlist says reserved by ${w.reserved_by}`;
  return "Wishlist says nobody has it";
}

const wishBadgeClass = g => (g.wish && g.wish.listed && !g.wish.in_sync ? "warn" : "wish");

// The PUT /api/wishlist/aliases body for mapping an unmatched holder to a person.
export function aliasBody(entry, personId) {
  return { kind: entry.kind, value: entry.value, person_id: Number(personId) };
}

const renderWishName = w => (safeHref(w.url)
  ? `<a href="${esc(safeHref(w.url))}" target="_blank" rel="noopener noreferrer">${esc(w.name ?? "Wish")}</a>`
  : esc(w.name ?? "Wish"));
const wishDetails = w => [w.store, w.occasion, w.secret ? "secret idea" : null].filter(Boolean).join(" · ");
const renderWishLine = w => (wishDetails(w) ? `<div class="line">${esc(wishDetails(w))}</div>` : "");

const renderOpenWish = (w, offline) => `<li class="row wish">
  <div class="main"><div class="name">${renderWishName(w)}</div>${renderWishLine(w)}</div>
  <div class="amt">${esc(money(w.price_cents))}</div>
  <div class="row-actions"><button type="button" class="btn small" data-wish="${esc(w.id)}"${renderDisabled(offline)}>Add to ideas</button></div>
</li>`;

const holderText = (w, people) => {
  const who = people.find(p => p.id === w.person_id);
  const verb = w.purchased ? "Bought by" : "Reserved by";
  return `${verb} ${w.label ?? "someone"}${who && who.name !== w.label ? ` (${who.name})` : ""}`;
};
const renderReservedWish = (w, people) => `<li class="row wish dim">
  <div class="main"><div class="name">${renderWishName(w)}</div><div class="line">${esc(holderText(w, people))}</div></div>
  <div class="amt">${esc(money(w.price_cents))}</div>
</li>`;

const renderPersonOptions = people => renderOptions([{ value: "", label: "Who is this?" },
  ...people.filter(p => p.active).map(p => ({ value: String(p.id), label: p.name }))], "");
const renderUnmatched = (u, index, people) => `<li class="row">
  <div class="main"><div class="name">${esc(u.label)}</div><div class="line">${esc(u.kind === "email" ? u.value : "Name on the wishlist")}</div></div>
  <div class="row-actions"><select class="assign" data-alias="${esc(index)}" aria-label="Who is ${esc(u.label)}">${renderPersonOptions(people)}</select></div>
</li>`;

const tabChoices = wl => [{ value: "", label: "Choose a tab…" }, ...wl.tabs.map(t => ({ value: String(t.id), label: t.name ?? `Tab ${t.id}` }))];
const renderTabLink = wl => (wl.linked ? "" : `<label class="field"><span>Which wishlist tab is this?</span>
  <select data-wl-tab>${renderOptions(tabChoices(wl), "")}</select></label>`);

const renderEach = (list, renderItem) => list.map(renderItem).join("");
const renderWishSection = (title, list, renderItem) => (list.length
  ? `<h3>${esc(title)}</h3><ul class="rows">${renderEach(list, renderItem)}</ul>` : "");

export const renderWishlist = (wl, people) => `<section class="card wishlist">
  <div class="group-head"><span><span class="title">Wishlist</span> <span class="muted small">${esc(wishlistLine(wl))}</span></span>
    <button type="button" class="btn small" data-wl-refresh>Refresh</button></div>
  ${renderTabLink(wl)}
  ${renderWishSection("On the wishlist", wl.open, w => renderOpenWish(w, !wl.reachable))}
  ${renderWishSection("Reserved on the wishlist", wl.reserved, w => renderReservedWish(w, people))}
  ${renderWishSection("Who is this?", wl.unmatched, (u, i) => renderUnmatched(u, i, people))}
</section>`;

// The left column: the open and held ideas, then any old gift with no giver. Nothing at all when both are empty.
const renderLeftSections = (board, choices, offline) => renderIdeaSection(board.ideas, choices, board.person.id)
  + renderUnassigned(board.unassigned, choices, offline);
const renderLeft = (board, choices, offline) => (renderLeftSections(board, choices, offline)
  ? `<div class="col">${renderLeftSections(board, choices, offline)}</div>` : "");

const renderWishlistIf = (board, people) => (board.wishlist ? renderWishlist(board.wishlist, people) : "");

export const renderBoard = (board, occasionName, choices, people = [], names = []) => {
  const n = countGifts(board);
  const offline = Boolean(board.wishlist && !board.wishlist.reachable);
  return `
  <a class="back" href="#/">&larr; Home</a>
  <section class="card colored"${renderColor(board.person.color)}>
    <div class="head"><div><h2>${esc(board.person.name)}</h2><span class="sub">${esc(occasionName)}</span></div>
      <span class="sub">${esc(plural(n.total, "gift"))} · ${esc(String(n.unassigned))} unassigned</span></div>
    <p class="stat-line">${esc(splitLine(board.split))}</p>
    <p class="stat-line">${esc(evennessLine(board.evenness))}</p>
    <div class="actions"><a class="btn small" href="#/person/${esc(board.person.id)}">Plan and history</a></div>
  </section>
  ${renderWishlistIf(board, people)}
  ${renderNames(names)}
  <div class="cols">
    ${renderLeft(board, choices, offline)}
    <div class="col">${renderGroups(board.givers, choices, offline)}</div>
  </div>`;
};

// Puts `text` on the clipboard; when the browser will not allow that, shows it selected in a read-only box.
export async function copyList(text, box) {
  try {
    await navigator.clipboard.writeText(text);
    toast("Copied");
    return;
  } catch {
    const area = document.createElement("textarea");
    area.readOnly = true;
    area.value = text;
    area.rows = Math.min(8, text.split("\n").length + 1);
    box.replaceChildren(area);
    area.focus();
    area.select();
    toast("Copy the selected text");
  }
}

export async function render(ctx) {
  const { view } = ctx;
  const occasion = currentOccasion();
  const personId = Number(ctx.parts[1]);
  if (!occasion) {
    view.innerHTML = `<div class="notice"><p>Create an occasion first.</p><p><a class="btn primary" href="#/settings">Open Settings</a></p></div>`;
    return;
  }
  let board, households, names;
  try {
    [board, households, names] = await Promise.all([get(`/api/occasions/${occasion.id}/board/${personId}`), get("/api/households"),
      get("/api/giver-names")]);
  } catch (e) {
    if (e.status !== 404) throw e;
    view.innerHTML = `<div class="notice"><p>No board for this person.</p><p><a class="btn" href="#/">Home</a></p></div>`;
    return;
  }
  if (!ctx.alive()) return;
  const choices = assignChoices(households, state.people);
  view.innerHTML = renderBoard(board, occasion.name, choices, state.people, names);
  const allGifts = [...board.unassigned, ...board.givers.flatMap(g => g.gifts)];
  const giftOf = id => allGifts.find(g => g.id === Number(id));

  // Whatever went wrong, show the server's words and draw the board again from the server's truth.
  const attempt = async (action, done) => {
    try {
      await action();
      if (done) toast(done);
    } catch (err) {
      if (!err.locked) toast(err.message);
    }
    if (ctx.alive()) ctx.rerender();
  };

  // Taking the idea for this person makes the gift (and reserves a linked wish); assigning a gift replaces its givers.
  const takeIdea = (ideaId, value, name = "") => attempt(
    () => send("POST", `/api/ideas/${ideaId}/take/${personId}`, takeBody(value === "other" ? "" : value, name, String(occasion.id))), "Assigned");
  const assignGift = (gift, value, name = null) => (value === "back"
    ? attempt(() => send("POST", `/api/gifts/${gift.id}/back-to-ideas`, {}), "Back in ideas")
    : attempt(() => send("POST", `/api/gifts/${gift.id}/assign`, assignBody(gift, value, name)), "Assigned"));
  const askName = (kind, id) => {
    const slot = view.querySelector(`[data-name-slot="${kind}-${id}"]`);
    slot.innerHTML = renderNameForm(kind, id);
    slot.querySelector("input").focus();
  };

  view.addEventListener("change", e => {
    const t = e.target;
    if (t.matches("select[data-idea]") && t.value) {
      if (t.value === "other") askName("idea", t.dataset.idea);
      else takeIdea(t.dataset.idea, t.value);
    } else if (t.matches("select[data-wl-tab]") && t.value) {
      attempt(() => send("PATCH", `/api/people/${personId}`, { wishlist_person_id: Number(t.value) }), "Linked");
    } else if (t.matches("select[data-alias]") && t.value) {
      attempt(() => send("PUT", "/api/wishlist/aliases", aliasBody(board.wishlist.unmatched[Number(t.dataset.alias)], t.value)), "Saved");
    } else if (t.matches("select.assign[data-gift]") && t.value) {
      if (t.value === "other") askName("gift", t.dataset.gift);
      else assignGift(giftOf(t.dataset.gift), t.value);
    } else if (t.matches("input[data-santa]")) {
      attempt(() => send("PATCH", `/api/gifts/${t.dataset.santa}`, { santa: t.checked }));
    }
  });
  view.addEventListener("click", e => {
    if (e.target.closest("[data-wl-refresh]")) {
      attempt(() => send("POST", "/api/wishlist/refresh", {}));
      return;
    }
    const wish = e.target.closest("[data-wish]");
    if (wish) { // pulled over as an idea for this person; nothing is sent to the wishlist until it is assigned
      attempt(() => send("POST", `/api/occasions/${occasion.id}/board/${personId}/wishes/${wish.dataset.wish}`, {}), "Added to ideas");
      return;
    }
    const toIdea = e.target.closest("[data-to-idea]");
    if (toIdea) {
      attempt(() => send("POST", `/api/gifts/${toIdea.dataset.toIdea}/to-idea`, {}), "Moved to ideas");
      return;
    }
    const cancel = e.target.closest("[data-cancel-name]");
    if (cancel) {
      const form = cancel.closest("form");
      const menu = view.querySelector(`select[data-${form.dataset.kind}="${form.dataset.id}"]`);
      if (menu) menu.value = ""; // back to Assign…
      cancel.closest(".slot").innerHTML = "";
      return;
    }
    const copy = e.target.closest("[data-copy]");
    if (copy) {
      const index = copy.dataset.copy;
      copyList(board.givers[Number(index)].list_text, view.querySelector(`[data-copy-box="${index}"]`));
      return;
    }
    const step = e.target.closest("button[data-status]");
    if (step && step.dataset.gift && giftOf(step.dataset.gift)?.status !== step.dataset.status) {
      attempt(() => send("POST", `/api/gifts/${step.dataset.gift}/status`, { status: step.dataset.status }));
    }
  });
  view.addEventListener("submit", e => {
    e.preventDefault();
    const form = e.target;
    if (form.dataset.form !== "giver-name") return;
    const name = form.elements.giver_name.value.trim();
    if (!name) {
      form.querySelector(".form-error").textContent = "Type a name.";
      return;
    }
    form.querySelector("button[type=submit]").disabled = true;
    return form.dataset.kind === "idea" ? takeIdea(form.dataset.id, "other", name) : assignGift(giftOf(form.dataset.id), "other", name);
  });
}
