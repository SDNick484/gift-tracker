// Shared helpers and the small amount of state every screen reads. No top-level DOM access, so Node can load it.
import { get } from "./api.js";

// ---------------------------------------------------------------- escaping and formatting
// The regex uses hex escapes for the quote characters so the escaping check in tests/test_frontend.py can scan
// this file without mistaking a quote inside a regex for the start of a string.
const ESC = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };
export function esc(s) {
  if (s === null || s === undefined) return "";
  return String(s).replace(/[&<>\x22\x27]/g, c => ESC[c]);
}

export function money(cents) {
  if (!Number.isInteger(cents)) return "";
  const abs = Math.abs(cents);
  const dollars = String(Math.floor(abs / 100)).replace(/\B(?=(\d{3})+(?!\d))/g, ",");
  return `${cents < 0 ? "-" : ""}$${dollars}.${String(abs % 100).padStart(2, "0")}`;
}

export function daysLabel(n) {
  if (n === 0) return "today";
  const d = Math.abs(n);
  return `${d} ${d === 1 ? "day" : "days"}${n < 0 ? " ago" : ""}`;
}

const OURS = ["idea", "decided", "ordered", "arrived", "wrapped", "given"];
const THEIRS = ["told", "confirmed", "given"];
export function statusSteps(track) {
  return (track === "theirs" ? THEIRS : OURS).slice();
}

const AMOUNT = /^\$?(\d{1,3}(,\d{3})+|\d+)(\.\d{1,2})?$/;
// Dollars typed by a person -> integer cents, the same shapes the server's parse_cents accepts. Empty is null.
export function dollarsToCents(text) {
  const raw = (text ?? "").trim();
  if (!raw) return null;
  const m = AMOUNT.exec(raw);
  if (!m) throw new Error("Enter an amount such as 29.99.");
  const [dollars, frac = ""] = raw.replace(/[$,]/g, "").split(".");
  return Number(dollars) * 100 + Number(frac.padEnd(2, "0"));
}

// Integer cents -> the text for an input: 2999 -> "29.99".
export function centsToText(cents) {
  if (!Number.isInteger(cents)) return "";
  return `${Math.floor(cents / 100)}.${String(cents % 100).padStart(2, "0")}`;
}

// "#/idea?for=3" -> { parts: ["idea"], query: { for: "3" } }
export function parseHash(hash) {
  const [path, qs = ""] = (hash || "").replace(/^#\/?/, "").split("?");
  const query = {};
  for (const [k, v] of new URLSearchParams(qs)) query[k] = v;
  return { parts: path.split("/").filter(Boolean), query };
}

// "Us" when the givers are every owner, otherwise their names joined with " & ". "" for no giver.
export function giverLabel(giverIds, people) {
  const byId = new Map(people.map(p => [p.id, p]));
  const givers = giverIds.map(id => byId.get(id)).filter(Boolean);
  if (!givers.length) return "";
  const owners = people.filter(p => p.is_user).map(p => p.id);
  if (owners.length > 1 && owners.length === giverIds.length && owners.every(id => giverIds.includes(id))) return "Us";
  return givers.map(p => p.name).join(" & ");
}

// ---------------------------------------------------------------- markup helpers (every one returns escaped markup)
const HEX = /^#[0-9a-fA-F]{6}$/;

// ` data-color="#rrggbb"` for a person's color, or "". applyColors() turns it into a CSS variable after the
// markup is in the page: the Content-Security-Policy forbids inline style attributes.
export function renderColor(color) {
  return HEX.test(color ?? "") ? ` data-color="${esc(color)}"` : "";
}

export function applyColors(root) {
  for (const el of root.querySelectorAll("[data-color]")) {
    if (HEX.test(el.dataset.color ?? "")) el.style.setProperty("--c", el.dataset.color);
  }
}

export function renderChecked(on) {
  return on ? " checked" : "";
}

export function renderDisabled(on) {
  return on ? " disabled" : "";
}

export function renderSelected(on) {
  return on ? " selected" : "";
}

// <option>s from [{ value, label }]; `selected` is compared as text.
export function renderOptions(items, selected) {
  return items.map(i => `<option value="${esc(i.value)}"${renderSelected(i.value !== null && String(i.value) === String(selected))}>${esc(i.label)}</option>`).join("");
}

const ICONS = {
  plus: '<path d="M12 5v14M5 12h14" stroke-width="2.2"/>',
  lock: '<rect x="5" y="11" width="14" height="9" rx="2"/><path d="M8 11V8a4 4 0 0 1 8 0v3"/>',
  unlock: '<rect x="5" y="11" width="14" height="9" rx="2"/><path d="M8 11V8a4 4 0 0 1 7.5-2"/>',
  gear: '<circle cx="12" cy="12" r="3"/><path d="M12 3v3M12 18v3M3 12h3M18 12h3M5.6 5.6l2.1 2.1M16.3 16.3l2.1 2.1M18.4 5.6l-2.1 2.1M7.7 16.3l-2.1 2.1"/>',
  back: '<path d="M15 5l-7 7 7 7"/>',
  copy: '<rect x="8" y="8" width="11" height="12" rx="2"/><path d="M5 16V6a2 2 0 0 1 2-2h8"/>',
  backspace: '<path d="M9 5h10a1 1 0 0 1 1 1v12a1 1 0 0 1-1 1H9l-5-7 5-7Z"/><path d="M12 9l5 6M17 9l-5 6"/>',
};
const renderIconPath = name => ICONS[name] ?? ""; // the paths above are constants, never user text
export function renderIcon(name) {
  return `<svg class="ico" viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">${renderIconPath(name)}</svg>`;
}

export function toast(message) {
  const el = document.createElement("div");
  el.className = "toast";
  el.setAttribute("role", "status");
  el.textContent = message;
  document.body.append(el);
  setTimeout(() => el.remove(), 3200);
}

// ---------------------------------------------------------------- shared state and loaders
export const state = {
  me: null,          // GET /api/me
  people: [],        // GET /api/people?inactive=1, every person including protected ones
  occasions: [],     // GET /api/occasions
  occasionId: null,  // the occasion the home screen, quick idea and boards work in
  prev: "",          // the hash the user was on before this one: where Save returns to
};

function remember(key, value) {
  try { globalThis.localStorage?.setItem(key, String(value)); } catch { /* private mode: not remembered */ }
}
function recall(key) {
  try { return globalThis.localStorage?.getItem(key) ?? null; } catch { return null; }
}

export async function loadAll() {
  const [me, people, occasions] = await Promise.all([get("/api/me"), get("/api/people?inactive=1"), get("/api/occasions")]);
  state.me = me;
  state.people = people;
  state.occasions = occasions;
  const stored = Number(recall("gt_occasion"));
  const pick = occasions.find(o => o.id === state.occasionId) ?? occasions.find(o => o.id === stored)
    ?? occasions.find(o => o.current) ?? occasions[0];
  state.occasionId = pick ? pick.id : null;
}

export function setOccasion(id) {
  state.occasionId = id;
  remember("gt_occasion", id);
}

export function currentOccasion() {
  return state.occasions.find(o => o.id === state.occasionId) ?? null;
}

export const isUnlocked = () => state.me?.unlocked === true;
export const personById = id => state.people.find(p => p.id === id) ?? null;
export const owners = () => state.people.filter(p => p.is_user);

// Everyone a gift can be for or from. While locked a protected person is not offered: their name would show on a
// device left open, and a gift for them is refused with a 423 anyway.
export function choosablePeople() {
  return state.people.filter(p => p.active && (isUnlocked() || !p.protected));
}

export const recents = {
  get() {
    try { return JSON.parse(recall("gt_recent") ?? "[]").filter(Number.isInteger); } catch { return []; }
  },
  add(personId) {
    remember("gt_recent", JSON.stringify([personId, ...this.get().filter(i => i !== personId)].slice(0, 30)));
  },
};
