// #/gift/<id>  Gift detail: title, recipients, givers, status steps as buttons, Santa (only when the server sent
// the flag, which it does only while unlocked, and only for a gift from an owner), link, store, price, notes.
// Status and Santa apply the moment they are tapped; the fields below save together.
import { get, send } from "./api.js";
import {
  centsToText, esc, giverLabel, isUnlocked, money, renderChecked, renderDisabled, state, statusSteps, toast,
} from "./core.js";

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

// The PATCH body from the form's values. Throws an Error with a sentence to show when something is missing.
export function buildPatch(f) {
  const title = f.title.trim();
  if (!title) throw new Error("A gift needs a title.");
  if (!f.recipientIds.length) throw new Error("Pick at least one recipient.");
  return {
    title, url: f.url.trim(), store: f.store.trim(), price: f.price.trim(), notes: f.notes.trim(),
    recipient_ids: f.recipientIds, giver_ids: f.giverIds,
  };
}

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

const renderSantaBox = gift => (typeof gift.santa === "boolean"
  ? `<label class="check"><input type="checkbox" id="santa"${renderChecked(gift.santa)}${renderDisabled(!gift.canSanta)}> From Santa <span class="muted small">(a gift from us only)</span></label>`
  : "");

export function renderPage(gift, people, occasionName, unlocked) {
  const view = { ...gift, canSanta: canSanta(gift, people) };
  const recipients = pickList(people, gift.recipient_ids, { unlocked, recipients: true });
  const givers = pickList(people, gift.giver_ids, { unlocked, recipients: false });
  const names = ids => ids.map(id => people.find(p => p.id === id)?.name).filter(Boolean).join(" & ");
  return `
  <a class="back" href="${esc(state.prev || "#/")}">&larr; Back</a>
  <div class="head"><div><h2>${esc(gift.title)}</h2>
    <span class="sub">${esc(occasionName)} · to ${esc(names(gift.recipient_ids))}${renderFromSub(gift, people)}</span></div>
    <span class="amt">${esc(money(gift.price_cents))}</span></div>
  <div class="cols">
    <div class="col">
      <section class="card"><h3>Status</h3>
        <div id="steps" class="steps" role="group" aria-label="Status">${renderSteps(view)}</div>
        ${renderTold(gift)}
        <div id="santaBox">${renderSantaBox(view)}</div>
        <p class="form-error" id="statusError" role="alert"></p>
      </section>
    </div>
    <div class="col">
      <form id="fields" class="card fields">
        <label class="field"><span>Title</span><input name="title" type="text" maxlength="200" value="${esc(gift.title)}" required></label>
        <div class="field"><span>For</span><div class="check-list">${renderChecks("recipient", recipients, gift.recipient_ids)}</div></div>
        <div class="field"><span>From</span><div class="check-list">${renderChecks("giver", givers, gift.giver_ids)}</div></div>
        <label class="field"><span>Link</span><input name="url" type="url" inputmode="url" maxlength="2000" value="${esc(gift.url ?? "")}"></label>
        <div class="two">
          <label class="field"><span>Store</span><input name="store" type="text" maxlength="200" value="${esc(gift.store ?? "")}"></label>
          <label class="field"><span>Price</span><input name="price" type="text" inputmode="decimal" value="${esc(centsToText(gift.price_cents))}"></label>
        </div>
        <label class="field"><span>Notes</span><textarea name="notes" maxlength="2000">${esc(gift.notes ?? "")}</textarea></label>
        <p class="form-error" id="formError" role="alert"></p>
        <div class="actions"><button class="btn primary" type="submit">Save</button><span class="spacer"></span><button class="btn danger" type="button" id="delete">Delete</button></div>
      </form>
    </div>
  </div>`;
}
const renderFromSub = (gift, people) => (gift.giver_ids.length ? ` · from ${esc(giverLabel(gift.giver_ids, people))}` : "");

const renderNotFound = () => `<div class="notice"><p>No such gift.</p><p><a class="btn" href="#/">Home</a></p></div>`;

const checked = (form, name) => [...form.querySelectorAll(`input[name="${name}"]:checked`)].map(i => Number(i.value));

export async function render(ctx) {
  const { view } = ctx;
  let gift;
  try {
    gift = await get(`/api/gifts/${Number(ctx.parts[1])}`);
  } catch (e) {
    if (e.status !== 404) throw e;
    view.innerHTML = renderNotFound();
    return;
  }
  if (!ctx.alive()) return;
  const occasionName = state.occasions.find(o => o.id === gift.occasion_id)?.name ?? "";
  view.innerHTML = renderPage(gift, state.people, occasionName, isUnlocked());
  const people = state.people;

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
      refreshStatus(await send("POST", `/api/gifts/${gift.id}/status`, { status }));
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
        recipientIds: checked(form, "recipient"), giverIds: checked(form, "giver"),
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
}
