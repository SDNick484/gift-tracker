// #/lists/<pending|to-wrap|where>  The three working lists after purchase. Not here yet: ordered gifts by expected
// date, a row turns red when its date has passed and carries a warning when it ships somewhere that is not home.
// To wrap: arrived gifts under each recipient, with the hiding place. Where is it?: arrived or wrapped gifts by
// hiding place. The server leaves protected gifts out of all three while locked; nothing here decides that.
// School mode has no hiding places, so no "Where is it?" tab.
import { get } from "./api.js";
import { esc, renderColor, state } from "./core.js";
import { isSchool } from "./mode.js";

const TABS = [
  { key: "pending", label: "Not here yet" },
  { key: "to-wrap", label: "To wrap" },
  { key: "where", label: "Where is it?" },
];
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

export const tabsFor = school => TABS.filter(t => !(school && t.key === "where"));
export const tabKey = (name, school) => (tabsFor(school).some(t => t.key === name) ? name : "pending");
export const tabLabel = key => TABS.find(t => t.key === key)?.label ?? "";

// "2026-12-09" -> "Dec 9"; nothing -> "no date"; anything that is not a date comes back as it was.
export function shortDate(iso) {
  if (!iso) return "no date";
  const m = /^\d{4}-(\d{2})-(\d{2})$/.exec(iso);
  return m && MONTHS[Number(m[1]) - 1] ? `${MONTHS[Number(m[1]) - 1]} ${Number(m[2])}` : iso;
}

export function names(ids, people) {
  const byId = new Map(people.map(p => [p.id, p.name]));
  return ids.map(id => byId.get(id)).filter(Boolean).join(" & ");
}

// ---------------------------------------------------------------- markup
const renderCurrent = on => (on ? ` aria-current="page"` : "");
const renderTab = (t, active) => `<a class="tab" href="#/lists/${esc(t.key)}"${renderCurrent(t.key === active)}>${esc(t.label)}</a>`;
const renderTabLinks = (active, school) => tabsFor(school).map(t => renderTab(t, active)).join("");
export const renderTabs = (active, school) => `<nav class="tabs" aria-label="Lists">${renderTabLinks(active, school)}</nav>`;

const renderOverdueClass = g => (g.overdue ? " overdue" : "");
const renderOverdueBadge = g => (g.overdue ? ` <span class="badge overdue">overdue</span>` : "");
const renderShipBadge = g => (g.ship_warning ? ` <span class="badge warn">ships elsewhere</span>` : "");
const renderSeller = g => (g.seller ? ` · ${esc(g.seller)}${g.order_number ? ` #${esc(g.order_number)}` : ""}` : "");
// School mode sends no location keys at all: nothing to say there. Family mode sends null for "not placed yet".
const renderPlace = g => {
  if (!("location_path" in g)) return "";
  return g.location_path ? esc(g.location_path) : "no hiding place yet";
};

const renderPendingRow = (g, people) => `<li class="row${renderOverdueClass(g)}">
  <a class="main" href="#/gift/${esc(g.id)}"><div class="name">${esc(g.title)}</div>
    <div class="line">to ${esc(names(g.recipient_ids, people))}${renderSeller(g)}</div></a>
  <div class="amt">${esc(shortDate(g.eta))}${renderOverdueBadge(g)}${renderShipBadge(g)}</div>
</li>`;
const renderPendingRows = (gifts, people) => gifts.map(g => renderPendingRow(g, people)).join("");

export const renderPending = (data, people) => (data.gifts.length
  ? `<ul class="rows">${renderPendingRows(data.gifts, people)}</ul>`
  : `<p class="empty">Nothing on its way.</p>`);

const renderWrapRow = g => `<li class="row">
  <a class="main" href="#/gift/${esc(g.id)}"><div class="name">${esc(g.title)}</div>
    <div class="line">${renderPlace(g)}</div></a>
</li>`;
const renderWrapRows = gifts => gifts.map(renderWrapRow).join("");
const renderWrapGroup = (group, people) => {
  const person = people.find(p => p.id === group.person_id);
  return `<section class="card colored"${renderColor(person?.color)}><h3>${esc(person?.name ?? "")}</h3>
    <ul class="rows">${renderWrapRows(group.gifts)}</ul></section>`;
};
const renderWrapGroups = (groups, people) => groups.map(g => renderWrapGroup(g, people)).join("");

export const renderToWrap = (data, people) => (data.groups.length
  ? `<div class="cols">${renderWrapGroups(data.groups, people)}</div>`
  : `<p class="empty">Nothing to wrap.</p>`);

const renderWhereRow = (g, people) => `<li class="row">
  <a class="main" href="#/gift/${esc(g.id)}"><div class="name">${esc(g.title)}</div>
    <div class="line">to ${esc(names(g.recipient_ids, people))}</div></a>
  <div class="amt"><span class="badge">${esc(g.status)}</span></div>
</li>`;
const renderWhereRows = (gifts, people) => gifts.map(g => renderWhereRow(g, people)).join("");
const renderWhereGroup = (group, people) => `<section class="card"><h3>${esc(group.path ?? "No place yet")}</h3>
  <ul class="rows">${renderWhereRows(group.gifts, people)}</ul></section>`;
const renderWhereGroups = (groups, people) => groups.map(g => renderWhereGroup(g, people)).join("");

export const renderWhere = (data, people) => (data.groups.length
  ? `<div class="cols">${renderWhereGroups(data.groups, people)}</div>`
  : `<p class="empty">Nothing is in the house yet.</p>`);

const renderBody = (tab, data, people) => {
  if (tab === "to-wrap") return renderToWrap(data, people);
  if (tab === "where") return renderWhere(data, people);
  return renderPending(data, people);
};

export const renderLists = (tab, data, people, school, occasionName) => `
  <a class="back" href="#/">&larr; Home</a>
  <div class="head"><h2>${esc(tabLabel(tab))}</h2><span class="sub">${esc(occasionName)}</span></div>
  ${renderTabs(tab, school)}
  ${renderBody(tab, data, people)}
  <p class="muted small"><a href="#/places">Addresses and hiding places</a></p>`;

const renderNoOccasion = () => `<div class="notice"><p>No occasion yet.</p><p><a class="btn primary" href="#/settings">Create one in Settings</a></p></div>`;

export async function render(ctx) {
  const { view } = ctx;
  if (state.occasionId === null) {
    view.innerHTML = renderNoOccasion();
    return;
  }
  const school = isSchool(state.me);
  const tab = tabKey(ctx.parts[1] ?? "", school);
  const data = await get(`/api/occasions/${state.occasionId}/lists/${tab}`);
  if (!ctx.alive()) return;
  const occasionName = state.occasions.find(o => o.id === state.occasionId)?.name ?? "";
  view.innerHTML = renderLists(tab, data, state.people, school, occasionName);
}
