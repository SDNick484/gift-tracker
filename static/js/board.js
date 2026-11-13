// #/board/<person_id>  A protected person's board: unassigned wishes, then the gifts grouped by who gives them.
// Assign picks from a fixed list of givers (the households, plus each owner alone), so a gift cannot go to two
// people at once: the menu sends the gift's current givers as expected_giver_ids and the server answers 409 when
// someone else got there first. Told and confirmed are buttons on a gift from someone else; Copy list puts a
// giver's names and links on the clipboard.
import { get, send } from "./api.js";
import { currentOccasion, esc, money, renderChecked, renderColor, renderOptions, state, statusSteps, toast } from "./core.js";

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

// The POST body for choosing `value` in a gift's menu: "none" unassigns.
export function assignBody(gift, value) {
  return { giver_ids: value === "none" ? [] : value.split(",").map(Number), expected_giver_ids: gift.giver_ids };
}

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

const renderAssignOptions = (g, choices) => renderOptions([
  { value: "", label: g.giver_ids.length ? "Reassign…" : "Assign…" }, ...choices,
  ...(g.giver_ids.length ? [{ value: "none", label: "Unassign" }] : []),
], "");
const renderAssign = (g, choices) => `<select class="assign" data-gift="${esc(g.id)}" aria-label="Assign ${esc(g.title)}">${renderAssignOptions(g, choices)}</select>`;

const renderTold = g => (g.track === "theirs" && g.told_at ? ` · told ${esc(g.told_at)}` : "");
const renderStore = g => (g.store ? ` · ${esc(g.store)}` : "");
const renderGiftLine = g => `<span class="badge">${esc(g.status)}</span>${renderStore(g)}${renderTold(g)}`;

const renderGift = (g, choices) => `<li class="row">
  <a class="main" href="#/gift/${esc(g.id)}"><div class="name">${esc(g.title)}</div><div class="line">${renderGiftLine(g)}</div></a>
  <div class="amt">${esc(money(g.price_cents))}</div>
  <div class="row-actions">${renderControl(g)}${renderAssign(g, choices)}</div>
</li>`;
const renderGifts = (gifts, choices) => gifts.map(g => renderGift(g, choices)).join("");

const renderCopy = (group, index) => (group.is_us ? "" : `<button type="button" class="btn small" data-copy="${esc(index)}">Copy list</button>`);
const renderGroup = (group, index, choices) => `<section class="card board-group">
  <div class="group-head"><span><span class="title">${esc(group.label)}</span> <span class="muted small">${esc(plural(group.gifts.length, "gift"))}</span></span>${renderCopy(group, index)}</div>
  <div data-copy-box="${esc(index)}"></div>
  <ul class="rows">${renderGifts(group.gifts, choices)}</ul>
</section>`;
const renderGroups = (groups, choices) => groups.map((g, i) => renderGroup(g, i, choices)).join("");

const renderUnassigned = (list, choices) => (list.length
  ? `<section class="col"><h3>Unassigned</h3><ul class="rows">${renderGifts(list, choices)}</ul></section>`
  : `<section class="col"><h3>Unassigned</h3><p class="empty">Everything has a giver.</p></section>`);

export const renderBoard = (board, occasionName, choices) => {
  const n = countGifts(board);
  return `
  <a class="back" href="#/">&larr; Home</a>
  <section class="card colored"${renderColor(board.person.color)}>
    <div class="head"><div><h2>${esc(board.person.name)}</h2><span class="sub">${esc(occasionName)}</span></div>
      <span class="sub">${esc(plural(n.total, "gift"))} · ${esc(String(n.unassigned))} unassigned</span></div>
    <p class="stat-line">${esc(splitLine(board.split))}</p>
    <p class="stat-line">${esc(evennessLine(board.evenness))}</p>
    <div class="actions"><a class="btn small" href="#/person/${esc(board.person.id)}">Plan and history</a></div>
  </section>
  <div class="cols">
    ${renderUnassigned(board.unassigned, choices)}
    <div class="col">${renderGroups(board.givers, choices)}</div>
  </div>`;
};

// Puts `text` on the clipboard; when the browser will not allow that, shows it selected in a read-only box.
async function copyList(text, box) {
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
  let board, households;
  try {
    [board, households] = await Promise.all([get(`/api/occasions/${occasion.id}/board/${personId}`), get("/api/households")]);
  } catch (e) {
    if (e.status !== 404) throw e;
    view.innerHTML = `<div class="notice"><p>No board for this person.</p><p><a class="btn" href="#/">Home</a></p></div>`;
    return;
  }
  if (!ctx.alive()) return;
  const choices = assignChoices(households, state.people);
  view.innerHTML = renderBoard(board, occasion.name, choices);
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

  view.addEventListener("change", e => {
    const t = e.target;
    if (t.matches("select.assign") && t.value) {
      const gift = giftOf(t.dataset.gift);
      attempt(() => send("POST", `/api/gifts/${gift.id}/assign`, assignBody(gift, t.value)), "Assigned");
    } else if (t.matches("input[data-santa]")) {
      attempt(() => send("PATCH", `/api/gifts/${t.dataset.santa}`, { santa: t.checked }));
    }
  });
  view.addEventListener("click", e => {
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
}
