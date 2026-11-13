// #/idea  Quick idea: For (people, most recently used first), What, From (each owner, or both). Two fields and
// one tap: For starts on the person used last, From on both, and Save goes back to where the user came from.
// Link, price and store are added later on the gift's own screen.
import { send } from "./api.js";
import { choosablePeople, currentOccasion, esc, owners, recents, renderChecked, renderOptions, state, toast } from "./core.js";

// People for the For picker: owners are left out (gifts between the owners are off the list), people used
// recently come first in the order they were used, everyone else after in their own order.
export function forChoices(people, recentIds) {
  const pool = people.filter(p => !p.is_user);
  const byId = new Map(pool.map(p => [p.id, p]));
  const first = recentIds.map(id => byId.get(id)).filter(Boolean);
  const seen = new Set(first.map(p => p.id));
  return [...first, ...pool.filter(p => !seen.has(p.id))].map(p => ({ value: p.id, label: p.name }));
}

// The From choices: each owner (the signed-in one first, marked), then both. The last one is the default.
export function fromChoices(ownerPeople, myPersonId) {
  const mine = ownerPeople.filter(o => o.id === myPersonId), rest = ownerPeople.filter(o => o.id !== myPersonId);
  const choices = [...mine, ...rest].map(o => ({ value: String(o.id), label: o.id === myPersonId ? `${o.name} (me)` : o.name, ids: [o.id] }));
  if (ownerPeople.length > 1) {
    choices.push({ value: ownerPeople.map(o => o.id).join(","), label: "Both", ids: ownerPeople.map(o => o.id) });
  }
  return choices;
}

// Where Save returns to: the screen the user came from, unless that was this one.
export function backTarget(prev) {
  return prev && !prev.startsWith("#/idea") ? prev : "#/";
}

const renderFrom = (choices, selected) => choices.map(c => `<label><input type="radio" name="from" value="${esc(c.value)}"${renderChecked(c.value === selected)}><span>${esc(c.label)}</span></label>`).join("");

const renderForm = (occasion, forList, fromList, preselect) => `
  <div class="head"><h2>New idea</h2><span class="sub">${esc(occasion.name)}</span></div>
  <form id="idea" class="card fields" autocomplete="off">
    <label class="field"><span>For</span><select name="for" required>${renderOptions(forList, preselect)}</select></label>
    <label class="field"><span>What</span><input name="what" type="text" maxlength="200" required autocapitalize="sentences" enterkeyhint="done"></label>
    <div class="field"><span>From</span><div class="seg">${renderFrom(fromList, fromList.at(-1).value)}</div></div>
    <p class="form-error" role="alert"></p>
    <div class="actions"><button class="btn primary" type="submit">Save</button><a class="btn" href="${esc(backTarget(state.prev))}">Cancel</a></div>
  </form>`;

const renderNothing = message => `<div class="notice"><p>${esc(message)}</p><p><a class="btn primary" href="#/settings">Open Settings</a></p></div>`;

export async function render(ctx) {
  const { view } = ctx;
  const occasion = currentOccasion();
  if (!occasion) {
    view.innerHTML = renderNothing("Create an occasion first.");
    return;
  }
  const forList = forChoices(choosablePeople(), recents.get());
  const fromList = fromChoices(owners(), state.me.person_id);
  if (!forList.length || !fromList.length) {
    view.innerHTML = renderNothing("Add the people you buy for first.");
    return;
  }
  const preselect = ctx.query.for && forList.some(c => String(c.value) === ctx.query.for) ? ctx.query.for : forList[0].value;
  view.innerHTML = renderForm(occasion, forList, fromList, preselect);
  const form = view.querySelector("#idea");
  const error = form.querySelector(".form-error");
  const save = form.querySelector("button[type=submit]");
  form.elements.what.focus();
  form.addEventListener("submit", async e => {
    e.preventDefault();
    const title = form.elements.what.value.trim();
    if (!title) {
      error.textContent = "Say what the idea is.";
      return;
    }
    const from = fromList.find(c => c.value === form.elements.from.value) ?? fromList.at(-1);
    const forId = Number(form.elements.for.value);
    save.disabled = true;
    error.textContent = "";
    try {
      await send("POST", "/api/gifts", { occasion_id: occasion.id, title, recipient_ids: [forId], giver_ids: from.ids });
    } catch (err) {
      save.disabled = false;
      error.textContent = err.locked ? "Locked. Unlock, then save again." : err.message;
      return;
    }
    recents.add(forId);
    toast("Saved");
    location.hash = backTarget(state.prev);
  });
}
