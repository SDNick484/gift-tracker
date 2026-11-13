// Frontend unit tests: `node --test tests/frontend.test.mjs`. The modules under static/js/ are ES modules with
// no top-level DOM access, so they load in Node as they are; only the DOM-free helpers are tested here.
import assert from "node:assert/strict";
import { test } from "node:test";

import { ApiError, events, get, send } from "../static/js/api.js";
import { backTarget, forChoices, fromChoices, render as renderIdeaScreen } from "../static/js/idea.js";
import { findEntry, giftLine, groupByOccasion, renderPage, totalsText } from "../static/js/person.js";
import { buildPatch, canSanta, pickList, renderPage as renderGiftPage, renderSteps, stepClass } from "../static/js/gift.js";
import { assignBody, assignChoices, countGifts, evennessLine, renderBoard, splitLine } from "../static/js/board.js";
import { groupPayload, occasionPayload, personPayload, pinPayload, renderOccasionEdit, renderPersonForm, renderSettings, visibleHouseholds } from "../static/js/settings.js";
import { idleExpired, nextEntry, renderNoPin, renderPad } from "../static/js/lock.js";
import { amountText, occasionChoices, planLabel, renderHome } from "../static/js/home.js";
import { applyColors, centsToText, daysLabel, dollarsToCents, esc, giverLabel, money, parseHash, statusSteps } from "../static/js/core.js";

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

test("idea: From offers me first, the other owner, then both (the default)", () => {
  const owners = [{ id: 1, name: "Alex" }, { id: 2, name: "Sam" }];
  assert.deepEqual(fromChoices(owners, 2), [
    { value: "2", label: "Sam (me)", ids: [2] }, { value: "1", label: "Alex", ids: [1] }, { value: "1,2", label: "Both", ids: [1, 2] }]);
  assert.deepEqual(fromChoices(owners, null).map(c => c.label), ["Alex", "Sam", "Both"]);
  assert.deepEqual(fromChoices([owners[0]], 1), [{ value: "1", label: "Alex (me)", ids: [1] }]);
});

test("idea: Save returns to where the user came from, never to the idea form itself", () => {
  assert.equal(backTarget("#/person/4"), "#/person/4");
  assert.equal(backTarget("#/idea?for=3"), "#/");
  assert.equal(backTarget(""), "#/");
});

test("idea: with no occasion there is nothing to save into", async () => {
  const view = { innerHTML: "" };
  await renderIdeaScreen({ view, query: {}, alive: () => true });
  assert.ok(view.innerHTML.includes("Create an occasion first."));
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
  assert.deepEqual(assignBody({ giver_ids: [] }, "3,4"), { giver_ids: [3, 4], expected_giver_ids: [] });
  assert.deepEqual(assignBody({ giver_ids: [1, 2] }, "none"), { giver_ids: [], expected_giver_ids: [1, 2] });
  assert.deepEqual(assignBody({ giver_ids: [3, 4] }, "2"), { giver_ids: [2], expected_giver_ids: [3, 4] });
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
