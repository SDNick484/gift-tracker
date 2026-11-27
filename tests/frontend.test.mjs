// Frontend unit tests: `node --test tests/frontend.test.mjs`. The modules under static/js/ are ES modules with
// no top-level DOM access, so they load in Node as they are; only the DOM-free helpers are tested here.
import assert from "node:assert/strict";
import { test } from "node:test";

import { ApiError, events, get, send } from "../static/js/api.js";
import { backTarget, forChoices, ideaBody, render as renderIdeaScreen } from "../static/js/idea.js";
import { findEntry, giftLine, groupByOccasion, renderPage, totalsText } from "../static/js/person.js";
import { buildPatch, canSanta, pickList, renderPage as renderGiftPage, renderSteps, stepClass } from "../static/js/gift.js";
import { assignBody, assignChoices, countGifts, evennessLine, renderBoard, splitLine } from "../static/js/board.js";
import { groupPayload, occasionPayload, personPayload, pinPayload, renderOccasionEdit, renderPersonForm, renderSettings, visibleHouseholds } from "../static/js/settings.js";
import { idleExpired, nextEntry, renderNoPin, renderPad } from "../static/js/lock.js";
import { amountText, occasionChoices, planLabel, renderHome } from "../static/js/home.js";
import { applyColors, centsToText, daysLabel, dollarsToCents, esc, giverLabel, giverText, money, parseHash, statusSteps } from "../static/js/core.js";

test("esc escapes the five HTML characters", () => {
  assert.equal(esc(`<a href="x">'&`), "&lt;a href=&quot;x&quot;&gt;&#39;&amp;");
  assert.equal(esc(null), "");
  assert.equal(esc(undefined), "");
  assert.equal(esc(0), "0");
  assert.equal(esc("&amp;"), "&amp;amp;");
});

test("money", () => {
  assert.equal(money(2999), "$29.99");
  assert.equal(money(129900), "$1,299.00");
  assert.equal(money(0), "$0.00");
  assert.equal(money(5), "$0.05");
  assert.equal(money(1000000000), "$10,000,000.00");
  assert.equal(money(null), "");
  assert.equal(money(undefined), "");
  assert.equal(money(NaN), "");
  assert.equal(money(12.5), "");
});

test("daysLabel", () => {
  assert.equal(daysLabel(43), "43 days");
  assert.equal(daysLabel(1), "1 day");
  assert.equal(daysLabel(0), "today");
  assert.equal(daysLabel(-2), "2 days ago");
  assert.equal(daysLabel(-1), "1 day ago");
});

test("statusSteps", () => {
  assert.deepEqual(statusSteps("theirs"), ["told", "confirmed", "given"]);
  assert.equal(statusSteps("ours").length, 6);
  assert.deepEqual(statusSteps("ours"), ["idea", "decided", "ordered", "arrived", "wrapped", "given"]);
});

test("dollarsToCents accepts what the server accepts and never goes through a float", () => {
  assert.equal(dollarsToCents("75"), 7500);
  assert.equal(dollarsToCents("$29.99"), 2999);
  assert.equal(dollarsToCents("29.9"), 2990);
  assert.equal(dollarsToCents("1,299.00"), 129900);
  assert.equal(dollarsToCents(" 0.07 "), 7);
  assert.equal(dollarsToCents(""), null);
  assert.equal(dollarsToCents("  "), null);
  assert.equal(dollarsToCents(null), null);
  for (const bad of ["abc", "-5", "NaN", "1.234", "1e3", "12,34", "$", "."]) {
    assert.throws(() => dollarsToCents(bad), /amount/, bad);
  }
});

test("centsToText fills an input with an amount the server will read back", () => {
  assert.equal(centsToText(2999), "29.99");
  assert.equal(centsToText(7500), "75.00");
  assert.equal(centsToText(5), "0.05");
  assert.equal(centsToText(null), "");
  assert.equal(dollarsToCents(centsToText(129901)), 129901);
});

test("parseHash", () => {
  assert.deepEqual(parseHash(""), { parts: [], query: {} });
  assert.deepEqual(parseHash("#/"), { parts: [], query: {} });
  assert.deepEqual(parseHash("#/person/12"), { parts: ["person", "12"], query: {} });
  assert.deepEqual(parseHash("#/idea?for=3&x=a%20b"), { parts: ["idea"], query: { for: "3", x: "a b" } });
  assert.deepEqual(parseHash("#/gift/7/"), { parts: ["gift", "7"], query: {} });
});

test("giverLabel names the givers, and 'Us' when it is every owner", () => {
  const people = [
    { id: 1, name: "Alex", is_user: true }, { id: 2, name: "Sam", is_user: true },
    { id: 3, name: "Pat", is_user: false }, { id: 4, name: "Lee", is_user: false },
  ];
  assert.equal(giverLabel([1, 2], people), "Us");
  assert.equal(giverLabel([2], people), "Sam");
  assert.equal(giverLabel([3, 4], people), "Pat & Lee");
  assert.equal(giverLabel([1, 3], people), "Alex & Pat");
  assert.equal(giverLabel([], people), "");
  assert.equal(giverLabel([99], people), "");
});

test("applyColors sets --c through the CSSOM and only for a plain hex color", () => {
  const mk = color => ({ dataset: { color }, style: { set: {}, setProperty(k, v) { this.set[k] = v; } } });
  const els = [mk("#1f4d3a"), mk("red; background:url(x)"), mk("#12345"), mk(undefined)];
  applyColors({ querySelectorAll: sel => (assert.equal(sel, "[data-color]"), els) });
  assert.deepEqual(els.map(e => e.style.set["--c"]), ["#1f4d3a", undefined, undefined, undefined]);
});

// ---------------------------------------------------------------- api.js
function stubFetch(status, body, seen = []) {
  globalThis.fetch = async (url, init) => {
    seen.push({ url, init });
    return { ok: status >= 200 && status < 300, status, json: async () => { if (body === undefined) throw new SyntaxError("no body"); return body; } };
  };
  return seen;
}

test("send adds X-Tracker and a JSON body; get adds neither", async () => {
  const seen = stubFetch(200, { ok: true });
  assert.deepEqual(await send("POST", "/api/gifts", { title: "x" }), { ok: true });
  assert.equal(seen[0].init.method, "POST");
  assert.equal(seen[0].init.headers["X-Tracker"], "1");
  assert.equal(seen[0].init.headers["Content-Type"], "application/json");
  assert.equal(seen[0].init.body, '{"title":"x"}');
  await send("DELETE", "/api/people/1");
  assert.equal(seen[1].init.body, "{}", "a DELETE still carries a JSON body: the request guard insists");
  await get("/api/me");
  assert.equal(seen[2].init.method, "GET");
  assert.equal(seen[2].init.headers["X-Tracker"], undefined);
  assert.equal(seen[2].init.body, undefined);
  assert.equal(seen[2].init.credentials, "same-origin");
});

test("a 423 dispatches `locked` and throws an ApiError that says so", async () => {
  stubFetch(423, { error: "Locked", locked: true });
  let fired = 0;
  const on = () => fired++;
  events.addEventListener("locked", on);
  await assert.rejects(get("/api/occasions/1/board/2"), e => e instanceof ApiError && e.status === 423 && e.locked === true && e.message === "Locked");
  events.removeEventListener("locked", on);
  assert.equal(fired, 1);
});

test("other errors carry the server's message and do not dispatch `locked`", async () => {
  let fired = 0;
  const on = () => fired++;
  events.addEventListener("locked", on);
  stubFetch(409, { error: "Someone just assigned this. Reload." });
  await assert.rejects(send("POST", "/api/gifts/1/assign", {}), e => e.status === 409 && e.message === "Someone just assigned this. Reload." && !e.locked);
  stubFetch(500, undefined);
  await assert.rejects(get("/api/me"), e => e.status === 500 && /500/.test(e.message));
  stubFetch(200, undefined);
  await assert.rejects(get("/api/me"), e => /Reload/.test(e.message));
  globalThis.fetch = async () => { throw new TypeError("offline"); };
  await assert.rejects(get("/api/me"), e => e.status === 0 && /reach/.test(e.message));
  events.removeEventListener("locked", on);
  assert.equal(fired, 0);
});

// ---------------------------------------------------------------- home.js
const person = (over = {}) => ({ id: 5, name: "Pat", color: "#1f4d3a", plan: "individual", budget_cents: 7500, spent_cents: 0, planned_cents: 0, gift_count: 0, headline: null, ...over });
const summary = (over = {}) => ({
  occasion: { id: 1, name: "Christmas", kind: "christmas", date: "2026-12-25", archived: false }, days_until: 43,
  groups: [{ id: 1, name: "Side A", people: [person()] }], protected: { locked: true, people: [] }, ...over,
});

test("home: amounts, plans and the occasion picker", () => {
  assert.deepEqual(amountText(person({ spent_cents: 2999, planned_cents: 500 })), { main: "$29.99 of $75.00", sub: "+$5.00 planned" });
  assert.deepEqual(amountText(person({ budget_cents: null, spent_cents: 0 })), { main: "$0.00", sub: "" });
  assert.equal(planLabel("exchange"), "exchange only");
  assert.equal(planLabel("individual"), "");
  assert.equal(planLabel(null), "");
  assert.deepEqual(occasionChoices([{ id: 1, name: "Old", archived: true }, { id: 2, name: "New", archived: false }]),
    [{ value: 2, label: "New" }, { value: 1, label: "Old (archived)" }]);
});

test("home: names, headlines and colors are escaped, and a protected section only shows as a locked row", () => {
  const evil = person({ name: `<img src=x onerror=alert(1)>`, headline: `"><script>x</script>`, color: `red" onmouseover="x` });
  const html = renderHome(summary({ groups: [{ id: 1, name: `<b>G</b>`, people: [evil] }] }), [{ id: 1, name: "<i>X</i>", archived: false }], true);
  assert.ok(!/<img|<script|<b>|<i>|onmouseover/.test(html), html);
  assert.ok(html.includes("&lt;img src=x onerror=alert(1)&gt;"));
  assert.ok(!html.includes("data-color"), "a color that is not #rrggbb is not passed on");
  assert.ok(html.includes("43 days") && html.includes('data-act="unlock"') && html.includes("Locked"));
  assert.ok(renderHome(summary(), [], true).includes('data-color="#1f4d3a"'));
  assert.ok(!renderHome(summary(), [], false).includes('data-act="unlock"'), "no protected people: nothing to unlock");
});

test("home: unlocked, one row per protected person linking to the board", () => {
  const html = renderHome(summary({ protected: { locked: false, people: [person({ id: 9, name: "Riley", headline: "Puzzle" })] } }), [], true);
  assert.ok(html.includes('href="#/board/9"') && html.includes("Puzzle") && !html.includes('data-act="unlock"'));
});

// ---------------------------------------------------------------- idea.js
test("idea: For lists the most recently used first and leaves out the owners", () => {
  const people = [{ id: 1, name: "Alex", is_user: true }, { id: 3, name: "Pat" }, { id: 4, name: "Lee" }, { id: 5, name: "Jordan" }];
  assert.deepEqual(forChoices(people, []).map(c => c.value), [3, 4, 5]);
  assert.deepEqual(forChoices(people, [5, 99, 1, 4]).map(c => c.value), [5, 4, 3]);
  assert.deepEqual(forChoices(people, [5]), [{ value: 5, label: "Jordan" }, { value: 3, label: "Pat" }, { value: 4, label: "Lee" }]);
});

test("idea: Save returns to where the user came from, never to the idea form itself", () => {
  assert.equal(backTarget("#/person/4"), "#/person/4");
  assert.equal(backTarget("#/idea?for=3"), "#/");
  assert.equal(backTarget(""), "#/");
});

test("idea: with nobody to tag there is nothing to save; an idea needs no occasion", async () => {
  const view = { innerHTML: "" };
  await renderIdeaScreen({ view, query: {}, alive: () => true });
  assert.ok(view.innerHTML.includes("Add the people you buy for first.") && !view.innerHTML.includes("occasion"));
});

// A form stand-in: named fields, and the For check boxes that are checked.
const ideaForm = (fields, checkedIds) => ({
  elements: Object.fromEntries(Object.entries(fields).map(([k, v]) => [k, typeof v === "boolean" ? { checked: v } : { value: v }])),
  querySelectorAll: sel => (assert.equal(sel, 'input[name="for"]:checked'), checkedIds.map(id => ({ value: String(id) }))),
});

test("idea: the body is an idea for every checked person, blank link and store left out, share off", () => {
  assert.deepEqual(ideaBody(ideaForm({ what: "  Kite ", url: " ", store: "", share: false }, [3, 5])),
    { title: "Kite", person_ids: [3, 5], share: false });
  assert.deepEqual(ideaBody(ideaForm({ what: "Kite", url: " https://example.com/kite ", store: " Toy Shop ", share: true }, [4])),
    { title: "Kite", person_ids: [4], url: "https://example.com/kite", store: "Toy Shop", share: true });
  assert.deepEqual(ideaBody(ideaForm({ what: "Kite", url: "", store: "", price: " 19.99 ", share: false }, [4])),
    { title: "Kite", person_ids: [4], price: "19.99", share: false });
});

// ---------------------------------------------------------------- person.js
const people4 = [{ id: 1, name: "Alex", is_user: true }, { id: 2, name: "Sam", is_user: true }, { id: 3, name: "Pat" }, { id: 4, name: "Lee" }];
const gift = (over = {}) => ({ id: 10, title: "Shoes?", status: "idea", price_cents: 7500, recipient_ids: [3], giver_ids: [1], ...over });

test("person: a gift line says who it is from and who else gets it", () => {
  assert.deepEqual(giftLine(gift(), people4, 3), { from: "Alex", also: "" });
  assert.deepEqual(giftLine(gift({ recipient_ids: [3, 4], giver_ids: [1, 2] }), people4, 3), { from: "Us", also: "Lee" });
  assert.deepEqual(giftLine(gift({ giver_ids: [] }), people4, 3), { from: "", also: "" });
});

test("person: history groups by occasion, newest first, without the occasion shown above", () => {
  const h = [
    { ...gift({ id: 1 }), occasion: { id: 7, name: "Christmas 2025", date: "2025-12-25" } },
    { ...gift({ id: 2 }), occasion: { id: 7, name: "Christmas 2025", date: "2025-12-25" } },
    { ...gift({ id: 3 }), occasion: { id: 9, name: "Christmas 2026", date: "2026-12-25" } },
    { ...gift({ id: 4 }), occasion: { id: 5, name: "Christmas 2024", date: "2024-12-25" } },
  ];
  const groups = groupByOccasion(h, 9);
  assert.deepEqual(groups.map(g => [g.name, g.gifts.map(x => x.id)]), [["Christmas 2025", [1, 2]], ["Christmas 2024", [4]]]);
});

test("person: totals text and the summary entry lookup", () => {
  assert.equal(totalsText({ spent_cents: 2999, budget_cents: 7500, planned_cents: 500 }), "Spent $29.99 of $75.00 · $5.00 planned");
  assert.equal(totalsText({ spent_cents: 0, budget_cents: null, planned_cents: 0 }), "Spent $0.00");
  assert.equal(totalsText(null), "");
  const s = { groups: [{ people: [{ id: 3 }] }], protected: { people: [{ id: 8 }] } };
  assert.deepEqual([findEntry(s, 3).id, findEntry(s, 8).id, findEntry(s, 99)], [3, 8, null]);
});

test("person: titles, names and notes are escaped, Santa only shows when the server sent it", () => {
  const evil = gift({ title: `<script>alert(1)</script>`, santa: true });
  const person = { id: 3, name: `<b>Pat</b>`, color: "#1f4d3a", household: `"x"`, active: false, protected: false };
  const occasion = { id: 1, name: `<i>Xmas</i>` };
  const html = renderPage(person, "Side <A>", occasion, { plan: "joint", budget_cents: 7500 }, null, [evil, gift({ id: 11 })], [], people4);
  assert.ok(!/<script|<b>|<i>/.test(html), html);
  assert.equal((html.match(/badge santa/g) ?? []).length, 1);
  assert.ok(html.includes('value="75.00"') && html.includes('<option value="joint" selected>'));
  assert.ok(html.includes("inactive") && html.includes('data-color="#1f4d3a"') && !html.includes("Board"));
  assert.ok(renderPage({ ...person, protected: true }, "", occasion, null, null, [], [], people4).includes('href="#/board/3"'));
});

// ---------------------------------------------------------------- gift.js
const fullGift = (over = {}) => ({
  id: 10, occasion_id: 1, title: "Camera", status: "ordered", track: "ours", url: null, store: null, price_cents: 8900, notes: null,
  recipient_ids: [3], giver_ids: [1, 2], told_at: null, santa: false, ...over,
});

test("gift: Santa needs the flag from the server, an ours gift and an owner among the givers", () => {
  assert.equal(canSanta(fullGift(), people4), true);
  assert.equal(canSanta(fullGift({ giver_ids: [1] }), people4), true);
  assert.equal(canSanta(fullGift({ giver_ids: [] }), people4), false, "no giver yet: not from an owner");
  assert.equal(canSanta(fullGift({ giver_ids: [4], track: "theirs" }), people4), false);
  const locked = fullGift(); delete locked.santa;
  assert.equal(canSanta(locked, people4), false, "no santa key while locked");
});

test("gift: status steps follow the track and mark the current and earlier ones", () => {
  const ours = renderSteps(fullGift());
  assert.deepEqual([...ours.matchAll(/data-status="(\w+)"/g)].map(m => m[1]), ["idea", "decided", "ordered", "arrived", "wrapped", "given"]);
  assert.ok(ours.includes('class="step done" data-status="decided" aria-pressed="false"'));
  assert.ok(ours.includes('class="step" data-status="ordered" aria-pressed="true"'));
  assert.deepEqual([...renderSteps(fullGift({ track: "theirs", status: "told" })).matchAll(/data-status="(\w+)"/g)].map(m => m[1]), ["told", "confirmed", "given"]);
  assert.equal(stepClass(0, 2), "step done");
  assert.equal(stepClass(2, 2), "step");
  assert.equal(stepClass(0, -1), "step", "a status not on this track marks nothing done");
});

test("gift: who can be ticked", () => {
  const ppl = [...people4, { id: 7, name: "Kid", protected: true, active: true }, { id: 8, name: "Old", active: false }].map(p => ({ active: true, ...p }));
  const ids = (selected, o) => pickList(ppl, selected, o).map(p => p.id);
  assert.deepEqual(ids([], { unlocked: false, recipients: true }), [3, 4]);
  assert.deepEqual(ids([], { unlocked: true, recipients: true }), [3, 4, 7]);
  assert.deepEqual(ids([], { unlocked: false, recipients: false }), [1, 2, 3, 4]);
  assert.deepEqual(ids([8, 2], { unlocked: false, recipients: true }), [2, 3, 4, 8], "people already on the gift stay");
});

test("gift: the PATCH body trims, keeps the price as typed text and insists on a title and a recipient", () => {
  const f = { title: " Camera ", url: "", store: " Shop ", price: " $89.00 ", notes: "", recipientIds: [3], giverIds: [1] };
  assert.deepEqual(buildPatch(f), { title: "Camera", url: "", store: "Shop", price: "$89.00", notes: "", recipient_ids: [3], giver_ids: [1] });
  assert.throws(() => buildPatch({ ...f, title: "  " }), /title/);
  assert.throws(() => buildPatch({ ...f, recipientIds: [] }), /recipient/);
  assert.deepEqual(buildPatch({ ...f, giverIds: [] }).giver_ids, []);
});

test("gift: page escapes everything typed and only offers Santa when the flag is there", () => {
  const evil = fullGift({ title: `</textarea><script>1</script>`, notes: `</textarea><img src=x>`, store: `"><b>`, url: `"x" onfocus="y`, santa: true });
  const html = renderGiftPage(evil, people4, "<i>X</i>", true);
  assert.ok(!/<script|<img|<b>|<i>/.test(html), html);
  assert.ok(html.includes('id="santa" checked'));
  const locked = fullGift(); delete locked.santa;
  assert.ok(!renderGiftPage(locked, people4, "X", false).includes('id="santa"'));
  assert.ok(renderGiftPage(fullGift({ giver_ids: [3], track: "theirs", status: "told", told_at: "2026-11-20" }), people4, "X", true).includes("Told 2026-11-20"));
});

// ---------------------------------------------------------------- board.js
const ppl5 = [
  { id: 1, name: "Alex", is_user: true, active: true }, { id: 2, name: "Sam", is_user: true, active: true },
  { id: 3, name: "Pat", active: true }, { id: 4, name: "Lee", active: true },
  { id: 7, name: "Riley", active: true, protected: true }, { id: 8, name: "Skyler", active: true, protected: true },
];
const households = [
  { label: "Us", person_ids: [1, 2] }, { label: "Pat & Lee", person_ids: [3, 4] },
  { label: "Riley", person_ids: [7] }, { label: "Skyler & Riley", person_ids: [7, 8] },
];

test("board: the Assign menu is the households (not ones of only protected people) plus each owner alone", () => {
  assert.deepEqual(assignChoices(households, ppl5), [
    { value: "1,2", label: "Us" }, { value: "3,4", label: "Pat & Lee" }, { value: "1", label: "Alex only" }, { value: "2", label: "Sam only" }]);
  const solo = [...households, { label: "Alex", person_ids: [1] }];
  assert.equal(assignChoices(solo, ppl5).filter(c => c.value === "1").length, 1, "an owner is not offered twice");
});

test("board: assigning sends the givers the user saw as expected_giver_ids, and 'none' unassigns", () => {
  assert.deepEqual(assignBody({ giver_ids: [] }, "3,4"), { giver_ids: [3, 4], giver_name: null, expected_giver_ids: [], expected_giver_name: null });
  assert.deepEqual(assignBody({ giver_ids: [1, 2] }, "none"), { giver_ids: [], giver_name: null, expected_giver_ids: [1, 2], expected_giver_name: null });
  assert.deepEqual(assignBody({ giver_ids: [3, 4], giver_name: null }, "2"), { giver_ids: [2], giver_name: null, expected_giver_ids: [3, 4], expected_giver_name: null });
});

const bgift = (over = {}) => ({ id: 20, title: "Puzzle", status: "idea", track: "ours", price_cents: 2500, store: null, told_at: null, giver_ids: [], santa: false, ...over });
const board = (over = {}) => ({
  person: { id: 7, name: "Riley", color: "#b3263e" }, unassigned: [bgift()],
  givers: [
    { label: "Pat & Lee", giver_ids: [3, 4], is_us: false, list_text: "Pat & Lee\n\nBook", gifts: [bgift({ id: 21, title: "Book", status: "told", track: "theirs", giver_ids: [3, 4], told_at: "2026-11-20" })] },
    { label: "Us", giver_ids: [1, 2], is_us: true, list_text: "Us\n\nBelt", gifts: [bgift({ id: 22, title: "Belt", giver_ids: [1, 2], santa: true, status: "wrapped" })] },
  ],
  split: { santa: 1, us: 0, others: 1 }, evenness: [{ person_id: 7, name: "Riley", cents: 6300 }, { person_id: 8, name: "Skyler", cents: 5800 }], ...over,
});

test("board: counts and the split and evenness lines", () => {
  assert.deepEqual(countGifts(board()), { total: 3, unassigned: 1 });
  assert.equal(splitLine({ santa: 1, us: 2, others: 3 }), "Split: 1 Santa · 2 from us · 3 from others");
  assert.equal(evennessLine(board().evenness), "Side by side: Riley $63.00 · Skyler $58.00");
  assert.equal(evennessLine([]), "");
});

test("board: markup has Assign on every gift, told/confirmed buttons on theirs, Santa and no Copy on ours", () => {
  const html = renderBoard(board(), "Christmas", assignChoices(households, ppl5));
  assert.equal((html.match(/class="assign"/g) ?? []).length, 3);
  assert.ok(html.includes('data-copy="0"') && !html.includes('data-copy="1"'));
  assert.ok(html.includes('data-gift="21" data-status="told" aria-pressed="true"') && html.includes('data-status="confirmed"'));
  assert.equal((html.match(/data-santa=/g) ?? []).length, 1);
  assert.ok(html.includes('data-santa="22" checked'));
  assert.ok(html.includes("3 gifts · 1 unassigned") && html.includes('data-color="#b3263e"') && html.includes("told 2026-11-20"));
  assert.ok(/<option value="none">Unassign<\/option>/.test(html), "an assigned gift can be unassigned");
});

test("board: no Santa checkbox without the flag, and typed text is escaped", () => {
  const b = board();
  delete b.givers[1].gifts[0].santa;
  b.unassigned[0].title = `<img src=x onerror=1>`;
  b.givers[0].label = `<b>Pat</b>`;
  const html = renderBoard(b, "<i>X</i>", [{ value: "9", label: `<script>` }]);
  assert.ok(!html.includes("data-santa"));
  assert.ok(!/<img|<b>|<i>|<script/.test(html), html);
});

// ---------------------------------------------------------------- settings.js
test("settings: person payload sends the color only once it was touched, and never a blank name", () => {
  const v = { name: " Pat ", group: "", household: " Pat & Lee ", protected: false, active: undefined, color: "#9aa29c", colorTouched: false };
  assert.deepEqual(personPayload(v), { name: "Pat", group_id: null, household: "Pat & Lee", protected: false });
  assert.deepEqual(personPayload({ ...v, group: "4", active: false, colorTouched: true, color: "#112233" }),
    { name: "Pat", group_id: 4, household: "Pat & Lee", protected: false, active: false, color: "#112233" });
  assert.throws(() => personPayload({ ...v, name: "  " }), /name/);
});

test("settings: group, occasion and PIN payloads", () => {
  assert.deepEqual(groupPayload({ name: " Side A " }), { name: "Side A" });
  assert.deepEqual(groupPayload({ name: "B", sort: "3" }), { name: "B", sort_order: 3 });
  assert.deepEqual(groupPayload({ name: "B", sort: "" }), { name: "B" });
  assert.throws(() => groupPayload({ name: "B", sort: "-1" }), /whole number/);
  assert.deepEqual(occasionPayload({ name: "Christmas", kind: "christmas", date: "2026-12-25", copyFrom: "3" }),
    { name: "Christmas", date: "2026-12-25", kind: "christmas", copy_from: 3 });
  assert.deepEqual(occasionPayload({ name: "Christmas", kind: "christmas", date: "2026-12-25", copyFrom: "" }),
    { name: "Christmas", date: "2026-12-25", kind: "christmas" });
  assert.deepEqual(occasionPayload({ name: "X", date: "2026-12-25", archived: true }), { name: "X", date: "2026-12-25", archived: true });
  assert.throws(() => occasionPayload({ name: "X", date: "" }), /date/);
  assert.deepEqual(pinPayload({ pin: "4821", again: "4821", current: "" }, false), { pin: "4821" });
  assert.deepEqual(pinPayload({ pin: "4821", again: "4821", current: "1234" }, true), { pin: "4821", current: "1234" });
  assert.throws(() => pinPayload({ pin: "48", again: "48", current: "" }, false), /4 to 8/);
  assert.throws(() => pinPayload({ pin: "4821", again: "4822", current: "" }, false), /differ/);
  assert.throws(() => pinPayload({ pin: "4821", again: "4821", current: "" }, true), /current/);
});

test("settings: a locked page lists no protected household member", () => {
  const people = [{ id: 1, name: "Alex" }, { id: 2, name: "Riley", protected: true }, { id: 3, name: "Pat" }];
  const hs = [{ label: "Us", person_ids: [1, 2] }, { label: "Riley", person_ids: [2] }, { label: "Pat", person_ids: [3] }];
  assert.deepEqual(visibleHouseholds(hs, people, false), [{ label: "Us", names: ["Alex"] }, { label: "Pat", names: ["Pat"] }]);
  assert.equal(visibleHouseholds(hs, people, true).length, 3);
});

const settingsData = (over = {}) => ({
  me: { email: "alex@example.com", has_pin: false }, groups: [{ id: 1, name: "Side A", sort_order: 0 }],
  people: [{ id: 3, name: "Pat", group_id: 1, household: "Pat & Lee", color: "#1f4d3a", protected: false, active: true, is_user: false }],
  hiddenCount: 0, households: [{ label: "Pat & Lee", names: ["Pat"] }], occasions: [{ id: 1, name: "Xmas", kind: "christmas", date: "2026-12-25", archived: false, current: true }],
  copyDefault: 1, ...over,
});

test("settings: page shows the hidden count, not the names, while locked; the PIN form follows has_pin", () => {
  const html = renderSettings(settingsData({ hiddenCount: 2 }));
  assert.ok(html.includes("2 protected people are hidden while locked") && html.includes('data-act="unlock"'));
  assert.ok(!renderSettings(settingsData()).includes("hidden while locked"));
  assert.ok(html.includes("Set PIN") && !html.includes('name="current"'));
  const withPin = renderSettings(settingsData({ me: { email: "alex@example.com", has_pin: true } }));
  assert.ok(withPin.includes("Change PIN") && withPin.includes('name="current"'));
  assert.ok(html.includes('<option value="1" selected>Xmas</option>'), "a new occasion copies the current one by default");
});

test("settings: forms escape typed text, and an owner cannot be deleted", () => {
  const evil = { id: 3, name: `"><script>1</script>`, group_id: 1, household: `<b>`, color: null, protected: true, active: false, is_user: false };
  const html = renderSettings(settingsData({ people: [evil], groups: [{ id: 1, name: `<i>G</i>`, sort_order: 0 }] })) + renderPersonForm(evil, [{ id: 1, name: "<u>" }]) + renderOccasionEdit({ id: 1, name: `"><svg>`, date: "2026-12-25", archived: false });
  assert.ok(!/<script|<b>|<i>|<u>|<svg/.test(html), html);
  const owner = { ...evil, name: "Alex", is_user: true };
  assert.ok(!renderPersonForm(owner, []).includes("delete-person"));
  assert.ok(renderPersonForm({ ...owner, is_user: false }, []).includes("delete-person"));
  assert.ok(renderPersonForm(null, []).includes("Add person") && !renderPersonForm(null, []).includes('name="active"'));
});

// ---------------------------------------------------------------- lock.js
test("lock: the pad takes digits up to eight and backspace, nothing else", () => {
  assert.equal(nextEntry("", "4"), "4");
  assert.equal(nextEntry("48", "2"), "482");
  assert.equal(nextEntry("12345678", "9"), "12345678");
  assert.equal(nextEntry("482", "back"), "48");
  assert.equal(nextEntry("", "back"), "");
  assert.equal(nextEntry("48", "a"), "48");
  assert.equal(nextEntry("48", "12"), "48");
});

test("lock: idle for lock_idle_seconds locks, a second less does not", () => {
  assert.equal(idleExpired(1_000_000 + 300_000, 1_000_000, 300), true);
  assert.equal(idleExpired(1_000_000 + 299_999, 1_000_000, 300), false);
});

test("lock: the pad has ten digits, a delete key and Unlock; with no PIN it points at Settings", () => {
  const pad = renderPad();
  assert.deepEqual([...pad.matchAll(/data-key="(\d)"/g)].map(m => m[1]).sort(), ["0", "1", "2", "3", "4", "5", "6", "7", "8", "9"]);
  assert.ok(pad.includes('data-key="back"') && pad.includes('data-act="go"') && pad.includes('data-act="cancel"'));
  assert.ok(!pad.includes("<input"), "digits are never typed into a field the browser could remember");
  const none = renderNoPin();
  assert.ok(none.includes('href="#/settings"') && !none.includes("data-key"));
});

// ---------------------------------------------------------------- school mode: mode.js and school.js
import { activeRoles, isSchool, lockApplies, peopleWithRole, pickScreen, roleChoices, ROLE_SUGGESTIONS } from "../static/js/mode.js";
import { cellMap, cellPayload, cellState, columnCaption, daysUntil, newGiftPayload, renderAddGift, renderCellEditor, renderMatrix, renderPage as renderSchoolPage, whoChoices } from "../static/js/school.js";

test("school: the mode comes from /api/me, and a family instance sends no mode key", () => {
  assert.equal(isSchool({ mode: "school" }), true);
  assert.equal(isSchool({}), false);
  assert.equal(isSchool(null), false);
  assert.equal(lockApplies({ mode: "school" }), false);
  assert.equal(lockApplies({ unlocked: false }), true);
  assert.equal(lockApplies(null), false);
});

test("school: pickScreen swaps home and drops the board only in school mode", () => {
  const family = { "": "F-home", board: "F-board", gift: "F-gift" }, school = { "": "S-home" };
  assert.equal(pickScreen("", family, school, {}), "F-home");
  assert.equal(pickScreen("board", family, school, {}), "F-board");
  assert.equal(pickScreen("nope", family, school, {}), null);
  assert.equal(pickScreen("", family, school, { mode: "school" }), "S-home");
  assert.equal(pickScreen("board", family, school, { mode: "school" }), null);
  assert.equal(pickScreen("ideas", { ...family, ideas: "F-ideas" }, school, { mode: "school" }), null);
  assert.equal(pickScreen("idea", { ...family, idea: "F-idea" }, school, { mode: "school" }), null);
  assert.equal(pickScreen("gift", family, school, { mode: "school" }), "F-gift");
  assert.equal(pickScreen("nope", family, school, { mode: "school" }), null);
});

const STAFF = [
  { id: 1, name: "Ms. Avery", role: "homeroom", active: true },
  { id: 2, name: "Ms. Blake", role: "Homeroom", active: true },
  { id: 3, name: "Ms. Chen", role: "assistant", active: true },
  { id: 4, name: "Mr. Diaz", role: "bus driver", active: false },
  { id: 5, name: "Alex", role: null, active: true, is_user: true },
];

test("school: roleChoices lists the suggestions then roles in use once; activeRoles needs an active person", () => {
  assert.deepEqual(roleChoices(STAFF), [...ROLE_SUGGESTIONS, "bus driver"]);
  assert.deepEqual(roleChoices([]), ROLE_SUGGESTIONS);
  assert.deepEqual(activeRoles(STAFF), ["homeroom", "assistant"]);
  assert.deepEqual(peopleWithRole(STAFF, " HOMEROOM ").map(p => p.id), [1, 2]);
  assert.deepEqual(peopleWithRole(STAFF, "bus driver"), []);
  assert.deepEqual(peopleWithRole(STAFF, ""), []);
});

test("school: cell state and column caption", () => {
  assert.equal(cellState(undefined), "off");
  assert.equal(cellState({ done: false }), "on");
  assert.equal(cellState({ done: true }), "done");
  assert.equal(columnCaption({ recipient_count: 7, done_count: 3 }), "7 · 3 done");
});

test("school: cellPayload sends only what the form has, trimmed, and refuses a long detail", () => {
  assert.deepEqual(cellPayload({ detail: "  $35 · coffee shop ", done: true }), { detail: "$35 · coffee shop", done: true });
  assert.deepEqual(cellPayload({ detail: "" }), { detail: "" });
  assert.deepEqual(cellPayload({ done: false }), { done: false });
  assert.throws(() => cellPayload({ detail: "x".repeat(201) }), /200/);
  assert.doesNotThrow(() => cellPayload({ detail: "x".repeat(200) }));
});

test("school: newGiftPayload picks a role's active staff or one person, never an owner", () => {
  assert.deepEqual(newGiftPayload({ title: " Card $35 ", who: "role:homeroom" }, 9, STAFF), { occasion_id: 9, title: "Card $35", recipient_ids: [1, 2], giver_ids: [] });
  assert.deepEqual(newGiftPayload({ title: "Granola", who: "person:3" }, 9, STAFF).recipient_ids, [3]);
  assert.throws(() => newGiftPayload({ title: "", who: "person:3" }, 9, STAFF), /title/);
  assert.throws(() => newGiftPayload({ title: "x", who: "role:bus driver" }, 9, STAFF), /No one has that role/);
  assert.throws(() => newGiftPayload({ title: "x", who: "" }, 9, STAFF), /Pick who/);
  assert.throws(() => newGiftPayload({ title: "x", who: "person:abc" }, 9, STAFF), /Pick who/);
  assert.deepEqual(whoChoices(STAFF).map(c => c.value), ["", "role:homeroom", "role:assistant", "person:1", "person:2", "person:3"]);
});

const MATRIX = {
  occasion: { id: 9, name: "Christmas 2025", kind: "christmas", date: "2025-12-19", archived: false },
  gifts: [
    { id: 10, title: "Card $35", status: "idea", recipient_count: 2, done_count: 1 },
    { id: 11, title: "Granola", status: "idea", recipient_count: 1, done_count: 0 },
  ],
  staff: [
    { id: 1, name: "Ms. Avery", role: "homeroom", color: "#1f4d3a", active: true, plan: "individual" },
    { id: 3, name: "Ms. Chen", role: "assistant", color: null, active: false, plan: null },
  ],
  cells: [
    { gift_id: 10, person_id: 1, detail: "$35 · coffee shop", done: true },
    { gift_id: 10, person_id: 3, detail: null, done: false },
    { gift_id: 11, person_id: 3, detail: null, done: false },
  ],
};

test("school: the matrix draws staff down, gifts across, a state per cell and a count per column", () => {
  const html = renderMatrix(MATRIX, ["homeroom", "assistant"]);
  assert.equal([...html.matchAll(/<tr>/g)].length, 4, "head, two staff, total");
  assert.ok(html.includes('href="#/gift/10"') && html.includes('href="#/person/3"'));
  assert.ok(html.includes("2 · 1 done") && html.includes("1 · 0 done"));
  assert.ok(html.includes('class="cell done has-detail" data-gift="10" data-person="1"'));
  assert.ok(html.includes('class="cell on" data-gift="10" data-person="3"'));
  assert.ok(html.includes('class="cell off" data-gift="11" data-person="1"'));
  assert.ok(html.includes('aria-label="Ms. Avery, Granola: not included"'));
  assert.ok(html.includes('<th scope="row">2 staff</th><td>2</td><td>1</td>'), "the footer counts people per column");
  assert.ok(html.includes('data-color="#1f4d3a"') && html.includes("staff dim"), "color from data, inactive dimmed");
  assert.equal([...html.matchAll(/class="role-add"/g)].length, 2);
  assert.ok(!html.includes("style="));
});

test("school: the matrix says so when there is no staff or no gift, and skips role menus with no roles", () => {
  assert.ok(renderMatrix({ ...MATRIX, staff: [], cells: [] }, []).includes("No staff yet"));
  const noGifts = renderMatrix({ ...MATRIX, gifts: [], cells: [] }, []);
  assert.ok(noGifts.includes("No gifts yet") && noGifts.includes("<table"));
  assert.ok(!renderMatrix(MATRIX, []).includes("role-add"));
  assert.equal(cellMap(MATRIX).get("10:1").detail, "$35 · coffee shop");
});

test("school: everything typed is escaped in the grid, the editor, the form and the page", () => {
  const evil = `"><script>alert(1)</script>`;
  const m = {
    ...MATRIX, occasion: { ...MATRIX.occasion, name: evil },
    gifts: [{ id: 10, title: evil, status: "idea", recipient_count: 1, done_count: 0 }],
    staff: [{ id: 1, name: evil, role: evil, color: evil, active: true, plan: null }],
    cells: [{ gift_id: 10, person_id: 1, detail: evil, done: false }],
  };
  const people = [{ id: 1, name: evil, role: evil, active: true }];
  const html = renderMatrix(m, [evil]) + renderCellEditor(m.gifts[0], m.staff[0], m.cells[0]) + renderAddGift(people)
    + renderSchoolPage(m, [{ id: 9, name: evil, archived: false }], people, new Date(2025, 11, 1));
  assert.ok(!/<script|<svg|<img/.test(html), html);
  assert.ok(!html.includes('data-color="&quot;'), "a bad color is dropped, not echoed");
});

test("school: the cell editor shows the detail and done state; Remove and Close are buttons", () => {
  const html = renderCellEditor(MATRIX.gifts[0], MATRIX.staff[0], MATRIX.cells[0]);
  assert.ok(html.includes('value="$35 · coffee shop"') && html.includes('name="done" type="checkbox" checked'));
  assert.ok(html.includes('data-act="remove"') && html.includes('data-act="close"'));
  assert.ok(html.includes('data-gift="10" data-person="1"'));
});

test("school: daysUntil counts calendar days", () => {
  assert.equal(daysUntil("2025-12-19", new Date(2025, 11, 1)), 18);
  assert.equal(daysUntil("2025-12-19", new Date(2025, 11, 19)), 0);
  assert.equal(daysUntil("2025-12-19", new Date(2025, 11, 20)), -1);
});

test("school: settings forms swap Protected for Role, hide the PIN card and badge the role", () => {
  const person = { id: 3, name: "Ms. Avery", group_id: null, household: "", color: "#1f4d3a", protected: false, active: true, is_user: false, role: "homeroom" };
  const school = renderPersonForm(person, [], true);
  assert.ok(school.includes('name="role"') && school.includes('value="homeroom"') && !school.includes('name="protected"'));
  const family = renderPersonForm(person, []);
  assert.ok(!family.includes('name="role"') && family.includes('name="protected"'));
  assert.equal(renderPersonForm(person, [], false), family);
  const data = { me: { email: "alex@example.com", has_pin: false }, groups: [], people: [person], hiddenCount: 0, households: [], occasions: [], copyDefault: "" };
  const page = renderSettings({ ...data, school: true, roles: ["homeroom", "assistant"] });
  assert.ok(!page.includes("Set PIN") && !page.includes("<h3>PIN</h3>"));
  assert.ok(page.includes('<datalist id="roles"><option value="homeroom"></option>') && page.includes('<span class="badge">homeroom</span>'));
  const { role: _none, ...familyPerson } = person; // a family instance's people have no role key
  const fam = renderSettings({ ...data, people: [familyPerson] });
  assert.ok(fam.includes("Set PIN") && !fam.includes('id="roles"') && !fam.includes("homeroom"));
});

test("school: personPayload sends role only when the form has it, and never protected from a school form", () => {
  const base = { name: "Ms. Avery", group: "", household: "", color: "#000000", colorTouched: false };
  assert.deepEqual(personPayload({ ...base, protected: undefined, role: " homeroom " }), { name: "Ms. Avery", group_id: null, household: "", protected: undefined, role: "homeroom" });
  assert.equal(JSON.stringify(personPayload({ ...base, protected: undefined, role: "" })), '{"name":"Ms. Avery","group_id":null,"household":"","role":""}');
  assert.ok(!("role" in personPayload({ ...base, protected: false })));
});

// The screen's wiring, against stand-in elements (no DOM or browser here): which request each tap sends and what
// is drawn afterwards. The markup itself is checked above; this catches a wrong id or a handler on the wrong node.
import { render as renderSchoolScreen } from "../static/js/school.js";
import { state } from "../static/js/core.js";

function schoolHarness(handler) {
  const calls = [];
  globalThis.fetch = async (url, init) => {
    const call = { method: init.method, url, body: init.body ? JSON.parse(init.body) : undefined };
    calls.push(call);
    const [status, data] = handler(call);
    return { ok: status < 400, status, json: async () => data };
  };
  globalThis.document = { createElement: () => ({ setAttribute() {}, remove() {} }), body: { append(m) { toasts.push(m.textContent); } } };
  const toasts = [];
  const node = (extra = {}) => ({ innerHTML: "", handlers: {}, addEventListener(t, f) { this.handlers[t] = f; }, querySelectorAll: () => [], ...extra });
  const error = { textContent: "" };
  const input = () => ({ focus() {}, value: "" });
  const els = { "#occasion": node(), "#matrix": node(), "#editor": node(), "#add-gift": node() };
  els["#editor"].querySelector = sel => (sel === "form" ? els.editorForm : input());
  const view = { innerHTML: "", querySelector: sel => els[sel] };
  Object.assign(state, { occasionId: 9, people: STAFF, occasions: [{ id: 9, name: "Christmas 2025", archived: false, current: true }] });
  return { calls, toasts, els, error, view, ctx: { view, alive: () => true, rerender() { calls.push("rerender"); } } };
}

const tap = (giftId, personId) => ({ target: { closest: sel => (sel === "button.cell" ? { dataset: { gift: String(giftId), person: String(personId) } } : null) } });

test("school screen: tapping an empty cell adds that person and redraws the grid from the server", async () => {
  let matrixCalls = 0;
  const h = schoolHarness(c => {
    if (c.method === "GET") return [200, { ...MATRIX, gifts: MATRIX.gifts.map(g => ({ ...g, recipient_count: g.recipient_count + matrixCalls++ })) }];
    return [201, { gift_id: 11, person_id: 1, detail: null, done: false, recipient_count: 2, done_count: 0 }];
  });
  await renderSchoolScreen(h.ctx);
  assert.ok(h.view.innerHTML.includes('id="matrix"') && h.view.innerHTML.includes('id="add-gift"'));
  assert.deepEqual(h.calls[0], { method: "GET", url: "/api/occasions/9/matrix", body: undefined });
  await h.els["#matrix"].handlers.click(tap(11, 1));
  assert.deepEqual(h.calls.slice(1).map(c => `${c.method} ${c.url}`), ["PUT /api/gifts/11/recipients/1", "GET /api/occasions/9/matrix"]);
  assert.deepEqual(h.calls[1].body, {});
  assert.ok(h.els["#matrix"].innerHTML.includes('class="cell off"'), "the grid was redrawn");
  await h.els["#matrix"].handlers.click({ target: { closest: () => null } });   // a tap elsewhere does nothing
  assert.equal(h.calls.length, 3);
});

test("school screen: tapping a filled cell opens the editor, Save sends detail and done, Remove deletes", async () => {
  const h = schoolHarness(c => (c.method === "GET" ? [200, MATRIX] : [200, { ok: true }]));
  await renderSchoolScreen(h.ctx);
  await h.els["#matrix"].handlers.click(tap(10, 1));
  assert.ok(h.els["#editor"].innerHTML.includes('id="cell-editor"') && h.els["#editor"].innerHTML.includes('value="$35 · coffee shop"'));
  assert.equal(h.calls.length, 1, "opening the editor sends nothing");
  h.els.editorForm = { dataset: { gift: "10", person: "1" }, elements: { detail: { value: " $35 · bookstore " }, done: { checked: false } }, querySelector: () => h.error };
  await h.els["#editor"].handlers.submit({ preventDefault() {}, target: h.els.editorForm });
  assert.deepEqual(h.calls[1], { method: "PUT", url: "/api/gifts/10/recipients/1", body: { detail: "$35 · bookstore", done: false } });
  assert.equal(h.els["#editor"].innerHTML, "", "closed after saving");
  await h.els["#matrix"].handlers.click(tap(10, 3));
  await h.els["#editor"].handlers.click({ target: { closest: () => ({ dataset: { act: "remove" } }) } });
  assert.equal(h.calls.at(-2).method, "DELETE");
  assert.equal(h.calls.at(-2).url, "/api/gifts/10/recipients/1");
});

test("school screen: a refused removal shows the server's sentence and keeps the editor open", async () => {
  const h = schoolHarness(c => (c.method === "GET" ? [200, MATRIX] : [409, { error: "A gift needs at least one recipient. Delete the gift instead." }]));
  await renderSchoolScreen(h.ctx);
  await h.els["#matrix"].handlers.click(tap(11, 3));
  h.els.editorForm = { dataset: { gift: "11", person: "3" }, querySelector: () => h.error };
  await h.els["#editor"].handlers.click({ target: { closest: () => ({ dataset: { act: "remove" } }) } });
  assert.equal(h.error.textContent, "A gift needs at least one recipient. Delete the gift instead.");
  assert.ok(h.els["#editor"].innerHTML.includes("cell-editor"));
});

test("school screen: the role menu posts the role and the add-gift form posts a gift with its first recipients", async () => {
  const h = schoolHarness(c => {
    if (c.method === "GET") return [200, MATRIX];
    return [c.url === "/api/gifts" ? 201 : 200, c.url === "/api/gifts" ? { id: 12 } : { added: [2], recipient_count: 3, done_count: 1 }];
  });
  await renderSchoolScreen(h.ctx);
  await h.els["#matrix"].handlers.change({ target: { closest: sel => (sel === "select.role-add" ? { value: "homeroom", dataset: { gift: "11" } } : null) } });
  assert.deepEqual(h.calls[1], { method: "POST", url: "/api/gifts/11/recipients", body: { role: "homeroom" } });
  assert.deepEqual(h.toasts, ["Added 1"]);
  const form = { elements: { title: { value: "Cookies" }, who: { value: "role:assistant" } }, querySelector: () => h.error, reset() { this.wasReset = true; } };
  await h.els["#add-gift"].handlers.submit({ preventDefault() {}, target: form });
  assert.deepEqual(h.calls.find(c => c.url === "/api/gifts").body, { occasion_id: 9, title: "Cookies", recipient_ids: [3], giver_ids: [] });
  assert.equal(form.wasReset, true);
  const bad = { elements: { title: { value: "Cookies" }, who: { value: "" } }, querySelector: () => h.error };
  const before = h.calls.length;
  await h.els["#add-gift"].handlers.submit({ preventDefault() {}, target: bad });
  assert.equal(h.error.textContent, "Pick who it is for.");
  assert.equal(h.calls.length, before, "nothing is sent for an invalid form");
});

test("school screen: a new occasion with no staff, and no occasion at all, draw a notice", async () => {
  const h = schoolHarness(() => [200, { ...MATRIX, staff: [], gifts: [], cells: [] }]);
  await renderSchoolScreen(h.ctx);
  assert.ok(h.view.innerHTML.includes("No staff yet"));
  state.occasionId = null;
  const none = { innerHTML: "" };
  await renderSchoolScreen({ view: none, alive: () => true });
  assert.ok(none.innerHTML.includes("No occasion yet"));
});


// ---------------------------------------------------------------- after purchase: the lists and the counts on home
import { names as listNames, render as renderListsScreen, renderPending, renderTabs, renderToWrap, renderWhere, shortDate, tabKey, tabsFor } from "../static/js/lists.js";
import { renderCounts } from "../static/js/home.js";

const EVIL = `<img src=x onerror=alert(1)>`;
const listPeople = [{ id: 1, name: "Jordan", color: "#1f4d3a" }, { id: 2, name: `<b>Riley</b>`, color: "#3a5f9a" }];
const pendingGift = (extra = {}) => ({ id: 7, title: "Camera", status: "ordered", recipient_ids: [1], seller: "Camera shop", order_number: "1234", eta: "2026-12-09", arrived_on: null, ship_to_id: 1, ship_warning: false, overdue: false, location_id: null, location_path: null, ...extra });

test("lists: the counts row names the three lists, links them and gives Where is it? no number", () => {
  const html = renderCounts({ pending: 3, to_wrap: 9 });
  assert.ok(html.includes('<a href="#/lists/pending">Not here yet 3</a>') && html.includes('<a href="#/lists/to-wrap">To wrap 9</a>'));
  assert.ok(html.includes('<a href="#/lists/where">Where is it?</a>'));
  const summary = { occasion: { id: 1 }, days_until: 3, groups: [], protected: { locked: true, people: [] } };
  assert.ok(!renderHome(summary, [{ id: 1, name: "X", archived: false }], false).includes('class="counts"'), "no counts given: the old home");
  const home = renderHome(summary, [{ id: 1, name: "X", archived: false }], false, { pending: 0, to_wrap: 2 });
  assert.ok(home.indexOf("Idea</a>") < home.indexOf('class="counts"') && home.includes("To wrap 2"));
});

test("lists: shortDate, names and the tab set", () => {
  assert.equal(shortDate("2026-12-09"), "Dec 9");
  assert.equal(shortDate("2027-01-31"), "Jan 31");
  assert.equal(shortDate(null), "no date");
  assert.equal(shortDate("soon"), "soon");
  assert.equal(listNames([1, 2, 99], listPeople), "Jordan & <b>Riley</b>");
  assert.deepEqual(tabsFor(false).map(t => t.key), ["pending", "to-wrap", "where"]);
  assert.deepEqual(tabsFor(true).map(t => t.key), ["pending", "to-wrap"], "school mode has no hiding places");
  assert.equal(tabKey("where", true), "pending");
  assert.equal(tabKey("where", false), "where");
  assert.equal(tabKey("nonsense", false), "pending");
  const bar = renderTabs("to-wrap", false);
  assert.ok(bar.includes('href="#/lists/to-wrap" aria-current="page"') && !bar.includes('href="#/lists/pending" aria-current'));
  assert.ok(!renderTabs("pending", true).includes("Where is it?"));
});

test("lists: a row turns red and says overdue when its date has passed, and warns when it ships elsewhere", () => {
  const html = renderPending({ gifts: [pendingGift({ overdue: true, ship_warning: true }), pendingGift({ id: 8, title: "Later", eta: null })] }, listPeople);
  const [first, second] = html.split("</li>");
  assert.ok(first.includes('class="row overdue"') && first.includes("overdue</span>") && first.includes("ships elsewhere"));
  assert.ok(first.includes("Dec 9") && first.includes("Camera shop #1234") && first.includes("to Jordan"));
  assert.ok(second.includes('class="row"') && !second.includes("overdue") && second.includes("no date") && !second.includes("ships elsewhere"));
  assert.ok(renderPending({ gifts: [] }, listPeople).includes("Nothing on its way."));
});

test("lists: To wrap groups by recipient with the place, Where is it? by place, and nothing typed is left unescaped", () => {
  const wrap = renderToWrap({ groups: [{ person_id: 1, gifts: [pendingGift({ title: EVIL, location_path: `Garage › ${EVIL}` }), pendingGift({ id: 9, title: "Scarf", location_path: null })] }, { person_id: 2, gifts: [pendingGift({ id: 10 })] }] }, listPeople);
  assert.ok(wrap.includes('data-color="#1f4d3a"') && wrap.includes("<h3>Jordan</h3>") && wrap.includes("&lt;b&gt;Riley&lt;/b&gt;"));
  assert.ok(wrap.includes("no hiding place yet") && wrap.includes("Garage › &lt;img"));
  const where = renderWhere({ groups: [{ location_id: 3, path: `Hall ${EVIL}`, gifts: [pendingGift({ title: EVIL, status: "wrapped", recipient_ids: [2] })] }, { location_id: null, path: null, gifts: [pendingGift()] }] }, listPeople);
  assert.ok(where.includes("No place yet") && where.includes("wrapped") && where.includes("&lt;b&gt;Riley&lt;/b&gt;"));
  const pending = renderPending({ gifts: [pendingGift({ title: EVIL, seller: EVIL, order_number: EVIL, recipient_ids: [2] })] }, listPeople);
  for (const html of [wrap, where, pending]) assert.ok(!html.includes("<img") && !html.includes("<b>"), "raw markup in: " + html.slice(0, 80));
  assert.ok(renderToWrap({ groups: [] }, listPeople).includes("Nothing to wrap."));
  assert.ok(renderWhere({ groups: [] }, listPeople).includes("Nothing is in the house yet."));
});

test("lists: a school list has no location keys, so it names no place", () => {
  const g = pendingGift();
  delete g.location_id; delete g.location_path;
  const html = renderToWrap({ groups: [{ person_id: 1, gifts: [g] }] }, listPeople);
  assert.ok(!html.includes("no hiding place yet"));
});

function listHarness(school) {
  const calls = [];
  globalThis.fetch = async (url, init) => {
    calls.push(`${init.method} ${url}`);
    return { ok: true, status: 200, json: async () => (url.endsWith("to-wrap") || url.endsWith("where") ? { groups: [] } : { gifts: [] }) };
  };
  Object.assign(state, { me: school ? { mode: "school" } : { email: "a@example.com" }, occasionId: 9, people: listPeople, occasions: [{ id: 9, name: `<i>Christmas</i>`, archived: false }] });
  return { calls, view: { innerHTML: "" }, ctx: view => ({ view, alive: () => true, parts: [] }) };
}

test("lists screen: each tab asks for its own list; an unknown tab, and where in school mode, fall back to Not here yet", async () => {
  const h = listHarness(false);
  for (const [part, url] of [["pending", "pending"], ["to-wrap", "to-wrap"], ["where", "where"], ["bogus", "pending"], [undefined, "pending"]]) {
    h.calls.length = 0;
    await renderListsScreen({ view: h.view, alive: () => true, parts: ["lists", part] });
    assert.deepEqual(h.calls, [`GET /api/occasions/9/lists/${url}`], String(part));
  }
  assert.ok(h.view.innerHTML.includes("&lt;i&gt;Christmas&lt;/i&gt;") && h.view.innerHTML.includes("Where is it?"));
  const s = listHarness(true);
  await renderListsScreen({ view: s.view, alive: () => true, parts: ["lists", "where"] });
  assert.deepEqual(s.calls, ["GET /api/occasions/9/lists/pending"]);
  assert.ok(!s.view.innerHTML.includes("Where is it?"));
  state.occasionId = null;
  const none = { innerHTML: "" };
  await renderListsScreen({ view: none, alive: () => true, parts: ["lists"] });
  assert.ok(none.innerHTML.includes("No occasion yet"));
});


// ---------------------------------------------------------------- after purchase: the order card and the places screen
import { addressChoices, locationChoices, orderPayload, renderOrder, render as renderGiftScreen, statusBody } from "../static/js/gift.js";
import { parentChoices, renderAddresses, renderLocations, renderPlaces, render as renderPlacesScreen } from "../static/js/places.js";

const ADDRESSES = [{ id: 1, label: "Home", is_home: true }, { id: 2, label: "Grandparents' house", is_home: false }];
const LOCATIONS = [
  { id: 1, parent_id: null, name: "Garage", sort_order: 0, path: "Garage", depth: 0 },
  { id: 2, parent_id: 1, name: "Top shelf", sort_order: 0, path: "Garage › Top shelf", depth: 1 },
  { id: 3, parent_id: 2, name: "Blue bin", sort_order: 0, path: "Garage › Top shelf › Blue bin", depth: 2 },
  { id: 4, parent_id: null, name: "Hall closet", sort_order: 1, path: "Hall closet", depth: 0 },
];
const orderGift = (over = {}) => fullGift({ seller: "Camera shop", order_number: "1234", ship_to_id: 1, ship_elsewhere_ok: false, eta: "2026-12-09", arrived_on: null, ordered_by: "Alex", ship_warning: false, location_id: null, location_path: null, ...over });
const form = (over = {}) => ({ seller: "  Camera shop ", order_number: " 1234 ", ship_to_id: "2", ship_elsewhere_ok: true, eta: "2026-12-09", arrived_on: "", location_id: "3", ...over });

test("order card: orderPayload trims, sends no address as null, and leaves the hiding place out in school mode", () => {
  assert.deepEqual(orderPayload(form()), { seller: "Camera shop", order_number: "1234", ship_to_id: 2, ship_elsewhere_ok: true, eta: "2026-12-09", arrived_on: "", location_id: 3 });
  assert.equal(orderPayload(form({ ship_to_id: "", location_id: "" })).ship_to_id, null);
  assert.equal(orderPayload(form({ location_id: "" })).location_id, null);
  assert.ok(!("location_id" in orderPayload(form(), true)));
});

test("order card: only ordered and arrived carry the order fields to /status, and never the hiding place", () => {
  assert.deepEqual(statusBody("wrapped", form()), { status: "wrapped" });
  assert.deepEqual(statusBody("ordered", null), { status: "ordered" }, "no order card: the status alone");
  const ordered = statusBody("ordered", form());
  assert.deepEqual(ordered, { status: "ordered", seller: "Camera shop", order_number: "1234", ship_to_id: 2, ship_elsewhere_ok: true, eta: "2026-12-09", arrived_on: "" });
  assert.deepEqual(statusBody("arrived", form({ arrived_on: "2026-12-08" })).arrived_on, "2026-12-08");
  assert.ok(!("location_id" in ordered));
});

test("order card: the pickers offer the addresses (home marked) and the places by path", () => {
  assert.deepEqual(addressChoices(ADDRESSES).map(c => c.label), ["Pick an address", "Home (home)", "Grandparents' house"]);
  assert.deepEqual(locationChoices(LOCATIONS).map(c => c.label), ["Not hidden yet", "Garage", "Garage › Top shelf", "Garage › Top shelf › Blue bin", "Hall closet"]);
  const html = renderOrder(orderGift({ ship_to_id: 2, location_id: 3 }), ADDRESSES, LOCATIONS, false);
  assert.ok(html.includes('<option value="2" selected>Grandparents&#39; house</option>') && html.includes('<option value="3" selected>Garage › Top shelf › Blue bin</option>'));
  assert.ok(html.includes('value="Camera shop"') && html.includes('value="2026-12-09"') && html.includes("Ordered by Alex"));
});

test("order card: a ship-to that is not home shows the warning with the address named, a home one does not", () => {
  const warn = renderOrder(orderGift({ ship_to_id: 2, ship_warning: true }), ADDRESSES, LOCATIONS, false);
  assert.ok(warn.includes("Ships to Grandparents&#39; house, not home.") && warn.includes('role="alert"'));
  assert.ok(!renderOrder(orderGift(), ADDRESSES, LOCATIONS, false).includes("not home"));
  assert.ok(renderOrder(orderGift({ ship_to_id: 2, ship_warning: false, ship_elsewhere_ok: true }), ADDRESSES, LOCATIONS, false).includes(" checked>"));
});

test("order card: school mode has no hiding place field; everything typed is escaped", () => {
  assert.ok(!renderOrder(orderGift(), ADDRESSES, LOCATIONS, true).includes('name="location_id"'));
  assert.ok(renderOrder(orderGift(), ADDRESSES, LOCATIONS, false).includes('name="location_id"'));
  const evil = renderOrder(orderGift({ seller: EVIL, order_number: `"><script>`, ordered_by: EVIL, ship_to_id: 2, ship_warning: true }),
    [{ id: 2, label: EVIL, is_home: false }], [{ id: 1, path: EVIL }], false);
  assert.ok(!evil.includes("<img") && !evil.includes("<script"), evil.slice(0, 120));
});

test("order card: the gift page draws it only when given the places and only for a gift from an owner", () => {
  const places = { addresses: ADDRESSES, locations: LOCATIONS, school: false };
  assert.ok(renderGiftPage(orderGift(), people4, "X", true, places).includes('id="order"'));
  assert.ok(!renderGiftPage(orderGift(), people4, "X", true).includes('id="order"'), "the old four-argument call is unchanged");
  assert.ok(!renderGiftPage(orderGift({ track: "theirs", giver_ids: [3], status: "told" }), people4, "X", true, places).includes('id="order"'));
});

function giftHarness({ school = false, gift = orderGift(), handler }) {
  const calls = [];
  globalThis.fetch = async (url, init) => {
    const call = { method: init.method, url, body: init.body ? JSON.parse(init.body) : undefined };
    calls.push(call);
    const reply = handler(call);
    if (reply) return { ok: reply[0] < 400, status: reply[0], json: async () => reply[1] };
    const data = { "/api/addresses": ADDRESSES, "/api/locations": LOCATIONS }[url] ?? gift;
    return { ok: true, status: 200, json: async () => data };
  };
  const toasts = [];
  globalThis.document = { createElement: () => ({ setAttribute() {}, remove() {} }), body: { append(m) { toasts.push(m.textContent); } } };
  const node = (extra = {}) => ({ innerHTML: "", handlers: {}, textContent: "", addEventListener(t, f) { this.handlers[t] = f; }, ...extra });
  const orderForm = { elements: { seller: { value: "Camera shop" }, order_number: { value: "1234" }, ship_to_id: { value: "2" }, ship_elsewhere_ok: { checked: false }, eta: { value: "2026-12-09" }, arrived_on: { value: "" }, ...(school ? {} : { location_id: { value: "3" } }) } };
  const els = { "#steps": node(), "#santaBox": node(), "#statusError": node(), "#orderBox": node(), "#order": orderForm, "#orderError": node(), "#fields": node({ elements: {} }), "#formError": node(), "#delete": node() };
  const view = { innerHTML: "", querySelector: sel => els[sel] ?? null };
  Object.assign(state, { me: school ? { mode: "school" } : { email: "a@example.com", unlocked: true }, people: people4, occasions: [{ id: 1, name: "Christmas" }] });
  return { calls, toasts, els, view, ctx: { view, alive: () => true, parts: ["gift", "10"], rerender() {} } };
}
const stepTap = status => ({ target: { closest: () => ({ dataset: { status } }) } });

test("gift screen: Mark ordered posts the card's values to /status, and refreshes the card from the answer", async () => {
  const h = giftHarness({ gift: orderGift({ status: "decided", seller: null, ship_to_id: null, eta: null, ordered_by: null }),
    handler: c => (c.method === "POST" ? [200, orderGift({ status: "ordered", ordered_by: "Alex" })] : null) });
  await renderGiftScreen(h.ctx);
  assert.deepEqual(h.calls.map(c => `${c.method} ${c.url}`).sort(), ["GET /api/addresses", "GET /api/gifts/10", "GET /api/giver-names", "GET /api/locations"]);
  assert.ok(h.view.innerHTML.includes('id="order"') && h.view.innerHTML.includes('id="orderBox"'));
  await h.els["#steps"].handlers.click(stepTap("ordered"));
  const post = h.calls.at(-1);
  assert.deepEqual([post.method, post.url], ["POST", "/api/gifts/10/status"]);
  assert.deepEqual(post.body, { status: "ordered", seller: "Camera shop", order_number: "1234", ship_to_id: 2, ship_elsewhere_ok: false, eta: "2026-12-09", arrived_on: "" });
  assert.ok(h.els["#orderBox"].innerHTML.includes("Ordered by Alex"));
});

test("gift screen: a refused Mark ordered shows the server's sentence and sends no location", async () => {
  const h = giftHarness({ gift: orderGift({ status: "decided" }), handler: c => (c.method === "POST" ? [400, { error: "Marking a gift ordered needs seller, eta.", missing: ["seller", "eta"] }] : null) });
  await renderGiftScreen(h.ctx);
  await h.els["#steps"].handlers.click(stepTap("ordered"));
  assert.equal(h.els["#statusError"].textContent, "Marking a gift ordered needs seller, eta.");
  assert.ok(!("location_id" in h.calls.at(-1).body));
});

test("gift screen: other statuses send the status alone; Save order details patches the card, hiding place included", async () => {
  const h = giftHarness({ handler: c => (c.method === "POST" ? [200, orderGift({ status: "wrapped" })] : c.method === "PATCH" ? [200, orderGift({ location_id: 3 })] : null) });
  await renderGiftScreen(h.ctx);
  await h.els["#steps"].handlers.click(stepTap("wrapped"));
  assert.deepEqual(h.calls.at(-1).body, { status: "wrapped" });
  await h.els["#orderBox"].handlers.submit({ preventDefault() {} });
  const patch = h.calls.at(-1);
  assert.deepEqual([patch.method, patch.url], ["PATCH", "/api/gifts/10"]);
  assert.deepEqual(patch.body, { seller: "Camera shop", order_number: "1234", ship_to_id: 2, ship_elsewhere_ok: false, eta: "2026-12-09", arrived_on: "", location_id: 3 });
  assert.deepEqual(h.toasts, ["Saved"]);
});

test("gift screen: school mode never asks for or sends hiding places", async () => {
  const h = giftHarness({ school: true, handler: c => (c.method === "PATCH" ? [200, orderGift()] : null) });
  await renderGiftScreen(h.ctx);
  assert.ok(!h.calls.some(c => c.url === "/api/locations"), "school mode does not fetch the places");
  assert.ok(!h.calls.some(c => c.url === "/api/giver-names"), "nor typed giver suggestions");
  await h.els["#orderBox"].handlers.submit({ preventDefault() {} });
  assert.ok(!("location_id" in h.calls.at(-1).body));
});

test("places: parentChoices offers everything except a place and what is inside it", () => {
  assert.deepEqual(parentChoices(LOCATIONS, 1).map(c => c.label), ["Top level", "Hall closet"]);
  assert.deepEqual(parentChoices(LOCATIONS, 2).map(c => c.label), ["Top level", "Garage", "Hall closet"]);
  assert.deepEqual(parentChoices(LOCATIONS, 4).map(c => c.label), ["Top level", "Garage", "Garage › Top shelf", "Garage › Top shelf › Blue bin"]);
  assert.equal(parentChoices(LOCATIONS, null).length, 5, "a new place may go anywhere");
});

test("places: the home address cannot be deleted or made home again; hiding places show by path; school mode has none", () => {
  const html = renderAddresses(ADDRESSES);
  const [home, other] = html.split("</li>");
  assert.ok(home.includes("home</span>") && !home.includes("make-home") && !home.includes("delete-address"));
  assert.ok(other.includes('data-act="make-home" data-id="2"') && other.includes('data-act="delete-address" data-id="2"'));
  const locs = renderLocations(LOCATIONS);
  assert.ok(locs.includes("Garage › Top shelf › Blue bin") && locs.includes('data-act="move" data-id="2"'));
  assert.ok(locs.includes('<option value="1" selected>Garage</option>'), "the move menu shows the current parent");
  assert.ok(renderPlaces(ADDRESSES, LOCATIONS, false).includes("Hiding places"));
  assert.ok(!renderPlaces(ADDRESSES, LOCATIONS, true).includes("Hiding places"));
  const evil = renderPlaces([{ id: 5, label: EVIL, is_home: false }], [{ id: 1, parent_id: null, name: EVIL, path: EVIL }], false);
  assert.ok(!evil.includes("<img"));
});

function placesHarness(school = false) {
  const calls = [];
  globalThis.fetch = async (url, init) => {
    calls.push(`${init.method} ${url}${init.body && init.body !== "{}" ? ` ${init.body}` : ""}`);
    return { ok: true, status: 200, json: async () => (url === "/api/addresses" ? ADDRESSES : url === "/api/locations" ? LOCATIONS : { ok: true }) };
  };
  globalThis.document = { createElement: () => ({ setAttribute() {}, remove() {} }), body: { append() {} } };
  Object.assign(state, { me: school ? { mode: "school" } : { email: "a@example.com" } });
  const view = { innerHTML: "", handlers: {}, addEventListener(t, f) { this.handlers[t] = f; } };
  let redraws = 0;
  return { calls, view, redraws: () => redraws, ctx: { view, alive: () => true, rerender() { redraws += 1; } } };
}
const btn = (act, id, label) => ({ target: { closest: sel => (sel === "[data-act]" ? { dataset: { act, id: String(id), label } } : null) } });

test("places screen: each button sends one request and redraws; school mode asks only for addresses", async () => {
  const h = placesHarness();
  await renderPlacesScreen(h.ctx);
  assert.deepEqual(h.calls.slice().sort(), ["GET /api/addresses", "GET /api/locations"]);
  h.calls.length = 0;
  await h.view.handlers.click(btn("make-home", 2));
  globalThis.confirm = () => true;
  await h.view.handlers.click(btn("delete-address", 2));
  await h.view.handlers.click(btn("delete-location", 3));
  globalThis.prompt = () => " Attic ";
  await h.view.handlers.click(btn("rename-location", 4, "Hall closet"));
  assert.deepEqual(h.calls, ["PATCH /api/addresses/2 {\"is_home\":true}", "DELETE /api/addresses/2", "DELETE /api/locations/3", "PATCH /api/locations/4 {\"name\":\" Attic \"}"]);
  assert.equal(h.redraws(), 4);
  globalThis.confirm = () => false;
  await h.view.handlers.click(btn("delete-location", 1));
  assert.equal(h.calls.length, 4, "declining the confirmation sends nothing");
  await h.view.handlers.change({ target: { closest: () => ({ dataset: { act: "move", id: "3" }, value: "" }) } });
  await h.view.handlers.change({ target: { closest: () => ({ dataset: { act: "move", id: "3" }, value: "4" }) } });
  assert.deepEqual(h.calls.slice(4), ['PATCH /api/locations/3 {"parent_id":null}', 'PATCH /api/locations/3 {"parent_id":4}']);
  const add = { preventDefault() {}, target: { dataset: { form: "location-add" }, elements: { name: { value: "Attic" }, parent_id: { value: "" } } } };
  await h.view.handlers.submit(add);
  assert.equal(h.calls.at(-1), 'POST /api/locations {"name":"Attic","parent_id":null}');
  const s = placesHarness(true);
  await renderPlacesScreen(s.ctx);
  assert.deepEqual(s.calls, ["GET /api/addresses"]);
});

// ---------------------------------------------------------------- agent tokens and the notes they leave on a gift
import { day, renderCreated, renderTokenRows, renderTokens, tokenLine, tokenPayload, render as renderTokensScreen } from "../static/js/tokens.js";
import { agentNote, renderAgentLog } from "../static/js/gift.js";

const TOKENS = [
  { id: 2, name: "Owner", scope: "full", unlocked: true, created_at: 1800000000, last_used_at: null, revoked_at: null },
  { id: 1, name: "Shipping", scope: "delivery", unlocked: false, created_at: 1800000000, last_used_at: 1800086400, revoked_at: 1800172800 },
];

test("tokens: the payload trims, needs a name, and asks to see protected gifts only for full outside school mode", () => {
  assert.deepEqual(tokenPayload({ name: "  Shipping ", scope: "delivery", unlocked: true }), { name: "Shipping", scope: "delivery" });
  assert.deepEqual(tokenPayload({ name: "Owner", scope: "full", unlocked: true }), { name: "Owner", scope: "full", unlocked: true });
  assert.deepEqual(tokenPayload({ name: "Owner", scope: "full", unlocked: false }), { name: "Owner", scope: "full" });
  assert.deepEqual(tokenPayload({ name: "Owner", scope: "full", unlocked: true }, true), { name: "Owner", scope: "full" });
  assert.throws(() => tokenPayload({ name: "  ", scope: "shop" }), /needs a name/);
});

test("tokens: the list escapes names, marks revoked tokens and offers revoke only for live ones", () => {
  assert.equal(day(1800000000), "2027-01-15");
  assert.equal(day(null), "");
  assert.equal(tokenLine(TOKENS[0]), "made 2027-01-15 · never used");
  assert.equal(tokenLine(TOKENS[1]), "made 2027-01-15 · last used 2027-01-16 · revoked 2027-01-17");
  const [owner, shipping] = renderTokenRows(TOKENS).split("</li>");
  assert.ok(owner.includes("sees protected") && owner.includes('data-act="revoke" data-id="2"') && !owner.includes(" dim"));
  assert.ok(shipping.includes("revoked</span>") && !shipping.includes('data-act="revoke"') && shipping.includes("row dim"));
  const evil = renderTokenRows([{ ...TOKENS[0], name: EVIL, scope: EVIL }]);
  assert.ok(!evil.includes("<img"));
  assert.ok(renderTokenRows([]).includes("No tokens yet."));
});

test("tokens: a new token is shown once, escaped, with the warning; school mode has no sees-protected box", () => {
  const made = renderCreated({ name: EVIL, token: "gt_abc<def" });
  assert.ok(made.includes("It will not be shown again.") && made.includes("gt_abc&lt;def") && !made.includes("<img"));
  assert.equal(renderCreated(null), "");
  assert.ok(renderTokens(TOKENS, false).includes('name="unlocked"'));
  assert.ok(!renderTokens(TOKENS, true).includes('name="unlocked"'));
  assert.ok(!renderTokens(TOKENS, false).includes("gt_"), "a listed token never shows the token itself");
});

test("tokens screen: making a token posts the form and draws the token once; revoke asks first", async () => {
  const calls = [];
  globalThis.fetch = async (url, init) => {
    calls.push(`${init.method} ${url}${init.body && init.body !== "{}" ? ` ${init.body}` : ""}`);
    const data = init.method === "POST" ? { id: 3, name: "Buyer", scope: "shop", token: "gt_secretvalue" } : init.method === "DELETE" ? TOKENS[0] : TOKENS;
    return { ok: true, status: init.method === "POST" ? 201 : 200, json: async () => data };
  };
  globalThis.document = { createElement: () => ({ setAttribute() {}, remove() {} }), body: { append() {} } };
  Object.assign(state, { me: { email: "a@example.com", unlocked: true } });
  let redraws = 0;
  const view = { innerHTML: "", handlers: {}, addEventListener(t, f) { this.handlers[t] = f; } };
  await renderTokensScreen({ view, alive: () => true, rerender() { redraws += 1; } });
  assert.deepEqual(calls, ["GET /api/tokens"]);
  const form = { dataset: { form: "token-add" }, querySelector: () => ({ textContent: "" }),
    elements: { name: { value: " Buyer " }, scope: { value: "shop" }, unlocked: { checked: true } } };
  await view.handlers.submit({ preventDefault() {}, target: form });
  assert.deepEqual(calls.slice(1), ['POST /api/tokens {"name":"Buyer","scope":"shop"}', "GET /api/tokens"]);
  assert.ok(view.innerHTML.includes("gt_secretvalue") && view.innerHTML.includes("will not be shown again"));
  globalThis.confirm = () => false;
  await view.handlers.click(btn("revoke", 2));
  assert.equal(calls.length, 3, "declining sends nothing");
  globalThis.confirm = () => true;
  await view.handlers.click(btn("revoke", 2));
  assert.deepEqual([calls.at(-1), redraws], ["DELETE /api/tokens/2", 1]);
});

test("order card: agent notes are listed, escaped, and absent when there are none", () => {
  const log = [{ at: 1800000000, token: EVIL, action: "status: arrived" }];
  assert.equal(agentNote({ at: 1800000000, token: "Shipping", action: "order: eta" }), "Shipping · order: eta · 2027-01-15");
  const html = renderAgentLog({ agent_log: log });
  assert.ok(html.includes("Agent notes") && html.includes("status: arrived") && !html.includes("<img"));
  assert.equal(renderAgentLog({ agent_log: [] }), "");
  assert.equal(renderAgentLog({}), "");
  const places = { addresses: ADDRESSES, locations: LOCATIONS, school: false };
  assert.ok(renderGiftPage(orderGift({ agent_log: log }), people4, "X", true, places).includes("Agent notes"));
});


// ---------------------------------------------------------------- gift exchanges
import { budgetText, drawerChoices, drawLine, exchangePayload, giftPayload, recipientChoices, renderExchange, renderExchanges, render as renderExchangesScreen, undrawn } from "../static/js/exchanges.js";
import { renderExchangesSection } from "../static/js/home.js";

const exPeople = [
  { id: 1, name: "Alex", color: "#1f4d3a", is_user: true, protected: false, active: true },
  { id: 3, name: `<b>Jordan</b>`, color: "#3a5f9a", is_user: false, protected: false, active: true },
  { id: 6, name: "Kid 1", color: "#c0392b", is_user: false, protected: true, active: true },
  { id: 7, name: "Old", color: null, is_user: false, protected: false, active: false },
];
const exDraw = (over = {}) => ({ drawer_id: 1, recipient_id: 3, gift_id: null, gift_title: null, gift_status: null, price_cents: null, over_budget: false, ...over });
const exchange = (over = {}) => ({ id: 4, name: `<i>Secret Santa</i>`, budget_cents: 2500, date: "2026-12-20", draws: [exDraw()], ...over });

test("exchanges: a draw says who drew whom, escapes names and titles, and offers a gift form only while it has none", () => {
  const bare = drawLine(exchange(), exDraw(), exPeople);
  assert.ok(bare.includes("Alex drew &lt;b&gt;Jordan&lt;/b&gt;") && bare.includes("no gift yet") && bare.includes('data-form="gift-add"'));
  assert.ok(!bare.includes("over budget") && !bare.includes("<b>"));
  const done = drawLine(exchange(), exDraw({ gift_id: 9, gift_title: EVIL, gift_status: "idea", price_cents: 2500 }), exPeople);
  assert.ok(done.includes('href="#/gift/9"') && done.includes("&lt;img src=x") && !done.includes(EVIL));
  assert.ok(done.includes("$25.00") && !done.includes("over budget") && !done.includes('data-form="gift-add"'), "at the budget is not over");
  assert.ok(drawLine(exchange(), exDraw({ gift_id: 9, gift_title: "Watch", gift_status: "idea", price_cents: 2501, over_budget: true }), exPeople).includes('<span class="badge warn">over budget</span>'));
});

test("exchanges: no markup carries a style attribute or an inline handler", () => {
  const html = renderExchanges([exchange({ draws: [exDraw({ gift_id: 9, gift_title: "x", gift_status: "idea", price_cents: 1, over_budget: true })] })], exPeople, EVIL, true);
  assert.ok(!/\sstyle=|\sonclick=|<script/i.test(html));
  assert.ok(html.includes("&lt;img src=x onerror=alert(1)&gt;") && html.includes('data-color="#1f4d3a"'));
});

test("exchanges: a locked page offers no protected person; unlocked it offers protected drawers", () => {
  assert.deepEqual(drawerChoices(exPeople, false).map(p => p.id), [1]);
  assert.deepEqual(drawerChoices(exPeople, true).map(p => p.id), [1, 6]);
  assert.deepEqual(recipientChoices(exPeople, false, 1).map(p => p.id), [3]);
  assert.deepEqual(recipientChoices(exPeople, true, 1).map(p => p.id), [3, 6], "inactive people and the drawer are not offered");
  assert.deepEqual(undrawn(exchange(), exPeople, true).map(p => p.id), [6]);
  const locked = renderExchange(exchange({ draws: [] }), exPeople, false);
  assert.ok(!locked.includes("Kid 1"));
  assert.ok(renderExchange(exchange({ draws: [] }), exPeople, true).includes("Kid 1"));
});

test("exchanges: exchangePayload sends cents, null for blanks, and refuses a bad amount", () => {
  assert.deepEqual(exchangePayload({ name: "  Office  ", budget: "$25", date: "2026-12-20" }), { name: "Office", budget_cents: 2500, date: "2026-12-20" });
  assert.deepEqual(exchangePayload({ name: "A", budget: "", date: "" }), { name: "A", budget_cents: null, date: null });
  assert.throws(() => exchangePayload({ name: "A", budget: "lots", date: "" }), /amount/);
  assert.deepEqual(giftPayload(9, 4, exDraw(), { title: " Mug ", price: " 20 " }),
    { occasion_id: 9, title: "Mug", price: "20", recipient_ids: [3], giver_ids: [1], exchange_id: 4 });
  assert.equal(giftPayload(9, 4, exDraw(), { title: "Mug", price: "" }).price, null);
  assert.equal(budgetText(null), "no limit");
  assert.equal(budgetText(2500), "$25.00 limit");
});

test("home: the exchanges section is empty without exchanges and links to the screen with an over-budget count", () => {
  assert.equal(renderExchangesSection(undefined), "");
  assert.equal(renderExchangesSection([]), "");
  const html = renderExchangesSection([exchange({ draws: [exDraw({ over_budget: true }), exDraw({ drawer_id: 2 })] })]);
  assert.ok(html.includes('href="#/exchanges"') && html.includes("2 draws") && html.includes("$25.00") && html.includes("1 over budget"));
  assert.ok(html.includes("&lt;i&gt;Secret Santa&lt;/i&gt;") && !html.includes("<i>"));
  assert.ok(!renderExchangesSection([exchange({ draws: [exDraw()], budget_cents: null })]).includes("over budget"));
  const summary = { occasion: { id: 1 }, days_until: 3, groups: [], protected: { locked: true, people: [] } };
  assert.ok(!renderHome(summary, [{ id: 1, name: "X", archived: false }], false).includes("Exchanges"), "no exchanges key: the old home");
  assert.ok(renderHome({ ...summary, exchanges: [exchange()] }, [{ id: 1, name: "X", archived: false }], false).includes("Exchanges"));
});

test("exchanges screen: asks for the current occasion's exchanges and draws them", async () => {
  const calls = [];
  globalThis.fetch = async (url, init) => {
    calls.push(`${init.method} ${url}`);
    return { ok: true, status: 200, json: async () => [exchange()] };
  };
  Object.assign(state, { me: { email: "a@example.com", unlocked: false }, occasionId: 9, people: exPeople, occasions: [{ id: 9, name: "Xmas", archived: false }] });
  const view = { innerHTML: "", addEventListener() {} };
  await renderExchangesScreen({ view, alive: () => true, parts: ["exchanges"], rerender() {} });
  assert.deepEqual(calls, ["GET /api/occasions/9/exchanges"]);
  assert.ok(view.innerHTML.includes("Exchanges") && !view.innerHTML.includes("Kid 1"));
  state.occasionId = null;
  const none = { innerHTML: "", addEventListener() {} };
  await renderExchangesScreen({ view: none, alive: () => true, parts: ["exchanges"], rerender() {} });
  assert.ok(none.innerHTML.includes("No occasion yet"));
});

// ---------------------------------------------------------------- board.js: the wishlist link
import { ageText, aliasBody, renderWishlist, safeHref, wishBadge, wishlistLine } from "../static/js/board.js";

const wlWish = (over = {}) => ({ id: 11, name: "Lego set", url: "https://example.com/lego", store: "Toy Shop", price_cents: 4999,
  occasion: "Christmas", secret: false, reserved_by: null, purchased: false, purchased_by: null, ...over });
const wlBlock = (over = {}) => ({ linked: true, tab_id: 3, reachable: true, fetched_at: 1800000000, age_seconds: 30,
  tabs: [{ id: 3, name: "Kid 1", person_id: 7 }], open: [wlWish()], reserved: [], unmatched: [], ...over });
const wlPeople = [{ id: 3, name: "Pat", active: true }, { id: 9, name: "Gone", active: false }];

test("wishlist: only http(s) links are followed", () => {
  assert.equal(safeHref("https://example.com/a"), "https://example.com/a");
  assert.equal(safeHref("HTTP://example.com/a"), "HTTP://example.com/a");
  for (const bad of ["javascript:alert(1)", "data:text/html,x", " https://example.com", "//example.com", "", null, undefined, 5]) {
    assert.equal(safeHref(bad), "", String(bad));
  }
});

test("wishlist: the age line says when it was read, and what is shown while it cannot be reached", () => {
  assert.equal(ageText(30), "just now");
  assert.equal(ageText(120), "2 min ago");
  assert.equal(ageText(7200), "2 h ago");
  assert.equal(ageText(3 * 86400), "3 days ago");
  assert.equal(ageText(null), "");
  assert.equal(wishlistLine(wlBlock()), "Wishlist checked just now.");
  assert.equal(wishlistLine(wlBlock({ reachable: false, age_seconds: 7200 })),
    "The wishlist cannot be reached. Showing what it said 2 h ago; Assign is off for wishes.");
  assert.equal(wishlistLine(wlBlock({ reachable: false, fetched_at: null, age_seconds: null })),
    "The wishlist cannot be reached, and nothing has been read from it yet.");
});

test("wishlist: the badge on a linked gift says whether the wishlist agrees", () => {
  const w = (over) => ({ wish: { item_id: 11, listed: true, reserved_by: "Us", purchased: false, purchased_by: null, in_sync: true, ...over } });
  assert.equal(wishBadge({}), "");
  assert.equal(wishBadge(w({})), "On the wishlist");
  assert.equal(wishBadge(w({ purchased: true, purchased_by: "Pat" })), "Bought on the wishlist");
  assert.equal(wishBadge(w({ in_sync: false, reserved_by: "Grandma" })), "Wishlist says reserved by Grandma");
  assert.equal(wishBadge(w({ in_sync: false, purchased: true, purchased_by: "Lee" })), "Wishlist says bought by Lee");
  assert.equal(wishBadge(w({ in_sync: false, reserved_by: null })), "Wishlist says nobody has it");
  assert.equal(wishBadge(w({ listed: false, in_sync: false })), "Not in the wishlist feed");
});

test("wishlist: an open wish has one Add to ideas button; an unmatched name maps to a person", () => {
  const html = renderWishlist(wlBlock(), wlPeople);
  assert.ok(html.includes('<button type="button" class="btn small" data-wish="11">Add to ideas</button>'), html);
  assert.ok(!html.includes("<select class=\"assign\" data-wish"), "no select for a wish");
  assert.deepEqual(aliasBody({ kind: "name", value: "uncle rob", label: "Uncle Rob" }, "3"), { kind: "name", value: "uncle rob", person_id: 3 });
});

test("wishlist: everything from the wishlist is escaped and a bad link is not a link", () => {
  const html = renderWishlist(wlBlock({
    linked: false, tabs: [{ id: 3, name: "<i>Tab</i>", person_id: null }],
    open: [wlWish({ name: "<img src=x onerror=1>", url: "javascript:alert(1)", store: "<b>S</b>" })],
    reserved: [wlWish({ id: 12, url: null, name: "<script>x</script>", reserved_by: "<b>R</b>", person_id: null, label: "<b>R</b>" })],
    unmatched: [{ kind: "name", value: "<b>r</b>", label: "<script>R</script>" }],
  }), wlPeople);
  assert.ok(!/<img|<b>|<i>|<script/.test(html), html);
  assert.ok(!html.includes("javascript:") && !html.includes("href="), "a non-http(s) link is plain text");
  assert.ok(html.includes("data-wl-tab") && html.includes("data-alias=\"0\"") && html.includes("data-wl-refresh"));
  assert.ok(html.includes('<option value="3">Pat</option>') && !html.includes(">Gone<"), "only active people to map to");
  const linked = renderWishlist(wlBlock(), wlPeople);
  assert.ok(linked.includes('href="https://example.com/lego"') && linked.includes('rel="noopener noreferrer"'));
  assert.ok(!linked.includes("data-wl-tab"), "a linked board does not ask for its tab");
  assert.ok(linked.includes("$49.99") && linked.includes("Toy Shop · Christmas"));
});

test("wishlist: while it cannot be reached, Assign is off for wishes and linked gifts only", () => {
  const offline = renderWishlist(wlBlock({ reachable: false }), wlPeople);
  assert.ok(/data-wish="11"[^>]* disabled>/.test(offline), offline);
  assert.ok(!/data-wish="11"[^>]* disabled>/.test(renderWishlist(wlBlock(), wlPeople)));
  const b = board({ wishlist: wlBlock({ reachable: false, open: [] }) });
  b.unassigned[0].wish = { item_id: 11, listed: true, reserved_by: null, purchased: false, purchased_by: null, in_sync: true };
  const html = renderBoard(b, "Christmas", [], wlPeople);
  assert.ok(/data-gift="20"[^>]* disabled>/.test(html), "the linked gift");
  assert.ok(!/data-gift="21"[^>]* disabled>/.test(html) && !/data-gift="22"[^>]* disabled>/.test(html), "gifts typed in by hand");
  assert.ok(html.includes("On the wishlist") && html.includes('class="badge wish"'));
});

test("wishlist: a board without the link renders as it did", () => {
  const html = renderBoard(board(), "Christmas", assignChoices(households, ppl5));
  assert.ok(!html.includes("wishlist") && !html.includes("Wishlist") && !html.includes("disabled") && !html.includes("data-wish"));
  assert.equal((html.match(/class="assign"/g) ?? []).length, 3);
});

// ---------------------------------------------------------------- ideas.js
import { copyState, editBody, filterIdeas, kidsIds, renderIdeas, renderIdeasPage, shareQuery, takeBody } from "../static/js/ideas.js";

const ideaPeople = [{ id: 1, name: "Alex", is_user: true, active: true }, { id: 3, name: "Pat", active: true, color: "#1f4d3a" },
  { id: 7, name: "Riley", active: true, protected: true }, { id: 8, name: "Casey", active: false, protected: true },
  { id: 9, name: "Jordan", active: true, protected: true }];
const copy = (person_id, over = {}) => ({ person_id, state: "open", gift_id: null, occasion_id: null, held_by: null, ...over });
const anIdea = (over = {}) => ({ id: 4, title: "Kite", url: "https://example.com/kite", store: "Toy Shop", price_cents: 1999, notes: null,
  share: false, wishlist_item_id: null, created_by: "alex@example.com", updated_at: 1800000000, people: [copy(3), copy(7)], ...over });

test("core: giverText is the people's label and the typed name joined by ' & '", () => {
  assert.equal(giverText({ giver_ids: [], giver_name: "Uncle <b>" }, ideaPeople), "Uncle <b>");
  assert.equal(giverText({ giver_ids: [3], giver_name: "Uncle Casey" }, ideaPeople), "Pat & Uncle Casey");
  assert.equal(giverText({ giver_ids: [3], giver_name: null }, ideaPeople), "Pat");
  assert.equal(giverText({ giver_ids: [3] }, ideaPeople), "Pat");
  assert.equal(giverText({ giver_ids: [] }, ideaPeople), "");
});

test("ideas: copyState reads each person's copy", () => {
  const i = anIdea({ people: [copy(3), copy(7, { state: "taken", gift_id: 12, occasion_id: 2 }), copy(9, { state: "held", held_by: "Grandma" })] });
  assert.equal(copyState(i, 3), "open");
  assert.equal(copyState(i, 7), "taken");
  assert.equal(copyState(i, 9), "held");
  assert.equal(copyState(i, 99), null, "not tagged: no copy");
});

test("ideas: filterIdeas keeps the ideas tagged with that person; null keeps all", () => {
  const list = [anIdea({ id: 1, people: [copy(3)] }), anIdea({ id: 2, people: [copy(7), copy(3)] }), anIdea({ id: 3, people: [copy(9)] })];
  assert.deepEqual(filterIdeas(list, null).map(i => i.id), [1, 2, 3]);
  assert.deepEqual(filterIdeas(list, 3).map(i => i.id), [1, 2]);
  assert.deepEqual(filterIdeas(list, 9).map(i => i.id), [3]);
  assert.deepEqual(filterIdeas(list, 5), []);
});

test("ideas: the kids are the active protected people; the share query names them", () => {
  assert.deepEqual(kidsIds(ideaPeople), [7, 9]);
  assert.deepEqual(kidsIds([{ id: 1, active: true }]), []);
  assert.equal(shareQuery(null), "/api/ideas/share");
  assert.equal(shareQuery([7, 9]), "/api/ideas/share?people=7,9");
});

test("ideas: the take body sends the chosen givers, the typed name when there is one, and the occasion", () => {
  assert.deepEqual(takeBody("1,2", "  ", "5"), { giver_ids: [1, 2], occasion_id: 5 });
  assert.deepEqual(takeBody("", " Uncle Casey ", "5"), { giver_ids: [], giver_name: "Uncle Casey", occasion_id: 5 });
  assert.deepEqual(takeBody("3", "Aunt Jo", ""), { giver_ids: [3], giver_name: "Aunt Jo" });
});

test("ideas: the edit body sends every field, so a cleared one is cleared", () => {
  assert.deepEqual(editBody(ideaForm({ title: " Kite ", url: "", store: " Shop ", price: " 19.99 ", notes: "" }, [3, 7])),
    { title: "Kite", url: "", store: "Shop", price: "19.99", notes: "", person_ids: [3, 7] });
});

test("ideas: each copy says open, taken (with the occasion) or held (with the holder); only open ones can be taken", () => {
  const i = anIdea({ people: [copy(3), copy(7, { state: "taken", gift_id: 12, occasion_id: 2 }), copy(9, { state: "held", held_by: "Grandma" })] });
  const html = renderIdeas([i], ideaPeople, [{ id: 2, name: "Christmas 2026" }]);
  assert.ok(html.includes("Christmas 2026") && html.includes('href="#/gift/12"'));
  assert.ok(html.includes("on the wishlist: Grandma"));
  assert.equal((html.match(/data-take=/g) ?? []).length, 1);
  assert.ok(html.includes('data-take="4" data-person="3"') && html.includes("Take for Pat…"));
  assert.ok(html.includes('data-color="#1f4d3a"') && html.includes('href="https://example.com/kite"') && html.includes("$19.99"));
  assert.ok(html.includes('data-share="4"') && !/data-share="4"[^>]* checked/.test(html), "share off");
  assert.ok(/data-share="4"[^>]* checked/.test(renderIdeas([anIdea({ share: true })], ideaPeople)), "share on");
  assert.ok(html.includes('data-delete="4"') && html.includes('data-form="idea-edit"'));
});

test("ideas: titles, names, stores, notes and holders are escaped; a bad link is not a link", () => {
  const evil = anIdea({ title: "<b>Kite</b>", store: "<i>S</i>", notes: "<script>x</script>", url: "javascript:alert(1)",
    people: [copy(3, { state: "held", held_by: "<b>Gran</b>" })] });
  const html = renderIdeas([evil], ideaPeople.map(p => (p.id === 3 ? { ...p, name: "<b>Pat</b>" } : p)));
  assert.ok(!/<b>|<i>|<script/.test(html), html);
  assert.ok(html.includes("&lt;b&gt;Kite&lt;/b&gt;") && html.includes("&lt;b&gt;Gran&lt;/b&gt;"));
  assert.ok(!html.includes('href="javascript:'));
  assert.ok(renderIdeas([], ideaPeople).includes("No ideas yet."));
});

test("ideas: the page has the person filter, the copy buttons and a datalist of names", () => {
  const html = renderIdeasPage({ ideas: [anIdea()], people: ideaPeople, occasions: [], personId: 3, names: ["Uncle <b>"] });
  assert.ok(html.includes('id="idea-person"') && html.includes('<option value="3" selected>Pat</option>'));
  assert.ok(html.includes('data-copy="person"') && html.includes('data-copy="kids"') && html.includes('data-copy="everyone"'));
  assert.ok(html.includes('<datalist id="giver-names"><option value="Uncle &lt;b&gt;"></option></datalist>'));
  assert.ok(html.includes('href="#/idea?for=3"'));
  const all = renderIdeasPage({ ideas: [], people: [ideaPeople[0], ideaPeople[1]], occasions: [], personId: null, names: [] });
  assert.ok(!all.includes('data-copy="person"') && !all.includes('data-copy="kids"') && all.includes('data-copy="everyone"'));
  assert.ok(all.includes('href="#/idea"'));
});

import { render as renderIdeasScreen } from "../static/js/ideas.js";

// A screen test replaces fetch, document, navigator and the shared state; this puts them back when the test ends, so
// the tests after it see what the tests before it saw.
function isolate(t) {
  const saved = { fetch: globalThis.fetch, document: globalThis.document, confirm: globalThis.confirm,
    navigator: Object.getOwnPropertyDescriptor(globalThis, "navigator"), state: { ...state } };
  t.after(() => {
    globalThis.fetch = saved.fetch;
    globalThis.document = saved.document;
    globalThis.confirm = saved.confirm;
    if (saved.navigator) Object.defineProperty(globalThis, "navigator", saved.navigator);
    else delete globalThis.navigator;
    for (const key of Object.keys(state)) delete state[key];
    Object.assign(state, saved.state);
  });
}

test("ideas screen: loads ideas, households and names; Copy list and Take send what they should", async t => {
  isolate(t);
  const calls = [];
  globalThis.fetch = async (url, init) => {
    calls.push({ method: init.method, url, body: init.body ? JSON.parse(init.body) : undefined });
    const data = url === "/api/ideas" ? [anIdea()] : url === "/api/households" ? [{ label: "Us", person_ids: [1] }]
      : url === "/api/giver-names" ? ["Aunt Jo"] : url.startsWith("/api/ideas/share") ? { text: "Pat\n- Kite" } : { id: 50 };
    return { ok: true, status: init.method === "POST" ? 201 : 200, json: async () => data };
  };
  const toasts = [];
  globalThis.document = { createElement: () => ({ setAttribute() {}, remove() {} }), body: { append(m) { toasts.push(m.textContent); } } };
  const copied = [];
  Object.defineProperty(globalThis, "navigator", { value: { clipboard: { writeText: async t => copied.push(t) } }, configurable: true });
  Object.assign(state, { me: { unlocked: true }, people: ideaPeople, occasions: [{ id: 2, name: "Christmas 2026", archived: false }], occasionId: 2 });
  const handlers = {};
  const view = { innerHTML: "", addEventListener: (t, f) => { handlers[t] = f; }, querySelector: () => ({}) };
  let redraws = 0;
  await renderIdeasScreen({ view, query: { person: "3" }, alive: () => true, rerender: () => { redraws++; } });
  assert.deepEqual(calls.map(c => c.url), ["/api/ideas", "/api/households", "/api/giver-names"]);
  assert.ok(view.innerHTML.includes("Kite") && view.innerHTML.includes('data-copy="person"'));

  const button = dataset => ({ closest: sel => (sel === "[data-copy]" && dataset.copy ? { dataset } : null) });
  await handlers.click({ target: button({ copy: "kids" }) });
  await handlers.click({ target: button({ copy: "person" }) });
  assert.deepEqual(calls.slice(3).map(c => c.url), ["/api/ideas/share?people=7,9", "/api/ideas/share?people=3"]);
  assert.deepEqual(copied, ["Pat\n- Kite", "Pat\n- Kite"]);

  const error = { textContent: "" };
  const form = { dataset: { form: "take", idea: "4", person: "3" }, elements: { giver: { value: "" }, giver_name: { value: " Aunt Jo " }, occasion: { value: "2" } },
    querySelector: sel => (sel === ".form-error" ? error : { disabled: false }) };
  await handlers.submit({ preventDefault() {}, target: form });
  assert.deepEqual(calls.at(-1), { method: "POST", url: "/api/ideas/4/take/3", body: { giver_ids: [], giver_name: "Aunt Jo", occasion_id: 2 } });
  assert.equal(redraws, 1);
  form.elements.giver_name.value = "";
  const before = calls.length;
  await handlers.submit({ preventDefault() {}, target: form });
  assert.equal(calls.length, before, "no giver: nothing sent");
  assert.equal(error.textContent, "Say who is giving it.");
});

// ---------------------------------------------------------------- typed givers, ideas on the board, back and move to ideas
import { renderNameForm } from "../static/js/board.js";
import { render as renderBoardScreen } from "../static/js/board.js";
import { render as renderGiftScreenAgain } from "../static/js/gift.js";

test("board: 'Someone else' assigns a typed name; the body carries the gift's current givers and name as expected", () => {
  assert.deepEqual(assignBody({ giver_ids: [3], giver_name: "Aunt Jo" }, "other", " Uncle Casey "),
    { giver_ids: [], giver_name: "Uncle Casey", expected_giver_ids: [3], expected_giver_name: "Aunt Jo" });
  assert.deepEqual(assignBody({ giver_ids: [], giver_name: "Aunt Jo" }, "1,2"),
    { giver_ids: [1, 2], giver_name: null, expected_giver_ids: [], expected_giver_name: "Aunt Jo" });
  assert.deepEqual(assignBody({ giver_ids: [], giver_name: "Aunt Jo" }, "none"),
    { giver_ids: [], giver_name: null, expected_giver_ids: [], expected_giver_name: "Aunt Jo" });
});

test("gift: the PATCH body sends the typed giver trimmed, null when the box is cleared, and nothing without the box", () => {
  const f = { title: "Camera", url: "", store: "", price: "", notes: "", recipientIds: [3], giverIds: [] };
  assert.equal(buildPatch({ ...f, giverName: " Uncle Casey " }).giver_name, "Uncle Casey");
  assert.equal(buildPatch({ ...f, giverName: "  " }).giver_name, null);
  assert.equal(buildPatch({ ...f, giverName: "" }).giver_name, null);
  assert.ok(!("giver_name" in buildPatch(f)), "school mode has no box and sends no key");
});

const boardIdea = (over = {}) => ({ id: 40, title: "Kite", url: "https://example.com/kite", store: "Toy Shop", price_cents: 1999,
  notes: null, share: false, wishlist_item_id: null, people: [{ person_id: 7, state: "open", gift_id: null, occasion_id: null, held_by: null }],
  copy_state: "open", ...over });

test("board: an Ideas section above the giver groups, an Assign menu per open idea, held ones dim with the holder", () => {
  const b = board({ ideas: [boardIdea({ title: "<b>Kite</b>" }),
    boardIdea({ id: 41, title: "Drone", url: null, copy_state: "held", people: [{ person_id: 7, state: "held", gift_id: null, occasion_id: null, held_by: "<i>Gran</i>" }] })] });
  const html = renderBoard(b, "Christmas", assignChoices(households, ppl5), ppl5, ["Aunt <b>Jo</b>"]);
  assert.ok(!/<b>|<i>/.test(html), html);
  assert.ok(html.includes("<h3>Ideas</h3>") && html.indexOf("<h3>Ideas</h3>") < html.indexOf("board-group"));
  assert.ok(html.includes("&lt;b&gt;Kite&lt;/b&gt;") && html.includes("on the wishlist: &lt;i&gt;Gran&lt;/i&gt;"));
  assert.equal((html.match(/data-idea="40"/g) ?? []).length, 1, "one Assign menu for the open idea");
  assert.ok(!html.includes('data-idea="41"'), "a held idea has no Assign");
  assert.ok(/<li class="row dim"[^>]*>\s*<div class="main"><div class="name">Drone/.test(html), "held is dim");
  assert.ok(/data-idea="40"[^>]*>.*<option value="other">Someone else…<\/option>/.test(html));
  assert.ok(html.includes('<datalist id="giver-names"><option value="Aunt &lt;b&gt;Jo&lt;/b&gt;"></option></datalist>'));
  assert.ok(html.includes('data-name-slot="idea-40"'));
});

test("board: a typed giver renders escaped; Unassign on an idea gift is Back to ideas; old giverless gifts can move to ideas", () => {
  const b = board();
  b.givers[0] = { ...b.givers[0], label: "Pat & <i>x", gifts: [bgift({ id: 21, giver_ids: [3], giver_name: "<i>x", track: "theirs", status: "told" })] };
  b.givers[1].gifts[0].from_idea = true;
  b.unassigned[0].from_idea = false;
  const html = renderBoard(b, "Christmas", assignChoices(households, ppl5), ppl5);
  assert.ok(!/<i>/.test(html), html);
  assert.ok(/data-gift="22"[^>]*>.*<option value="back">Back to ideas<\/option>/.test(html) && !/data-gift="22"[^>]*>.*value="none"/.test(html));
  assert.ok(/data-gift="21"[^>]*>.*<option value="none">Unassign<\/option>/.test(html), "an ordinary gift still unassigns");
  assert.ok(/data-gift="21"[^>]*><option value="" selected>Reassign…/.test(html), "a typed giver counts as assigned");
  assert.ok(html.includes('data-to-idea="20"') && html.includes("Move to ideas"));
  assert.equal((html.match(/data-to-idea=/g) ?? []).length, 1);
  assert.ok(/data-gift="20"[^>]*>.*<option value="other">Someone else…<\/option>/.test(html));
  const empty = renderBoard(board({ unassigned: [] }), "Christmas", []);
  assert.ok(!empty.includes("<h3>Unassigned</h3>") && !empty.includes("<h3>Ideas</h3>"), "nothing to show: no section");
});

test("Move to ideas only for a gift still at the idea status, on the board and the gift page", () => {
  for (const status of ["decided", "ordered", "arrived", "wrapped", "given"]) {
    const html = renderBoard(board({ unassigned: [bgift({ status })] }), "Christmas", assignChoices(households, ppl5), ppl5);
    assert.ok(!html.includes("data-to-idea="), status);
    const page = renderGiftPage(fullGift({ giver_ids: [], giver_name: null, from_idea: false, status }), people4, "X", true);
    assert.ok(!page.includes('id="to-idea"'), status);
  }
  const html = renderBoard(board({ unassigned: [bgift({ status: "idea" })] }), "Christmas", assignChoices(households, ppl5), ppl5);
  assert.ok(html.includes('data-to-idea="20"'));
  const page = renderGiftPage(fullGift({ giver_ids: [], giver_name: null, from_idea: false, status: "idea" }), people4, "X", true);
  assert.ok(page.includes('id="to-idea"'));
});

test("board: the inline name form has the datalist, a bounded box and OK", () => {
  const html = renderNameForm("gift", 20);
  assert.ok(html.includes('data-form="giver-name" data-kind="gift" data-id="20"'));
  assert.ok(html.includes('name="giver_name"') && html.includes('list="giver-names"') && html.includes('maxlength="80"'));
  assert.ok(html.includes(">OK</button>") && html.includes("data-cancel-name"));
});

test("gift: Back to ideas for a gift from an idea, Move to ideas for an old giverless one, neither in school mode", () => {
  const fromIdea = renderGiftPage(fullGift({ giver_ids: [1], giver_name: null, from_idea: true }), people4, "X", true);
  assert.ok(fromIdea.includes('id="back-to-ideas"') && fromIdea.includes("Back to ideas") && !fromIdea.includes('id="to-idea"'));
  const loose = renderGiftPage(fullGift({ giver_ids: [], giver_name: null, from_idea: false, status: "idea" }), people4, "X", true);
  assert.ok(loose.includes('id="to-idea"') && loose.includes("Move to ideas") && !loose.includes('id="back-to-ideas"'));
  const named = renderGiftPage(fullGift({ giver_ids: [], giver_name: "Aunt Jo", from_idea: false }), people4, "X", true);
  assert.ok(!named.includes('id="to-idea"') && !named.includes('id="back-to-ideas"'), "a gift with a giver moves nowhere");
  const school = renderGiftPage(fullGift({ giver_ids: [] }), people4, "X", true);
  assert.ok(!school.includes('id="to-idea"') && !school.includes('name="giver_name"'), "no family keys: no typed giver, no moves");
});

test("gift: From has a Someone else box with suggestions, and the sub-line names the typed giver, escaped", () => {
  const html = renderGiftPage(fullGift({ giver_ids: [3], giver_name: `<i>Uncle</i> "C"`, from_idea: false, track: "theirs", status: "told" }),
    people4, "X", true, null, ["Aunt <b>Jo</b>"]);
  assert.ok(!/<i>|<b>/.test(html), html);
  assert.ok(html.includes(`name="giver_name" type="text" maxlength="80" list="giver-names" value="&lt;i&gt;Uncle&lt;/i&gt; &quot;C&quot;"`));
  assert.ok(html.includes('<datalist id="giver-names"><option value="Aunt &lt;b&gt;Jo&lt;/b&gt;"></option></datalist>'));
  assert.ok(html.includes("from Pat &amp; &lt;i&gt;Uncle&lt;/i&gt; &quot;C&quot;"));
});

test("person: the person's open ideas as rows like gifts with Copy list, the typed giver on a gift row, and no + Idea in school mode", () => {
  const person = { id: 3, name: "Pat", color: null, household: null, active: true, protected: false };
  const occasion = { id: 1, name: "Christmas" };
  const ideasForPat = [boardIdea({ id: 40, title: "<b>Kite</b>", store: "<u>Shop</u>", price_cents: 1999, share: true, people: [{ person_id: 3, state: "open", gift_id: null, occasion_id: null, held_by: null }] }),
    boardIdea({ id: 41, title: "Drone", people: [{ person_id: 3, state: "taken", gift_id: 9, occasion_id: 1, held_by: null }] })];
  const g = gift({ giver_ids: [], giver_name: "<i>Aunt</i>" });
  const html = renderPage(person, "", occasion, null, null, [g], [], people4, { ideas: ideasForPat, names: ["Jo"] });
  assert.ok(!/<b>|<i>/.test(html), html);
  assert.ok(html.includes("<h3>Ideas</h3>") && html.includes("&lt;b&gt;Kite&lt;/b&gt;") && !html.includes("Drone"), "open ideas only");
  assert.ok(html.includes('<a class="main" href="#/ideas?person=3&amp;idea=40">') && !html.includes("data-take"), "the row opens the idea");
  assert.ok(html.includes('<span class="badge">idea</span> &lt;u&gt;Shop&lt;/u&gt; · shared') && html.includes("$19.99"));
  assert.ok(html.includes("data-copy-ideas") && html.includes("data-copy-box"));
  assert.ok(html.includes("from &lt;i&gt;Aunt&lt;/i&gt;"));
  assert.ok(html.includes('href="#/idea?for=3"'));
  const school = renderPage(person, "", occasion, null, null, [], [], people4, { school: true });
  assert.ok(!school.includes("+ Idea") && !school.includes("#/idea") && !school.includes("<h3>Ideas</h3>") && !school.includes("data-copy-ideas"));
});

test("home: the Ideas link sits next to + Idea, with or without the counts", () => {
  const summary = { occasion: { id: 1 }, days_until: 3, groups: [], protected: { locked: true, people: [] } };
  const bare = renderHome(summary, [{ id: 1, name: "X", archived: false }], false);
  assert.ok(bare.includes('href="#/ideas"'));
  assert.ok(bare.indexOf('href="#/idea"') < bare.indexOf('href="#/ideas"'));
  const counted = renderHome(summary, [{ id: 1, name: "X", archived: false }], false, { pending: 1, to_wrap: 2 });
  assert.equal((counted.match(/href="#\/ideas"/g) ?? []).length, 1, "once, not also in the counts row");
});

function boardHarness(t, boardData) {
  isolate(t);
  const calls = [];
  globalThis.fetch = async (url, init) => {
    calls.push({ method: init.method, url, body: init.body ? JSON.parse(init.body) : undefined });
    const data = url.startsWith("/api/occasions/1/board/7") && init.method === "GET" ? boardData
      : url === "/api/households" ? households : url === "/api/giver-names" ? ["Aunt Jo"] : { id: 99 };
    return { ok: true, status: init.method === "POST" ? 201 : 200, json: async () => data };
  };
  globalThis.document = { createElement: () => ({ setAttribute() {}, remove() {} }), body: { append() {} } };
  Object.assign(state, { me: { unlocked: true }, people: ppl5, occasions: [{ id: 1, name: "Christmas", archived: false }], occasionId: 1 });
  const handlers = {};
  const slots = {};
  const view = { innerHTML: "", addEventListener: (t2, f) => { handlers[t2] = f; },
    querySelector: sel => (slots[sel] ??= { innerHTML: "", querySelector: () => ({ focus() {} }) }) };
  const ctx = { view, parts: ["board", "7"], alive: () => true, rerender() { ctx.redraws++; }, redraws: 0 };
  return { calls, handlers, slots, view, ctx };
}
const pick = (attrs, value) => ({ value, dataset: attrs, matches: sel => sel.includes(Object.keys(attrs)[0]) && sel.includes("select") });

test("board screen: Back to ideas, Move to ideas, Add to ideas, assigning an idea and a typed name send what they should", async t => {
  const b = board({ ideas: [boardIdea()], wishlist: wlBlock() });
  b.givers[1].gifts[0].from_idea = true;
  const h = boardHarness(t, b);
  await renderBoardScreen(h.ctx);
  assert.deepEqual(h.calls.map(c => c.url).sort(), ["/api/giver-names", "/api/households", "/api/occasions/1/board/7"]);

  await h.handlers.change({ target: pick({ gift: "22" }, "back") });
  assert.deepEqual(h.calls.at(-1), { method: "POST", url: "/api/gifts/22/back-to-ideas", body: {} });

  const button = dataset => ({ closest: sel => (sel === `[${Object.keys(dataset).map(k => `data-${k.replace(/[A-Z]/g, c => `-${c.toLowerCase()}`)}`)[0]}]` ? { dataset } : null) });
  await h.handlers.click({ target: button({ toIdea: "20" }) });
  assert.deepEqual(h.calls.at(-1), { method: "POST", url: "/api/gifts/20/to-idea", body: {} });
  await h.handlers.click({ target: button({ wish: "11" }) });
  assert.deepEqual(h.calls.at(-1), { method: "POST", url: "/api/occasions/1/board/7/wishes/11", body: {} });

  await h.handlers.change({ target: pick({ idea: "40" }, "1,2") });
  assert.deepEqual(h.calls.at(-1), { method: "POST", url: "/api/ideas/40/take/7", body: { giver_ids: [1, 2], occasion_id: 1 } });

  const before = h.calls.length;
  await h.handlers.change({ target: pick({ idea: "40" }, "other") });
  assert.equal(h.calls.length, before, "Someone else asks for the name first");
  assert.ok(h.slots['[data-name-slot="idea-40"]'].innerHTML.includes('data-form="giver-name"'));
  const error = { textContent: "" };
  const form = kind => ({ dataset: { form: "giver-name", kind, id: kind === "idea" ? "40" : "20" }, elements: { giver_name: { value: " Uncle Casey " } },
    querySelector: sel => (sel === ".form-error" ? error : { disabled: false }) });
  await h.handlers.submit({ preventDefault() {}, target: form("idea") });
  assert.deepEqual(h.calls.at(-1), { method: "POST", url: "/api/ideas/40/take/7", body: { giver_ids: [], giver_name: "Uncle Casey", occasion_id: 1 } });
  await h.handlers.submit({ preventDefault() {}, target: form("gift") });
  assert.deepEqual(h.calls.at(-1), { method: "POST", url: "/api/gifts/20/assign",
    body: { giver_ids: [], giver_name: "Uncle Casey", expected_giver_ids: [], expected_giver_name: null } });
  const empty = form("gift");
  empty.elements.giver_name.value = "  ";
  const n = h.calls.length;
  await h.handlers.submit({ preventDefault() {}, target: empty });
  assert.equal(h.calls.length, n, "no name: nothing sent");
  assert.equal(error.textContent, "Type a name.");
});

test("gift screen: Back to ideas and Move to ideas post to their routes and leave the page; the Save sends the typed giver", async t => {
  isolate(t);
  globalThis.location = { hash: "#/gift/10" };
  t.after(() => { delete globalThis.location; });
  for (const [over, id, path] of [[{ giver_ids: [1], giver_name: null, from_idea: true }, "#back-to-ideas", "back-to-ideas"],
    [{ giver_ids: [], giver_name: null, from_idea: false, status: "idea" }, "#to-idea", "to-idea"]]) {
    const h = giftHarness({ gift: orderGift(over), handler: () => null });
    const button = { handlers: {}, addEventListener(type, f) { this.handlers[type] = f; } };
    const base = h.view.querySelector;
    h.view.querySelector = sel => (sel === id ? button : base(sel));
    state.prev = "#/board/7";
    await renderGiftScreenAgain(h.ctx);
    assert.ok(h.view.innerHTML.includes(`id="${id.slice(1)}"`));
    await button.handlers.click();
    assert.deepEqual(h.calls.at(-1), { method: "POST", url: `/api/gifts/10/${path}`, body: {} });
    assert.equal(globalThis.location.hash, "#/board/7");
  }
  const h = giftHarness({ gift: orderGift({ giver_ids: [], giver_name: "Aunt Jo", from_idea: false }), handler: () => null });
  const checks = [];
  h.els["#fields"] = { ...h.els["#fields"], querySelectorAll: () => checks, handlers: {},
    addEventListener(type, f) { this.handlers[type] = f; },
    elements: { title: { value: "Camera" }, url: { value: "" }, store: { value: "" }, price: { value: "" }, notes: { value: "" }, giver_name: { value: "  " } } };
  checks.push({ value: "3" });
  await renderGiftScreenAgain(h.ctx);
  await h.els["#fields"].handlers.submit({ preventDefault() {} });
  assert.equal(h.calls.at(-1).method, "PATCH");
  assert.equal(h.calls.at(-1).body.giver_name, null, "a cleared box clears the typed giver");
});
