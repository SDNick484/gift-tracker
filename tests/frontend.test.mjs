// Frontend unit tests: `node --test tests/frontend.test.mjs`. The modules under static/js/ are ES modules with
// no top-level DOM access, so they load in Node as they are; only the DOM-free helpers are tested here.
import assert from "node:assert/strict";
import { test } from "node:test";

import { ApiError, events, get, send } from "../static/js/api.js";
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
