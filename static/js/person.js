// #/person/<id>  One person: this occasion's gifts, the plan and budget editor, then history by occasion.
import { get, send } from "./api.js";
import {
  centsToText, currentOccasion, dollarsToCents, esc, giverLabel, money, personById, renderColor, renderOptions,
  requireUnlocked, state, toast,
} from "./core.js";

const PLANS = [
  { value: "", label: "Not in this occasion" }, { value: "individual", label: "Individual" },
  { value: "joint", label: "Joint" }, { value: "exchange", label: "Exchange only" }, { value: "skip", label: "Skip" },
];

// The second line of a gift row: who it is from, and who else receives it.
export function giftLine(gift, people, personId) {
  const others = gift.recipient_ids.filter(id => id !== personId).map(id => people.find(p => p.id === id)?.name).filter(Boolean);
  return { from: giverLabel(gift.giver_ids, people), also: others.join(" & ") };
}

// History grouped by occasion in the order given (newest first), leaving out the occasion shown above it.
export function groupByOccasion(gifts, excludeId) {
  const groups = [];
  for (const g of gifts) {
    if (g.occasion.id === excludeId) continue;
    let group = groups.find(x => x.id === g.occasion.id);
    if (!group) groups.push((group = { id: g.occasion.id, name: g.occasion.name, date: g.occasion.date, gifts: [] }));
    group.gifts.push(g);
  }
  return groups;
}

const renderSanta = g => (g.santa === true ? ` <span class="badge santa">Santa</span>` : "");
const renderFrom = line => (line.from ? ` from ${esc(line.from)}` : "");
const renderAlso = line => (line.also ? ` · with ${esc(line.also)}` : "");

function renderGift(g, people, personId) {
  const line = giftLine(g, people, personId);
  return `<li class="row">
    <a class="main" href="#/gift/${esc(g.id)}"><div class="name">${esc(g.title)}</div>
      <div class="line"><span class="badge">${esc(g.status)}</span>${renderFrom(line)}${renderAlso(line)}${renderSanta(g)}</div></a>
    <div class="amt">${esc(money(g.price_cents))}</div>
  </li>`;
}
const renderGifts = (gifts, people, personId) => gifts.map(g => renderGift(g, people, personId)).join("");
const renderNoGifts = gifts => (gifts.length ? "" : `<p class="empty">Nothing yet.</p>`);

const renderHistoryGroup = (group, people, personId) => `<section class="col"><h3>${esc(group.name)}</h3><ul class="rows">${renderGifts(group.gifts, people, personId)}</ul></section>`;
const renderHistory = (groups, people, personId) => groups.map(x => renderHistoryGroup(x, people, personId)).join("");

const renderBoardLink = person => (person.protected ? `<a class="btn" href="#/board/${esc(person.id)}">Board</a>` : "");
const renderInactive = person => (person.active ? "" : ` <span class="badge">inactive</span>`);
const renderSubline = (person, groupName) => esc([groupName, person.household].filter(Boolean).join(" · "));

// "Spent $X of $Y" for the person, from the home summary's entry for them.
export function totalsText(entry) {
  if (!entry) return "";
  const budget = entry.budget_cents === null ? "" : ` of ${money(entry.budget_cents)}`;
  const planned = entry.planned_cents > 0 ? ` · ${money(entry.planned_cents)} planned` : "";
  return `Spent ${money(entry.spent_cents)}${budget}${planned}`;
}

export function findEntry(summary, personId) {
  return [...summary.groups.flatMap(g => g.people), ...summary.protected.people].find(p => p.id === personId) ?? null;
}

const renderTotals = entry => (entry ? ` · ${esc(totalsText(entry))}` : "");

export const renderPage = (person, groupName, occasion, plan, entry, gifts, history, people) => `
  <a class="back" href="#/">&larr; Home</a>
  <section class="card colored"${renderColor(person.color)}>
    <div class="head"><div><h2>${esc(person.name)}${renderInactive(person)}</h2><span class="sub">${renderSubline(person, groupName)}</span></div>
      <div class="actions">${renderBoardLink(person)}<a class="btn primary" href="#/idea?for=${esc(person.id)}">+ Idea</a></div></div>
    <p class="muted small">${esc(occasion.name)}${renderTotals(entry)}</p>
  </section>
  <div class="cols wide-left">
    <div class="col">
      <h3>${esc(occasion.name)}</h3>
      <ul class="rows">${renderGifts(gifts, people, person.id)}</ul>
      ${renderNoGifts(gifts)}
    </div>
    <div class="col">
      <form id="plan" class="card fields">
        <h3>Plan for ${esc(occasion.name)}</h3>
        <label class="field"><span>Plan</span><select name="plan">${renderOptions(PLANS, plan?.plan ?? "")}</select></label>
        <label class="field"><span>Budget</span><input name="budget" type="text" inputmode="decimal" placeholder="none" value="${esc(centsToText(plan?.budget_cents))}"></label>
        <p class="form-error" role="alert"></p>
        <div class="actions"><button class="btn primary" type="submit">Save plan</button></div>
      </form>
      ${renderHistory(history, people, person.id)}
    </div>
  </div>`;
const renderNotFound = () => `<div class="notice"><p>No such person.</p><p><a class="btn" href="#/">Home</a></p></div>`;
const renderNoOccasion = () => `<div class="notice"><p>Create an occasion first.</p><p><a class="btn primary" href="#/settings">Open Settings</a></p></div>`;

export async function render(ctx) {
  const { view } = ctx;
  const person = personById(Number(ctx.parts[1]));
  if (!person) {
    view.innerHTML = renderNotFound();
    return;
  }
  if (person.protected) requireUnlocked();
  const occasion = currentOccasion();
  if (!occasion) {
    view.innerHTML = renderNoOccasion();
    return;
  }
  const [gifts, history, summary, groups] = await Promise.all([
    get(`/api/occasions/${occasion.id}/people/${person.id}/gifts`),
    get(`/api/people/${person.id}/history`),
    get(`/api/occasions/${occasion.id}/summary`),
    get("/api/groups"),
  ]);
  if (!ctx.alive()) return;
  const plan = occasion.people.find(p => p.person_id === person.id) ?? null;
  const groupName = groups.find(g => g.id === person.group_id)?.name ?? "";
  view.innerHTML = renderPage(person, groupName, occasion, plan, findEntry(summary, person.id), gifts,
    groupByOccasion(history.gifts, occasion.id), state.people);
  wirePlan(ctx, view.querySelector("#plan"), occasion, person, plan);
}

function wirePlan(ctx, form, occasion, person, plan) {
  const error = form.querySelector(".form-error");
  form.addEventListener("submit", async e => {
    e.preventDefault();
    error.textContent = "";
    const choice = form.elements.plan.value;
    try {
      const path = `/api/occasions/${occasion.id}/people/${person.id}`;
      if (choice === "") {
        if (plan) await send("DELETE", path);
      } else {
        await send("PUT", path, { plan: choice, budget_cents: dollarsToCents(form.elements.budget.value) });
      }
    } catch (err) {
      error.textContent = err.message;
      return;
    }
    toast("Saved");
    ctx.rerender();
  });
}
