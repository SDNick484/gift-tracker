// The PIN pad overlay. It opens on a `locked` event (a 423 from any route, a tap on a locked row, the Lock/Unlock
// button) and resolves true once POST /api/lock/unlock succeeds. The digits live in a variable, never in the
// page; the dots only count them. A user with no PIN is always locked, so the pad then points at Settings.
import { send } from "./api.js";
import { esc, renderIcon, state } from "./core.js";

const MAX_PIN = 8;
const MIN_PIN = 4;

// The pad's entry after one key: a digit appends (up to 8), "back" removes the last.
export function nextEntry(entry, key) {
  if (key === "back") return entry.slice(0, -1);
  return /^\d$/.test(key) && entry.length < MAX_PIN ? entry + key : entry;
}

// Whether the user has been idle long enough for the app to lock itself.
export function idleExpired(nowMs, lastInputMs, idleSeconds) {
  return nowMs - lastInputMs >= idleSeconds * 1000;
}

const renderKey = k => `<button type="button" class="key" data-key="${esc(k)}">${esc(k)}</button>`;
const renderDigitKeys = () => ["1", "2", "3", "4", "5", "6", "7", "8", "9"].map(renderKey).join("");

export const renderPad = () => `<div class="pad">
  <h2>Enter your PIN</h2>
  <div class="pad-dots" aria-hidden="true"></div>
  <p class="form-error" role="alert"></p>
  <div class="pad-keys">${renderDigitKeys()}
    <button type="button" class="key quiet" data-act="cancel">Cancel</button>${renderKey("0")}
    <button type="button" class="key quiet" data-key="back" aria-label="Delete">${renderIcon("backspace")}</button>
  </div>
  <button type="button" class="key go" data-act="go">Unlock</button>
</div>`;

export const renderNoPin = () => `<div class="pad">
  <h2>No PIN set</h2>
  <p class="muted">The protected part stays locked until you set a PIN.</p>
  <div class="actions"><a class="btn primary" href="#/settings" data-act="cancel">Open Settings</a><button type="button" class="btn" data-act="cancel">Close</button></div>
</div>`;

const renderPadFor = hasPin => (hasPin ? renderPad() : renderNoPin());

let current = null; // { promise } while the pad is open

export function padIsOpen() {
  return current !== null;
}

// Opens the pad if it is not open yet; always returns the same promise while it is. true = unlocked.
export function openPad() {
  if (current) return current.promise;
  const app = document.getElementById("app");
  const overlay = document.createElement("div");
  overlay.className = "overlay";
  overlay.setAttribute("role", "dialog");
  overlay.setAttribute("aria-modal", "true");
  overlay.setAttribute("aria-label", "PIN");
  const hasPin = state.me?.has_pin === true;
  overlay.innerHTML = renderPadFor(hasPin);
  document.body.append(overlay);
  app.inert = true;

  let entry = "", busy = false, finish;
  const promise = new Promise(resolve => { finish = resolve; });
  current = { promise };
  const dots = overlay.querySelector(".pad-dots"), error = overlay.querySelector(".form-error");

  const close = result => {
    document.removeEventListener("keydown", onKey, true);
    overlay.remove();
    app.inert = false;
    current = null;
    finish(result);
  };
  const paint = () => {
    if (dots) dots.replaceChildren(...Array.from({ length: entry.length }, () => document.createElement("i")));
  };
  const press = key => {
    if (busy) return;
    entry = nextEntry(entry, key);
    error.textContent = "";
    paint();
  };
  const submit = async () => {
    if (busy || entry.length < MIN_PIN) return;
    busy = true;
    try {
      await send("POST", "/api/lock/unlock", { pin: entry });
    } catch (err) {
      entry = "";
      busy = false;
      paint();
      error.textContent = err.message;
      return;
    }
    state.me.unlocked = true;
    close(true);
  };
  function onKey(e) {
    if (e.key === "Escape") close(false);
    else if (!hasPin) return;
    else if (e.key === "Enter") submit();
    else if (e.key === "Backspace") press("back");
    else if (/^\d$/.test(e.key)) press(e.key);
    else return;
    e.preventDefault();
    e.stopPropagation();
  }
  document.addEventListener("keydown", onKey, true);
  overlay.addEventListener("click", e => {
    const target = e.target.closest("[data-key],[data-act]");
    if (!target) return;
    if (target.dataset.key) press(target.dataset.key);
    else if (target.dataset.act === "go") submit();
    else if (target.dataset.act === "cancel") close(false);
  });
  (overlay.querySelector(".key, .btn") ?? overlay).focus();
  return promise;
}
