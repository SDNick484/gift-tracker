// #/  Home: one occasion at a glance. The occasion picker with days until, + Idea, the protected section (one
// locked row while locked, one row per protected person once unlocked), then every group with each person's
// plan, headline and spent of budget.
import { get } from "./api.js";
import { daysLabel, esc, money, renderColor, renderIcon, renderOptions, setOccasion, state } from "./core.js";

const PLAN_LABEL = { individual: "", joint: "joint", exchange: "exchange only", skip: "skip" };

export const planLabel = plan => PLAN_LABEL[plan] ?? "";

// What a person's right-hand cell says: money spent (of the budget, when there is one) and, below it, what is
// planned but not yet bought.
export function amountText(p) {
  const main = p.budget_cents === null || p.budget_cents === undefined
    ? money(p.spent_cents)
    : `${money(p.spent_cents)} of ${money(p.budget_cents)}`;
  return { main, sub: p.planned_cents > 0 ? `+${money(p.planned_cents)} planned` : "" };
}

// Occasions as picker options: the live ones first, archived ones last and marked.
export function occasionChoices(occasions) {
  const live = occasions.filter(o => !o.archived), old = occasions.filter(o => o.archived);
  return [...live, ...old].map(o => ({ value: o.id, label: o.archived ? `${o.name} (archived)` : o.name }));
}

const renderDim = on => (on ? " dim" : "");
const renderPlanBadge = p => (planLabel(p.plan) ? `<span class="badge plan">${esc(planLabel(p.plan))}</span> ` : "");
const renderHeadline = p => esc(p.headline ?? "nothing yet");
const renderSub = text => (text ? `<small>${esc(text)}</small>` : "");

function renderPerson(p) {
  const amt = amountText(p);
  return `<li class="row${renderDim(p.plan === "skip")}"${renderColor(p.color)}>
    <a class="main" href="#/person/${esc(p.id)}"><div class="name">${esc(p.name)}</div><div class="line">${renderPlanBadge(p)}${renderHeadline(p)}</div></a>
    <div class="amt">${esc(amt.main)}${renderSub(amt.sub)}</div>
  </li>`;
}

function renderProtectedPerson(p) {
  const amt = amountText(p);
  return `<li class="row"${renderColor(p.color)}>
    <a class="main" href="#/board/${esc(p.id)}"><div class="name">${esc(p.name)}</div><div class="line">${renderHeadline(p)}</div></a>
    <div class="amt">${esc(amt.main)}${renderSub(amt.sub)}</div>
    <div class="row-actions"><a class="btn small" href="#/board/${esc(p.id)}">Board</a><a class="btn small" href="#/person/${esc(p.id)}">Gifts and plan</a></div>
  </li>`;
}

const renderPeople = people => people.map(renderPerson).join("");
const renderProtectedPeople = people => people.map(renderProtectedPerson).join("");
const renderGroup = g => `<section class="col"><h3>${esc(g.name)}</h3><ul class="rows">${renderPeople(g.people)}</ul></section>`;
const renderGroups = groups => groups.map(renderGroup).join("");

const renderLockedRow = () => `<section><h3>The kids</h3>
  <button type="button" class="row locked-row" data-act="unlock"><span class="main"><span class="name">Locked</span><br><span class="line">Tap to unlock</span></span><span class="amt">${renderIcon("lock")}</span></button></section>`;

const renderProtectedSection = (summary, anyProtected) => {
  if (summary.protected.locked) return anyProtected ? renderLockedRow() : "";
  return summary.protected.people.length
    ? `<section><h3>The kids</h3><ul class="rows">${renderProtectedPeople(summary.protected.people)}</ul></section>`
    : "";
};

const renderEmpty = summary => (summary.groups.length || summary.protected.people.length
  ? ""
  : `<p class="empty">No one is in this occasion yet. Add people in <a href="#/settings">Settings</a>.</p>`);

// "Not here yet 3 · To wrap 9 · Where is it?": links to the three lists. Where is it? has no number, as in the spec.
export const renderCounts = c => `<p class="counts"><a href="#/lists/pending">Not here yet ${esc(c.pending)}</a> · <a href="#/lists/to-wrap">To wrap ${esc(c.to_wrap)}</a> · <a href="#/lists/where">Where is it?</a></p>`;
const renderCountsRow = c => (c ? renderCounts(c) : "");

export const renderHome = (summary, occasions, anyProtected, counts = null) => `
  <div class="head">
    <div class="picker">
      <select id="occasion" aria-label="Occasion">${renderOptions(occasionChoices(occasions), summary.occasion.id)}</select>
      <span class="countdown">${esc(daysLabel(summary.days_until))}</span>
    </div>
  </div>
  <a class="btn primary big-add" href="#/idea">${renderIcon("plus")} Idea</a>
  ${renderCountsRow(counts)}
  ${renderProtectedSection(summary, anyProtected)}
  <div class="cols">${renderGroups(summary.groups)}</div>
  ${renderEmpty(summary)}`;

const renderNoOccasion = () => `<div class="notice"><p>No occasion yet.</p><p><a class="btn primary" href="#/settings">Create one in Settings</a></p></div>`;

export async function render(ctx) {
  const { view } = ctx;
  if (state.occasionId === null) {
    view.innerHTML = renderNoOccasion();
    return;
  }
  const [summary, counts] = await Promise.all([get(`/api/occasions/${state.occasionId}/summary`),
    get(`/api/occasions/${state.occasionId}/lists/counts`)]);
  if (!ctx.alive()) return;
  const anyProtected = state.people.some(p => p.active && p.protected);
  view.innerHTML = renderHome(summary, state.occasions, anyProtected, counts);
  view.querySelector("#occasion").addEventListener("change", e => {
    setOccasion(Number(e.target.value));
    ctx.rerender();
  });
}
