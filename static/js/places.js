// #/places  Where things ship and where they hide. Addresses are labels ("Home", "Grandparents' house"), never street
// addresses; exactly one is home, and a ship-to that is not home gets a warning on the gift. Hiding places are a tree
// of any depth (Garage > Top shelf > Blue bin). School mode has no hiding places, so only addresses show there.
// Every change is sent at once and the screen is drawn again from the server.
import { get, send } from "./api.js";
import { esc, renderOptions, state, toast } from "./core.js";
import { isSchool } from "./mode.js";

// The places a location can be moved under: everything except itself and what is below it (the server refuses a
// location that would become its own ancestor; this keeps the menu from offering one).
export function parentChoices(locations, id) {
  const byId = new Map(locations.map(l => [l.id, l]));
  const below = l => {
    for (let cur = l, hops = 0; cur && hops < 1000; cur = byId.get(cur.parent_id), hops += 1) {
      if (cur.id === id) return true;
    }
    return false;
  };
  return [{ value: "", label: "Top level" }, ...(id === null ? locations : locations.filter(l => !below(l))).map(l => ({ value: l.id, label: l.path }))];
}

const renderHomeBadge = a => (a.is_home ? ` <span class="badge">home</span>` : "");
const renderMakeHome = a => (a.is_home ? "" : `<button type="button" class="btn small" data-act="make-home" data-id="${esc(a.id)}">Make home</button>`);
const renderDeleteAddress = a => (a.is_home ? "" : `<button type="button" class="btn small danger" data-act="delete-address" data-id="${esc(a.id)}">Delete</button>`);

const renderAddressRow = a => `<li class="row">
  <div class="main"><div class="name">${esc(a.label)}${renderHomeBadge(a)}</div></div>
  <div class="row-actions"><button type="button" class="btn small" data-act="rename-address" data-id="${esc(a.id)}" data-label="${esc(a.label)}">Rename</button>${renderMakeHome(a)}${renderDeleteAddress(a)}</div>
</li>`;
const renderAddressRows = list => list.map(renderAddressRow).join("");

export const renderAddresses = list => `<section class="card"><h3>Ship-to addresses</h3>
  <p class="muted small">An order that ships to anything but Home shows a warning until you say it is meant to.</p>
  <ul class="rows">${renderAddressRows(list)}</ul>
  <form class="inline-form" data-form="address-add"><label class="field"><span>New address label</span><input name="label" type="text" maxlength="100" required></label>
    <button class="btn" type="submit">Add</button><p class="form-error" role="alert"></p></form>
</section>`;

const renderMove = (l, list) => `<select class="assign" data-act="move" data-id="${esc(l.id)}" aria-label="Move ${esc(l.name)}">${renderOptions(parentChoices(list, l.id), l.parent_id)}</select>`;
const renderLocationRow = (l, list) => `<li class="row">
  <div class="main"><div class="name">${esc(l.path)}</div></div>
  <div class="row-actions"><button type="button" class="btn small" data-act="rename-location" data-id="${esc(l.id)}" data-label="${esc(l.name)}">Rename</button>${renderMove(l, list)}<button type="button" class="btn small danger" data-act="delete-location" data-id="${esc(l.id)}">Delete</button></div>
</li>`;
const renderLocationRows = list => list.map(l => renderLocationRow(l, list)).join("");

export const renderLocations = list => `<section class="card"><h3>Hiding places</h3>
  <p class="muted small">Rooms, closets, shelves and bins, as deep as you like. A place with a gift in it cannot be deleted.</p>
  <ul class="rows">${renderLocationRows(list)}</ul>
  <form class="fields" data-form="location-add">
    <div class="two"><label class="field"><span>New place</span><input name="name" type="text" maxlength="100" required></label>
      <label class="field"><span>Inside</span><select name="parent_id">${renderOptions(parentChoices(list, null), "")}</select></label></div>
    <p class="form-error" role="alert"></p>
    <div class="actions"><button class="btn" type="submit">Add</button></div>
  </form>
</section>`;

const renderLocationsUnlessSchool = (locations, school) => (school ? "" : renderLocations(locations));
export const renderPlaces = (addresses, locations, school) => `<a class="back" href="#/">&larr; Home</a>
  <div class="head"><h2>Places</h2></div>
  <div class="cols"><div class="col">${renderAddresses(addresses)}</div><div class="col">${renderLocationsUnlessSchool(locations, school)}</div></div>`;

const asId = el => Number(el?.dataset.id);

export async function render(ctx) {
  const { view } = ctx;
  const school = isSchool(state.me);
  const [addresses, locations] = await Promise.all([get("/api/addresses"), school ? Promise.resolve([]) : get("/api/locations")]);
  if (!ctx.alive()) return;
  view.innerHTML = renderPlaces(addresses, locations, school);

  const run = async (action, done) => {
    try {
      await action();
    } catch (err) {
      toast(err.locked ? "Locked. Unlock, then try again." : err.message);
      return;
    }
    if (done) toast(done);
    ctx.rerender();
  };

  view.addEventListener("click", async e => {
    const btn = e.target.closest?.("[data-act]");
    const act = btn?.dataset.act;
    if (act === "make-home") await run(() => send("PATCH", `/api/addresses/${asId(btn)}`, { is_home: true }), "Home changed");
    if (act === "rename-address") {
      const label = globalThis.prompt("Label", btn.dataset.label);
      if (label !== null && label.trim()) await run(() => send("PATCH", `/api/addresses/${asId(btn)}`, { label }), "Saved");
    }
    if (act === "delete-address" && globalThis.confirm("Delete this address?")) {
      await run(() => send("DELETE", `/api/addresses/${asId(btn)}`), "Deleted");
    }
    if (act === "rename-location") {
      const name = globalThis.prompt("Name", btn.dataset.label);
      if (name !== null && name.trim()) await run(() => send("PATCH", `/api/locations/${asId(btn)}`, { name }), "Saved");
    }
    if (act === "delete-location" && globalThis.confirm("Delete this place and everything inside it?")) {
      await run(() => send("DELETE", `/api/locations/${asId(btn)}`), "Deleted");
    }
  });

  view.addEventListener("change", async e => {
    const sel = e.target.closest?.('select[data-act="move"]');
    if (!sel) return;
    await run(() => send("PATCH", `/api/locations/${asId(sel)}`, { parent_id: sel.value === "" ? null : Number(sel.value) }), "Moved");
  });

  view.addEventListener("submit", async e => {
    e.preventDefault();
    const form = e.target;
    const kind = form.dataset?.form;
    if (kind === "address-add") await run(() => send("POST", "/api/addresses", { label: form.elements.label.value }), "Added");
    if (kind === "location-add") {
      const parent = form.elements.parent_id.value;
      await run(() => send("POST", "/api/locations", { name: form.elements.name.value, parent_id: parent === "" ? null : Number(parent) }), "Added");
    }
  });
}
