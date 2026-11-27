// #/idea  Quick idea: For (people, most recently used first; tick one or several), What, then an optional Link,
// Store and Price, and "Share with relatives" (off). An idea has no occasion and no giver: it is taken for one person later,
// on #/ideas or a board. Save goes back to where the user came from.
import { send } from "./api.js";
import { choosablePeople, esc, recents, renderChecked, state, toast } from "./core.js";

// People for the For picker: owners are left out (gifts between the owners are off the list), people used
// recently come first in the order they were used, everyone else after in their own order.
export function forChoices(people, recentIds) {
  const pool = people.filter(p => !p.is_user);
  const byId = new Map(pool.map(p => [p.id, p]));
  const first = recentIds.map(id => byId.get(id)).filter(Boolean);
  const seen = new Set(first.map(p => p.id));
  return [...first, ...pool.filter(p => !seen.has(p.id))].map(p => ({ value: p.id, label: p.name }));
}

// Where Save returns to: the screen the user came from, unless that was this one.
export function backTarget(prev) {
  return prev && !prev.startsWith("#/idea") ? prev : "#/";
}

// The POST /api/ideas body from the form: every checked person, the title, link, store and price when typed, and share.
export function ideaBody(form) {
  const field = name => (form.elements[name]?.value ?? "").trim();
  const body = { title: field("what"), person_ids: [...form.querySelectorAll('input[name="for"]:checked')].map(i => Number(i.value)) };
  if (field("url")) body.url = field("url");
  if (field("store")) body.store = field("store");
  if (field("price")) body.price = field("price");
  body.share = Boolean(form.elements.share?.checked);
  return body;
}

const renderFor = (choices, selected) => choices.map(c => `<label class="check"><input type="checkbox" name="for" value="${esc(c.value)}"${renderChecked(String(c.value) === selected)}> ${esc(c.label)}</label>`).join("");

const renderForm = (forList, preselect) => `
  <div class="head"><h2>New idea</h2></div>
  <form id="idea" class="card fields" autocomplete="off">
    <div class="field"><span>For</span><div class="check-list">${renderFor(forList, preselect)}</div></div>
    <label class="field"><span>What</span><input name="what" type="text" maxlength="200" required autocapitalize="sentences" enterkeyhint="next"></label>
    <div class="two"><label class="field"><span>Link</span><input name="url" type="url" maxlength="2000" placeholder="optional" inputmode="url"></label>
      <label class="field"><span>Store</span><input name="store" type="text" maxlength="200" placeholder="optional"></label></div>
    <label class="field"><span>Price</span><input name="price" type="text" inputmode="decimal" placeholder="optional"></label>
    <label class="check"><input type="checkbox" name="share"> Share with relatives</label>
    <p class="form-error" role="alert"></p>
    <div class="actions"><button class="btn primary" type="submit">Save</button><a class="btn" href="${esc(backTarget(state.prev))}">Cancel</a></div>
  </form>`;

const renderNothing = message => `<div class="notice"><p>${esc(message)}</p><p><a class="btn primary" href="#/settings">Open Settings</a></p></div>`;

export async function render(ctx) {
  const { view } = ctx;
  const forList = forChoices(choosablePeople(), recents.get());
  if (!forList.length) {
    view.innerHTML = renderNothing("Add the people you buy for first.");
    return;
  }
  const preselect = ctx.query.for && forList.some(c => String(c.value) === ctx.query.for) ? ctx.query.for : "";
  view.innerHTML = renderForm(forList, preselect);
  const form = view.querySelector("#idea");
  const error = form.querySelector(".form-error");
  const save = form.querySelector("button[type=submit]");
  form.elements.what.focus();
  form.addEventListener("submit", async e => {
    e.preventDefault();
    const body = ideaBody(form);
    if (!body.person_ids.length) {
      error.textContent = "Say who it is for.";
      return;
    }
    if (!body.title) {
      error.textContent = "Say what the idea is.";
      return;
    }
    save.disabled = true;
    error.textContent = "";
    try {
      await send("POST", "/api/ideas", body);
    } catch (err) {
      save.disabled = false;
      error.textContent = err.locked ? "Locked. Unlock, then save again." : err.message;
      return;
    }
    for (const id of [...body.person_ids].reverse()) recents.add(id);
    toast("Saved");
    location.hash = backTarget(state.prev);
  });
}
