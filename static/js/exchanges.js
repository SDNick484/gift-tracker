// #/exchanges  The gift exchanges of the current occasion (Secret Santa, Favorite Things, a staff exchange): name,
// budget, date, which member of this household drew whom, and the gift for each draw. A gift over the budget is
// flagged, never refused. The server leaves protected draws out while locked and answers 423 for a write to one;
// nothing here decides that, it only avoids offering a protected person while locked. Every change is sent at once
// and the screen is drawn again from the server.
import { get, send } from "./api.js";
import { dollarsToCents, esc, isUnlocked, money, renderColor, renderOptions, state, toast } from "./core.js";
import { shortDate } from "./lists.js";

export const budgetText = cents => (Number.isInteger(cents) ? `${money(cents)} limit` : "no limit");

// A drawer is an owner or, once unlocked, a protected person; a recipient is anyone the page may show.
export const drawerChoices = (people, unlocked) => people.filter(p => p.active && (p.is_user || (p.protected && unlocked)));
export const recipientChoices = (people, unlocked, drawerId) => people.filter(p => p.active && p.id !== drawerId && (unlocked || !p.protected));
export const undrawn = (list, people, unlocked) => drawerChoices(people, unlocked).filter(p => !list.draws.some(d => d.drawer_id === p.id));

// The body of POST /api/occasions/<id>/exchanges and PATCH /api/exchanges/<id> from the raw field text. Throws a
// message for a bad amount; an empty amount or date is null.
export function exchangePayload(raw) {
  return { name: (raw.name ?? "").trim(), budget_cents: dollarsToCents(raw.budget), date: (raw.date ?? "").trim() || null };
}

// The body of POST /api/gifts for one draw: the drawer gives, the person drawn receives.
export function giftPayload(occasionId, exchangeId, draw, raw) {
  return { occasion_id: occasionId, title: (raw.title ?? "").trim(), price: (raw.price ?? "").trim() || null,
    recipient_ids: [draw.recipient_id], giver_ids: [draw.drawer_id], exchange_id: exchangeId };
}

const nameOf = (id, people) => people.find(p => p.id === id)?.name ?? "Someone";
const colorOf = (id, people) => people.find(p => p.id === id)?.color;

// ---------------------------------------------------------------- markup
const renderOverBadge = d => (d.over_budget ? ` <span class="badge warn">over budget</span>` : "");
const renderGiftLine = d => (d.gift_id
  ? `<a href="#/gift/${esc(d.gift_id)}">${esc(d.gift_title)}</a> <span class="badge">${esc(d.gift_status)}</span>`
  : `<span class="muted">no gift yet</span>`);
const renderGiftAmount = d => (Number.isInteger(d.price_cents) ? `${esc(money(d.price_cents))}${renderOverBadge(d)}` : "");

const renderAddGift = (ex, d) => (d.gift_id ? "" : `<form class="inline-form" data-form="gift-add" data-exchange="${esc(ex.id)}" data-drawer="${esc(d.drawer_id)}" data-recipient="${esc(d.recipient_id)}">
  <label class="field"><span>Gift</span><input name="title" type="text" maxlength="200" required></label>
  <label class="field"><span>Price</span><input name="price" type="text" inputmode="decimal" placeholder="optional"></label>
  <button class="btn small" type="submit">Add gift</button></form>`);

const renderRecipientSelect = (ex, d, people, unlocked) => `<select class="assign" data-act="redraw" data-exchange="${esc(ex.id)}" data-drawer="${esc(d.drawer_id)}" aria-label="Who ${esc(nameOf(d.drawer_id, people))} drew">${renderOptions(
  recipientChoices(people, unlocked, d.drawer_id).map(p => ({ value: p.id, label: p.name })), d.recipient_id)}</select>`;

export const drawLine = (ex, d, people, unlocked = false) => `<li class="row"${renderColor(colorOf(d.drawer_id, people))}>
  <div class="main"><div class="name">${esc(nameOf(d.drawer_id, people))} drew ${esc(nameOf(d.recipient_id, people))}</div>
    <div class="line">${renderGiftLine(d)}</div></div>
  <div class="amt">${renderGiftAmount(d)}</div>
  <div class="row-actions">${renderRecipientSelect(ex, d, people, unlocked)}<button type="button" class="btn small danger" data-act="remove-draw" data-exchange="${esc(ex.id)}" data-drawer="${esc(d.drawer_id)}">Remove draw</button></div>
  ${renderAddGift(ex, d)}
</li>`;
const renderDraws = (ex, people, unlocked) => ex.draws.map(d => drawLine(ex, d, people, unlocked)).join("");

const renderAddDraw = (ex, people, unlocked) => {
  const drawers = undrawn(ex, people, unlocked);
  if (!drawers.length) return "";
  const first = drawers[0].id;
  return `<form class="inline-form" data-form="draw-add" data-exchange="${esc(ex.id)}">
    <label class="field"><span>Drawer</span><select name="drawer_id">${renderOptions(drawers.map(p => ({ value: p.id, label: p.name })), first)}</select></label>
    <label class="field"><span>Drew</span><select name="recipient_id">${renderOptions(recipientChoices(people, unlocked, null).map(p => ({ value: p.id, label: p.name })), "")}</select></label>
    <button class="btn small" type="submit">Add draw</button></form>`;
};

const renderMeta = ex => `${esc(budgetText(ex.budget_cents))} · ${esc(shortDate(ex.date))}`;

export const renderExchange = (ex, people, unlocked = false) => `<section class="card exchange" data-exchange="${esc(ex.id)}">
  <h3>${esc(ex.name)}</h3><p class="muted small">${renderMeta(ex)}</p>
  <ul class="rows">${renderDraws(ex, people, unlocked)}</ul>
  ${renderAddDraw(ex, people, unlocked)}
  <details><summary>Edit exchange</summary>
    <form class="fields" data-form="exchange-edit" data-exchange="${esc(ex.id)}">
      <div class="two"><label class="field"><span>Name</span><input name="name" type="text" maxlength="100" value="${esc(ex.name)}" required></label>
        <label class="field"><span>Budget</span><input name="budget" type="text" inputmode="decimal" value="${esc(Number.isInteger(ex.budget_cents) ? ex.budget_cents / 100 : "")}"></label></div>
      <label class="field"><span>Date</span><input name="date" type="date" value="${esc(ex.date ?? "")}"></label>
      <p class="form-error" role="alert"></p>
      <div class="actions"><button class="btn" type="submit">Save</button><button type="button" class="btn danger" data-act="delete-exchange" data-exchange="${esc(ex.id)}">Delete exchange</button></div>
    </form></details>
</section>`;

const renderCards = (list, people, unlocked) => list.map(ex => renderExchange(ex, people, unlocked)).join("");

const renderNewExchange = () => `<section class="card"><h3>New exchange</h3>
  <form class="fields" data-form="exchange-add">
    <div class="two"><label class="field"><span>Name</span><input name="name" type="text" maxlength="100" required></label>
      <label class="field"><span>Budget</span><input name="budget" type="text" inputmode="decimal" placeholder="optional"></label></div>
    <label class="field"><span>Date</span><input name="date" type="date"></label>
    <p class="form-error" role="alert"></p>
    <div class="actions"><button class="btn" type="submit">Add exchange</button></div>
  </form></section>`;

const renderEmpty = list => (list.length ? "" : `<p class="empty">No exchange yet.</p>`);

export const renderExchanges = (list, people, occasionName, unlocked = false) => `<a class="back" href="#/">&larr; Home</a>
  <div class="head"><h2>Exchanges</h2><span class="sub">${esc(occasionName)}</span></div>
  ${renderEmpty(list)}
  <div class="cols">${renderCards(list, people, unlocked)}</div>
  ${renderNewExchange()}`;

const renderNoOccasion = () => `<div class="notice"><p>No occasion yet.</p><p><a class="btn primary" href="#/settings">Create one in Settings</a></p></div>`;

const asId = el => Number(el?.dataset.exchange);
const asDrawer = el => Number(el?.dataset.drawer);

export async function render(ctx) {
  const { view } = ctx;
  if (state.occasionId === null) {
    view.innerHTML = renderNoOccasion();
    return;
  }
  const list = await get(`/api/occasions/${state.occasionId}/exchanges`);
  if (!ctx.alive()) return;
  const name = state.occasions.find(o => o.id === state.occasionId)?.name ?? "";
  view.innerHTML = renderExchanges(list, state.people, name, isUnlocked());

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
  const draw = (exchange, drawer, recipient) => send("PUT", `/api/exchanges/${exchange}/draws/${drawer}`, { recipient_id: Number(recipient) });

  view.addEventListener("click", async e => {
    const btn = e.target.closest?.("[data-act]");
    const act = btn?.dataset.act;
    if (act === "remove-draw" && globalThis.confirm("Remove this draw?")) {
      await run(() => send("DELETE", `/api/exchanges/${asId(btn)}/draws/${asDrawer(btn)}`), "Removed");
    }
    if (act === "delete-exchange" && globalThis.confirm("Delete this exchange?")) {
      await run(() => send("DELETE", `/api/exchanges/${asId(btn)}`), "Deleted");
    }
  });

  view.addEventListener("change", async e => {
    const sel = e.target.closest?.('select[data-act="redraw"]');
    if (sel) await run(() => draw(asId(sel), asDrawer(sel), sel.value), "Saved");
  });

  view.addEventListener("submit", async e => {
    e.preventDefault();
    const form = e.target;
    const kind = form.dataset?.form;
    const field = n => form.elements[n]?.value ?? "";
    if (kind === "exchange-add") {
      await run(() => send("POST", `/api/occasions/${state.occasionId}/exchanges`, exchangePayload({ name: field("name"), budget: field("budget"), date: field("date") })), "Added");
    }
    if (kind === "exchange-edit") {
      await run(() => send("PATCH", `/api/exchanges/${asId(form)}`, exchangePayload({ name: field("name"), budget: field("budget"), date: field("date") })), "Saved");
    }
    if (kind === "draw-add") await run(() => draw(asId(form), field("drawer_id"), field("recipient_id")), "Added");
    if (kind === "gift-add") {
      const body = giftPayload(state.occasionId, asId(form), { drawer_id: asDrawer(form), recipient_id: Number(form.dataset.recipient) }, { title: field("title"), price: field("price") });
      await run(() => send("POST", "/api/gifts", body), "Gift added");
    }
  });
}
