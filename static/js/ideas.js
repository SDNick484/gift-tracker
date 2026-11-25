// #/ideas  Every idea, or one person's (?person=<id>): each idea with its tagged people and the state of each one's
// copy (open; taken, with the occasion of the gift; held on the wishlist, with who holds it), the share switch, the
// edit fields and Delete. "Take for <name>…" on an open copy opens a small form: who gives it (the board's fixed
// giver list, or a typed name with suggestions) and the occasion (the current one first). Copy list puts the shared
// open ideas of one person, the kids or everyone on the clipboard. The server leaves protected ideas out while
// locked and answers 423 for a list naming a protected person; nothing here decides that.
import { get, send } from "./api.js";
import { centsToText, choosablePeople, currentOccasion, esc, money, renderChecked, renderColor, renderOptions, state, toast } from "./core.js";
import { assignChoices, copyList, safeHref } from "./board.js";

// The state of `personId`'s copy of the idea, or null when the idea is not for that person.
export function copyState(idea, personId) {
  const c = idea.people.find(p => p.person_id === personId);
  if (!c) return null;
  if (c.gift_id !== null && c.gift_id !== undefined) return "taken";
  return c.state === "held" || c.state === "taken" ? c.state : "open";
}

// The ideas tagged with `personId`; every idea for null.
export const filterIdeas = (ideas, personId) => (personId === null || personId === undefined
  ? ideas.slice()
  : ideas.filter(i => i.people.some(p => p.person_id === personId)));

// "The kids": the active protected people.
export const kidsIds = people => people.filter(p => p.active && p.protected).map(p => p.id);

// GET path for the copied list: null is everyone this request may see.
export const shareQuery = ids => (ids ? `/api/ideas/share?people=${ids.join(",")}` : "/api/ideas/share");

// The POST /api/ideas/<id>/take/<person_id> body from the take form's raw values.
export function takeBody(giverValue, name, occasionValue) {
  const body = { giver_ids: giverValue ? giverValue.split(",").map(Number) : [] };
  const typed = (name ?? "").trim();
  if (typed) body.giver_name = typed;
  if (occasionValue) body.occasion_id = Number(occasionValue);
  return body;
}

// The PATCH /api/ideas/<id> body from an edit form: every field, so a cleared one is cleared, and the tagged people.
export function editBody(form) {
  const field = name => (form.elements[name]?.value ?? "").trim();
  return { title: field("title"), url: field("url"), store: field("store"), price: field("price"), notes: field("notes"),
    person_ids: [...form.querySelectorAll('input[name="for"]:checked')].map(i => Number(i.value)) };
}

const nameOf = (id, people) => people.find(p => p.id === id)?.name ?? "Someone";
const colorOf = (id, people) => people.find(p => p.id === id)?.color;
const occasionName = (id, occasions) => occasions.find(o => o.id === id)?.name ?? "a gift";

// People an idea can be tagged with: the choosable ones, plus anyone it is already tagged with.
const tagPool = (idea, choosable, people) => [...choosable,
  ...idea.people.filter(c => !choosable.some(p => p.id === c.person_id)).map(c => ({ id: c.person_id, name: nameOf(c.person_id, people) }))];

// ---------------------------------------------------------------- markup
const renderTitle = idea => (safeHref(idea.url)
  ? `<a href="${esc(safeHref(idea.url))}" target="_blank" rel="noopener noreferrer">${esc(idea.title)}</a>`
  : esc(idea.title));
const ideaDetails = idea => [idea.store, idea.wishlist_item_id ? "from the wishlist" : null].filter(Boolean).join(" · ");
const renderDetails = idea => (ideaDetails(idea) ? `<p class="muted small">${esc(ideaDetails(idea))}</p>` : "");
const renderNotes = idea => (idea.notes ? `<p class="small idea-notes">${esc(idea.notes)}</p>` : "");

const renderCopyState = (c, occasions) => {
  if (c.state === "taken" || c.gift_id) return `taken · <a href="#/gift/${esc(c.gift_id)}">${esc(occasionName(c.occasion_id, occasions))}</a>`;
  if (c.state === "held") return `on the wishlist: ${esc(c.held_by ?? "someone")}`;
  return "open";
};
const renderTakeButton = (idea, c, people) => (copyState(idea, c.person_id) === "open"
  ? `<button type="button" class="btn small" data-take="${esc(idea.id)}" data-person="${esc(c.person_id)}">Take for ${esc(nameOf(c.person_id, people))}…</button>`
  : "");
const renderDim = (idea, c) => (copyState(idea, c.person_id) === "open" ? "" : " dim");
const renderCopy = (idea, c, people, occasions) => `<li class="row${renderDim(idea, c)}"${renderColor(colorOf(c.person_id, people))}>
  <div class="main"><div class="name">${esc(nameOf(c.person_id, people))}</div><div class="line">${renderCopyState(c, occasions)}</div></div>
  <div class="amt">${renderTakeButton(idea, c, people)}</div>
  <div class="slot" data-take-slot="${esc(idea.id)}-${esc(c.person_id)}"></div>
</li>`;
const renderCopies = (idea, people, occasions) => idea.people.map(c => renderCopy(idea, c, people, occasions)).join("");

const renderTagCheck = (p, on) => `<label class="check"><input type="checkbox" name="for" value="${esc(p.id)}"${renderChecked(on)}> ${esc(p.name)}</label>`;
const renderTagChecks = (idea, choosable, people) => tagPool(idea, choosable, people)
  .map(p => renderTagCheck(p, idea.people.some(c => c.person_id === p.id))).join("");

const renderEdit = (idea, choosable, people) => `<details><summary>Edit idea</summary>
  <form class="fields" data-form="idea-edit" data-idea="${esc(idea.id)}" autocomplete="off">
    <label class="field"><span>What</span><input name="title" type="text" maxlength="200" value="${esc(idea.title)}" required></label>
    <div class="two"><label class="field"><span>Link</span><input name="url" type="url" maxlength="2000" value="${esc(idea.url ?? "")}"></label>
      <label class="field"><span>Store</span><input name="store" type="text" maxlength="200" value="${esc(idea.store ?? "")}"></label></div>
    <label class="field"><span>Price</span><input name="price" type="text" inputmode="decimal" value="${esc(centsToText(idea.price_cents))}"></label>
    <label class="field"><span>Notes</span><textarea name="notes" maxlength="2000">${esc(idea.notes ?? "")}</textarea></label>
    <div class="field"><span>For</span><div class="check-list">${renderTagChecks(idea, choosable, people)}</div></div>
    <p class="form-error" role="alert"></p>
    <div class="actions"><button class="btn" type="submit">Save</button><button type="button" class="btn danger" data-delete="${esc(idea.id)}">Delete idea</button></div>
  </form></details>`;

const renderIdea = (idea, people, occasions, choosable) => `<section class="card idea" data-idea="${esc(idea.id)}">
  <div class="group-head"><span class="title">${renderTitle(idea)}</span><span class="amt">${esc(money(idea.price_cents))}</span></div>
  ${renderDetails(idea)}${renderNotes(idea)}
  <label class="check"><input type="checkbox" data-share="${esc(idea.id)}"${renderChecked(idea.share)}> Share with relatives</label>
  <ul class="rows">${renderCopies(idea, people, occasions)}</ul>
  ${renderEdit(idea, choosable, people)}
</section>`;
const renderIdeaCards = (ideas, people, occasions, choosable) => ideas.map(i => renderIdea(i, people, occasions, choosable)).join("");

export const renderIdeas = (ideas, people, occasions = [], choosable = people.filter(p => p.active && !p.is_user)) => (ideas.length
  ? `<div class="cols">${renderIdeaCards(ideas, people, occasions, choosable)}</div>`
  : `<p class="empty">No ideas yet.</p>`);

// The take form for one copy: who gives it and in which occasion.
const occasionChoices = occasions => occasions.filter(o => !o.archived).map(o => ({ value: o.id, label: o.name }));
export const renderTakeForm = (ideaId, personId, choices, occasions, occasionId) => `<form class="fields" data-form="take" data-idea="${esc(ideaId)}" data-person="${esc(personId)}" autocomplete="off">
  <div class="two"><label class="field"><span>From</span><select name="giver">${renderOptions([{ value: "", label: "Choose…" }, ...choices], "")}</select></label>
    <label class="field"><span>Or someone else</span><input name="giver_name" type="text" maxlength="80" list="giver-names" placeholder="A name"></label></div>
  <label class="field"><span>Occasion</span><select name="occasion">${renderOptions(occasionChoices(occasions), occasionId)}</select></label>
  <p class="form-error" role="alert"></p>
  <div class="actions"><button class="btn primary small" type="submit">Take</button><button type="button" class="btn small" data-cancel-take>Cancel</button></div>
</form>`;

const renderNameOption = n => `<option value="${esc(n)}"></option>`;
const renderNameOptions = names => names.map(renderNameOption).join("");
const filterChoices = (choosable, people, personId) => {
  const list = choosable.map(p => ({ value: p.id, label: p.name }));
  if (personId !== null && !choosable.some(p => p.id === personId)) list.push({ value: personId, label: nameOf(personId, people) });
  return [{ value: "", label: "Everyone" }, ...list];
};
const renderForQuery = personId => (personId === null ? "" : `?for=${personId}`);
const renderCopyButton = (kind, label, on) => (on ? `<button type="button" class="btn small" data-copy="${esc(kind)}">${esc(label)}</button>` : "");

export const renderIdeasPage = ({ ideas, people, choosable = people.filter(p => p.active && !p.is_user), occasions, personId, names }) => `
  <a class="back" href="#/">&larr; Home</a>
  <div class="head"><h2>Ideas</h2><a class="btn primary small" href="#/idea${esc(renderForQuery(personId))}">+ Idea</a></div>
  <section class="card fields">
    <label class="field"><span>Show</span><select id="idea-person">${renderOptions(filterChoices(choosable, people, personId), personId ?? "")}</select></label>
    <div class="actions"><span class="muted small">Copy list:</span>${renderCopyButton("person", "This person", personId !== null)}${renderCopyButton("kids", "The kids", kidsIds(people).length > 0)}${renderCopyButton("everyone", "Everyone", true)}</div>
    <div data-copy-box></div>
  </section>
  <datalist id="giver-names">${renderNameOptions(names)}</datalist>
  ${renderIdeas(filterIdeas(ideas, personId), people, occasions, choosable)}`;

export async function render(ctx) {
  const { view } = ctx;
  const personId = /^\d+$/.test(ctx.query.person ?? "") ? Number(ctx.query.person) : null;
  const [ideas, households, names] = await Promise.all([get("/api/ideas"), get("/api/households"), get("/api/giver-names")]);
  if (!ctx.alive()) return;
  const choosable = choosablePeople().filter(p => !p.is_user);
  view.innerHTML = renderIdeasPage({ ideas, people: state.people, choosable, occasions: state.occasions, personId, names });
  const choices = assignChoices(households, state.people);

  const run = async (action, done) => {
    try {
      await action();
    } catch (err) {
      toast(err.locked ? "Locked. Unlock, then try again." : err.message);
      return false;
    }
    if (done) toast(done);
    ctx.rerender();
    return true;
  };

  const copy = async kind => {
    const ids = kind === "person" ? [personId] : kind === "kids" ? kidsIds(state.people) : null;
    let text;
    try {
      ({ text } = await get(shareQuery(ids)));
    } catch (err) {
      if (!err.locked) toast(err.message);
      return;
    }
    if (!text) toast("Nothing shared and open to copy.");
    else await copyList(text, view.querySelector("[data-copy-box]"));
  };

  view.addEventListener("click", async e => {
    const t = e.target;
    const take = t.closest?.("[data-take]");
    if (take) {
      const slot = view.querySelector(`[data-take-slot="${take.dataset.take}-${take.dataset.person}"]`);
      if (slot.innerHTML) {
        slot.innerHTML = "";
        return;
      }
      slot.innerHTML = renderTakeForm(take.dataset.take, take.dataset.person, choices, state.occasions, currentOccasion()?.id ?? "");
      slot.querySelector("select[name=giver]").focus();
      return;
    }
    if (t.closest?.("[data-cancel-take]")) {
      t.closest(".slot").innerHTML = "";
      return;
    }
    const copyBtn = t.closest?.("[data-copy]");
    if (copyBtn) {
      await copy(copyBtn.dataset.copy);
      return;
    }
    const del = t.closest?.("[data-delete]");
    if (del && globalThis.confirm("Delete this idea?")) await run(() => send("DELETE", `/api/ideas/${del.dataset.delete}`), "Deleted");
  });

  view.addEventListener("change", async e => {
    const t = e.target;
    if (t.id === "idea-person") location.hash = t.value ? `#/ideas?person=${t.value}` : "#/ideas";
    else if (t.matches?.("input[data-share]")) {
      const on = t.checked;
      if (!await run(() => send("PATCH", `/api/ideas/${t.dataset.share}`, { share: on }), on ? "Shared" : "Not shared")) t.checked = !on;
    }
  });

  view.addEventListener("submit", async e => {
    e.preventDefault();
    const form = e.target;
    const error = form.querySelector(".form-error");
    if (form.dataset.form === "idea-edit") {
      const body = editBody(form);
      if (!body.person_ids.length && !globalThis.confirm("No one is ticked under For. Delete this idea?")) return;
      await run(() => send("PATCH", `/api/ideas/${form.dataset.idea}`, body), body.person_ids.length ? "Saved" : "Deleted");
    }
    if (form.dataset.form === "take") {
      const body = takeBody(form.elements.giver.value, form.elements.giver_name.value, form.elements.occasion.value);
      if (!body.giver_ids.length && !body.giver_name) {
        error.textContent = "Say who is giving it.";
        return;
      }
      const button = form.querySelector("button[type=submit]");
      button.disabled = true;
      error.textContent = "";
      try {
        await send("POST", `/api/ideas/${form.dataset.idea}/take/${form.dataset.person}`, body);
      } catch (err) {
        button.disabled = false;
        error.textContent = err.locked ? "Locked. Unlock, then try again." : err.message;
        return;
      }
      toast("Taken");
      ctx.rerender();
    }
  });
}
