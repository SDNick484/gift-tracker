// #/gift/<id>  Gift detail: title, recipients, givers, status steps as buttons, Santa (only when the server sent
// the flag, which it does only while unlocked, and only for a gift from an owner), link, store, price, notes.
// Status and Santa apply the moment they are tapped; the fields below save together. In a family instance From also
// takes a typed name ("Someone else", with the names used before as suggestions), a gift taken from an idea can go
// back to ideas, and an old gift with no giver can move to ideas. School gift JSON has neither `giver_name` nor
// `from_idea`, so none of that is drawn there.
import { get, send } from "./api.js";
import {
  centsToText, esc, giverText, isUnlocked, money, renderChecked, renderDisabled, renderOptions, state, statusSteps, toast,
} from "./core.js";
import { isSchool } from "./mode.js";

// Santa is only for a gift from an owner, and only visible to an unlocked request (the key is absent otherwise).
export function canSanta(gift, people) {
  const owners = new Set(people.filter(p => p.is_user).map(p => p.id));
  return typeof gift.santa === "boolean" && gift.track === "ours" && gift.giver_ids.some(id => owners.has(id));
}

// Who can be ticked: active people (protected ones only while unlocked), plus anyone already on the gift. Owners
// are not offered as recipients: gifts between the owners stay off the list.
export function pickList(people, selectedIds, { unlocked, recipients }) {
  return people.filter(p => selectedIds.includes(p.id)
    || (p.active && (unlocked || !p.protected) && !(recipients && p.is_user)));
}

// The PATCH body from the form's values. Throws an Error with a sentence to show when something is missing. The typed
// giver is sent only when the form has the box (never in school mode); a cleared box clears it (null).
export function buildPatch(f) {
  const title = f.title.trim();
  if (!title) throw new Error("A gift needs a title.");
  if (!f.recipientIds.length) throw new Error("Pick at least one recipient.");
  const body = {
    title, url: f.url.trim(), store: f.store.trim(), price: f.price.trim(), notes: f.notes.trim(),
    recipient_ids: f.recipientIds, giver_ids: f.giverIds,
  };
  if (typeof f.giverName === "string") body.giver_name = f.giverName.trim() || null;
  return body;
}

// ---------------------------------------------------------------- order card (seller, ship-to, dates, hiding place)
const idOrNull = v => (v === "" || v === null || v === undefined ? null : Number(v));
const orderFields = f => ({
  seller: (f.seller ?? "").trim(), order_number: (f.order_number ?? "").trim(), ship_to_id: idOrNull(f.ship_to_id),
  ship_elsewhere_ok: Boolean(f.ship_elsewhere_ok), eta: f.eta ?? "", arrived_on: f.arrived_on ?? "",
});

// The PATCH body for the order card. The hiding place is left out in school mode (the server refuses it there).
export function orderPayload(f, school = false) {
  const body = orderFields(f);
  if (!school) body.location_id = idOrNull(f.location_id);
  return body;
}

// The POST /status body. Ordered carries the form's order fields (the server needs seller, ship-to and date, from
// here or already saved); arrived carries them so the arrival date can be typed; anything else is the status alone.
export function statusBody(status, f, school = false) {
  if (!f || (status !== "ordered" && status !== "arrived")) return { status };
  const { location_id: _place, ...fields } = orderPayload(f, school);
  return { status, ...fields };
}

const addressLabel = (addresses, id) => addresses.find(a => a.id === id)?.label ?? "";
export const addressChoices = addresses => [{ value: "", label: "Pick an address" },
  ...addresses.map(a => ({ value: a.id, label: a.is_home ? `${a.label} (home)` : a.label }))];
export const locationChoices = locations => [{ value: "", label: "Not hidden yet" }, ...locations.map(l => ({ value: l.id, label: l.path }))];

const renderShipWarning = (gift, addresses) => (gift.ship_warning
  ? `<p class="warn-note" role="alert">Ships to ${esc(addressLabel(addresses, gift.ship_to_id))}, not home. Cancel or change it while you still can, or tick "Meant to ship there".</p>`
  : "");
const renderOrderedBy = gift => (gift.ordered_by ? `<p class="muted small">Ordered by ${esc(gift.ordered_by)}</p>` : "");
// What agent tokens wrote on the gift (newest first), so a change nobody made by hand can be traced to its token.
const agentDay = at => (Number.isInteger(at) ? new Date(at * 1000).toISOString().slice(0, 10) : "");
export const agentNote = n => [n.token, n.action, agentDay(n.at)].join(" · ");
const renderAgentNote = n => `<li>${esc(agentNote(n))}</li>`;
const renderAgentNotes = notes => notes.map(renderAgentNote).join("");
export const renderAgentLog = gift => (gift.agent_log?.length
  ? `<div class="muted small"><p>Agent notes</p><ul class="agent-log">${renderAgentNotes(gift.agent_log)}</ul></div>`
  : "");
const renderLocationField = (gift, locations, school) => (school ? "" : `<label class="field"><span>Hidden</span><select name="location_id">${renderOptions(locationChoices(locations), gift.location_id)}</select></label>`);

export const renderOrder = (gift, addresses, locations, school) => `
  <form id="order" class="card fields"><h3>Bought</h3>
    <div class="two">
      <label class="field"><span>Seller</span><input name="seller" type="text" maxlength="200" value="${esc(gift.seller ?? "")}"></label>
      <label class="field"><span>Order number</span><input name="order_number" type="text" maxlength="100" value="${esc(gift.order_number ?? "")}"></label>
    </div>
    <label class="field"><span>Ship to</span><select name="ship_to_id">${renderOptions(addressChoices(addresses), gift.ship_to_id)}</select></label>
    <label class="check"><input type="checkbox" name="ship_elsewhere_ok"${renderChecked(gift.ship_elsewhere_ok)}> Meant to ship there <span class="muted small">(no warning)</span></label>
    ${renderShipWarning(gift, addresses)}
    <div class="two">
      <label class="field"><span>Expected</span><input name="eta" type="date" value="${esc(gift.eta ?? "")}"></label>
      <label class="field"><span>Arrived</span><input name="arrived_on" type="date" value="${esc(gift.arrived_on ?? "")}"></label>
    </div>
    ${renderLocationField(gift, locations, school)}
    ${renderOrderedBy(gift)}
    ${renderAgentLog(gift)}
    <p class="form-error" id="orderError" role="alert"></p>
    <div class="actions"><button class="btn primary" type="submit">Save order details</button></div>
  </form>`;

// The order card is for gifts from an owner (a gift someone else gives is never ordered by us) and only when the
// caller supplied the addresses and places.
export const renderOrderBox = (gift, places) => (places && gift.track === "ours"
  ? renderOrder(gift, places.addresses, places.locations, places.school)
  : "");

export function stepClass(index, current) {
  return index < current ? "step done" : "step";
}

const renderStep = (s, i, gift, current) => `<button type="button" class="${esc(stepClass(i, current))}" data-status="${esc(s)}" aria-pressed="${esc(String(s === gift.status))}">${esc(s)}</button>`;
export const renderSteps = gift => {
  const steps = statusSteps(gift.track);
  const current = steps.indexOf(gift.status);
  return steps.map((s, i) => renderStep(s, i, gift, current)).join("");
};
const renderTold = gift => (gift.track === "theirs" && gift.told_at ? `<p class="muted small">Told ${esc(gift.told_at)}</p>` : "");

const renderCheck = (name, p, on) => `<label class="check"><input type="checkbox" name="${esc(name)}" value="${esc(p.id)}"${renderChecked(on)}> ${esc(p.name)}</label>`;
const renderChecks = (name, list, selected) => list.map(p => renderCheck(name, p, selected.includes(p.id))).join("");

// The typed giver box (family gift JSON has the `giver_name` key) and the suggestions it offers.
const renderNameOption = n => `<option value="${esc(n)}"></option>`;
const renderNameOptions = names => names.map(renderNameOption).join("");
const renderGiverName = (gift, names) => (gift.giver_name === undefined ? "" : `
        <label class="field"><span>Someone else</span><input name="giver_name" type="text" maxlength="80" list="giver-names" value="${esc(gift.giver_name ?? "")}" placeholder="A name"></label>
        <datalist id="giver-names">${renderNameOptions(names)}</datalist>`);

// Back to ideas for a gift taken from an idea; Move to ideas for an old gift with nobody giving it, still at the idea
// status (the server refuses one further along: an idea keeps no status or order). Family only.
const renderMoves = gift => {
  if (typeof gift.from_idea !== "boolean") return "";
  if (gift.from_idea) return `<button class="btn" type="button" id="back-to-ideas">Back to ideas</button>`;
  return gift.giver_ids.length || gift.giver_name || gift.status !== "idea"
    ? "" : `<button class="btn" type="button" id="to-idea">Move to ideas</button>`;
};

const renderSantaBox = gift => (typeof gift.santa === "boolean"
  ? `<label class="check"><input type="checkbox" id="santa"${renderChecked(gift.santa)}${renderDisabled(!gift.canSanta)}> From Santa <span class="muted small">(a gift from us only)</span></label>`
  : "");

export function renderPage(gift, people, occasionName, unlocked, places = null, names = []) {
  const view = { ...gift, canSanta: canSanta(gift, people) };
  const recipients = pickList(people, gift.recipient_ids, { unlocked, recipients: true });
  const givers = pickList(people, gift.giver_ids, { unlocked, recipients: false });
  const nameList = ids => ids.map(id => people.find(p => p.id === id)?.name).filter(Boolean).join(" & ");
  return `
  <a class="back" href="${esc(state.prev || "#/")}">&larr; Back</a>
  <div class="head"><div><h2>${esc(gift.title)}</h2>
    <span class="sub">${esc(occasionName)} · to ${esc(nameList(gift.recipient_ids))}${renderFromSub(gift, people)}</span></div>
    <span class="amt">${esc(money(gift.price_cents))}</span></div>
  <div class="cols">
    <div class="col">
      <section class="card"><h3>Status</h3>
        <div id="steps" class="steps" role="group" aria-label="Status">${renderSteps(view)}</div>
        ${renderTold(gift)}
        <div id="santaBox">${renderSantaBox(view)}</div>
        <p class="form-error" id="statusError" role="alert"></p>
      </section>
      <div id="orderBox">${renderOrderBox(gift, places)}</div>
    </div>
    <div class="col">
      <form id="fields" class="card fields">
        <label class="field"><span>Title</span><input name="title" type="text" maxlength="200" value="${esc(gift.title)}" required></label>
        <div class="field"><span>For</span><div class="check-list">${renderChecks("recipient", recipients, gift.recipient_ids)}</div></div>
        <div class="field"><span>From</span><div class="check-list">${renderChecks("giver", givers, gift.giver_ids)}</div></div>${renderGiverName(gift, names)}
        <label class="field"><span>Link</span><input name="url" type="url" inputmode="url" maxlength="2000" value="${esc(gift.url ?? "")}"></label>
        <div class="two">
          <label class="field"><span>Store</span><input name="store" type="text" maxlength="200" value="${esc(gift.store ?? "")}"></label>
          <label class="field"><span>Price</span><input name="price" type="text" inputmode="decimal" value="${esc(centsToText(gift.price_cents))}"></label>
        </div>
        <label class="field"><span>Notes</span><textarea name="notes" maxlength="2000">${esc(gift.notes ?? "")}</textarea></label>
        <p class="form-error" id="formError" role="alert"></p>
        <div class="actions"><button class="btn primary" type="submit">Save</button><span class="spacer"></span>${renderMoves(gift)}<button class="btn danger" type="button" id="delete">Delete</button></div>
      </form>
    </div>
  </div>`;
}
const renderFromSub = (gift, people) => (giverText(gift, people) ? ` · from ${esc(giverText(gift, people))}` : "");

const renderNotFound = () => `<div class="notice"><p>No such gift.</p><p><a class="btn" href="#/">Home</a></p></div>`;

const checked = (form, name) => [...form.querySelectorAll(`input[name="${name}"]:checked`)].map(i => Number(i.value));

export async function render(ctx) {
  const { view } = ctx;
  let gift, addresses, locations, names;
  const school = isSchool(state.me);
  try {
    [gift, addresses, locations, names] = await Promise.all([get(`/api/gifts/${Number(ctx.parts[1])}`), get("/api/addresses"),
      school ? Promise.resolve([]) : get("/api/locations"),
      school ? Promise.resolve([]) : get("/api/giver-names").catch(() => [])]); // suggestions only: never in the way
  } catch (e) {
    if (e.status !== 404) throw e;
    view.innerHTML = renderNotFound();
    return;
  }
  if (!ctx.alive()) return;
  const occasionName = state.occasions.find(o => o.id === gift.occasion_id)?.name ?? "";
  const places = { addresses, locations, school };
  view.innerHTML = renderPage(gift, state.people, occasionName, isUnlocked(), places, Array.isArray(names) ? names : []);
  const people = state.people;

  const orderBox = view.querySelector("#orderBox");
  const orderValues = () => {
    const form = view.querySelector("#order");
    if (!form) return null; // a gift someone else gives has no order card
    const el = form.elements;
    return { seller: el.seller.value, order_number: el.order_number.value, ship_to_id: el.ship_to_id.value,
      ship_elsewhere_ok: el.ship_elsewhere_ok.checked, eta: el.eta.value, arrived_on: el.arrived_on.value,
      location_id: el.location_id?.value ?? "" };
  };
  const refreshOrder = g => {
    const keep = view.querySelector("#order")?.elements.location_id?.value; // an unsaved pick of the hiding place
    orderBox.innerHTML = renderOrderBox(g, places);
    const select = view.querySelector("#order")?.elements.location_id;
    if (select && keep !== undefined && g.location_id === null) select.value = keep;
  };

  const refreshStatus = g => {
    gift = g;
    const withFlag = { ...g, canSanta: canSanta(g, people) };
    view.querySelector("#steps").innerHTML = renderSteps(withFlag);
    view.querySelector("#santaBox").innerHTML = renderSantaBox(withFlag);
  };
  const statusError = view.querySelector("#statusError");

  view.querySelector("#steps").addEventListener("click", async e => {
    const status = e.target.closest("[data-status]")?.dataset.status;
    if (!status || status === gift.status) return;
    statusError.textContent = "";
    try {
      const updated = await send("POST", `/api/gifts/${gift.id}/status`, statusBody(status, orderValues(), school));
      refreshStatus(updated);
      if (status === "ordered" || status === "arrived") refreshOrder(updated); // ordered_by, the arrival date
    } catch (err) {
      statusError.textContent = err.message;
    }
  });

  view.querySelector("#santaBox").addEventListener("change", async e => {
    if (e.target.id !== "santa") return;
    statusError.textContent = "";
    try {
      refreshStatus(await send("PATCH", `/api/gifts/${gift.id}`, { santa: e.target.checked }));
    } catch (err) {
      e.target.checked = !e.target.checked;
      statusError.textContent = err.locked ? "Locked. Unlock to change Santa." : err.message;
    }
  });

  orderBox.addEventListener("submit", async e => {
    e.preventDefault();
    const error = view.querySelector("#orderError");
    error.textContent = "";
    try {
      gift = await send("PATCH", `/api/gifts/${gift.id}`, orderPayload(orderValues(), school));
    } catch (err) {
      error.textContent = err.locked ? "Locked. Unlock, then save again." : err.message;
      return;
    }
    refreshOrder(gift);
    refreshStatus(gift);
    toast("Saved");
  });

  const form = view.querySelector("#fields");
  const formError = view.querySelector("#formError");
  form.addEventListener("submit", async e => {
    e.preventDefault();
    formError.textContent = "";
    let body;
    try {
      body = buildPatch({
        title: form.elements.title.value, url: form.elements.url.value, store: form.elements.store.value,
        price: form.elements.price.value, notes: form.elements.notes.value,
        recipientIds: checked(form, "recipient"), giverIds: checked(form, "giver"), giverName: form.elements.giver_name?.value,
      });
      await send("PATCH", `/api/gifts/${gift.id}`, body);
    } catch (err) {
      formError.textContent = err.locked ? "Locked. Unlock, then save again." : err.message;
      return;
    }
    toast("Saved");
    ctx.rerender();
  });

  view.querySelector("#delete").addEventListener("click", async () => {
    if (!globalThis.confirm("Delete this gift?")) return;
    try {
      await send("DELETE", `/api/gifts/${gift.id}`);
    } catch (err) {
      formError.textContent = err.message;
      return;
    }
    toast("Deleted");
    location.hash = state.prev || "#/";
  });

  // Back to ideas deletes the gift and reopens the idea's copy; Move to ideas turns the gift into an idea.
  const move = async (path, done) => {
    formError.textContent = "";
    try {
      await send("POST", `/api/gifts/${gift.id}/${path}`, {});
    } catch (err) {
      formError.textContent = err.locked ? "Locked. Unlock, then try again." : err.message;
      return;
    }
    toast(done);
    location.hash = state.prev || "#/";
  };
  view.querySelector("#back-to-ideas")?.addEventListener("click", () => move("back-to-ideas", "Back in ideas"));
  view.querySelector("#to-idea")?.addEventListener("click", () => move("to-idea", "Moved to ideas"));
}
