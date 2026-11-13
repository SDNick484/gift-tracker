// Frontend unit tests: `node --test tests/frontend.test.mjs`. The modules under static/js/ are ES modules with
// no top-level DOM access, so they load in Node as they are; only the DOM-free helpers are tested here.
import assert from "node:assert/strict";
import { test } from "node:test";

import { ApiError, events, get, send } from "../static/js/api.js";
import { backTarget, forChoices, fromChoices, render as renderIdeaScreen } from "../static/js/idea.js";
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
