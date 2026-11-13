// Entry point: hash routing, the shared header, and what to draw when a screen cannot (locked, not signed in).
// Screens are loaded on demand. Each exports `render(ctx)` with ctx = { view, parts, query, alive, rerender }:
// `view` is a fresh element for this visit, `alive()` is false once the user has moved on, and `rerender()`
// draws the same route again (after an unlock, say).
import { ApiError, events, send } from "./api.js";
import { applyColors, esc, loadAll, parseHash, renderIcon, state } from "./core.js";
import { idleExpired, openPad, padIsOpen } from "./lock.js";
import { lockApplies, pickScreen } from "./mode.js";

const SCREENS = {
  "": () => import("./home.js"),
  idea: () => import("./idea.js"),
  person: () => import("./person.js"),
  gift: () => import("./gift.js"),
  board: () => import("./board.js"),
  lists: () => import("./lists.js"),
  settings: () => import("./settings.js"),
};

// In school mode these replace the screen of the same name, and the board is gone (see mode.js).
const SCHOOL_SCREENS = {
  "": () => import("./school.js"),
};

const host = document.getElementById("app");
let visit = 0;
let here = "";

const renderError = message => `<div class="notice error" role="alert"><p>${esc(message)}</p><p><button class="btn" data-act="retry">Try again</button></p></div>`;
const renderLockedNotice = () => `<div class="notice" role="status"><p>This part is locked.</p><p><button class="btn primary" data-act="unlock">Unlock</button> <a class="btn" href="#/">Home</a></p></div>`;
const renderNotFound = () => `<div class="notice"><p>There is nothing at this address.</p><p><a class="btn" href="#/">Home</a></p></div>`;

export function route() {
  const mine = ++visit;
  const hash = location.hash || "#/";
  if (hash !== here) state.prev = here;
  here = hash;
  const { parts, query } = parseHash(hash);
  const view = document.createElement("div");
  view.className = "view";
  host.replaceChildren(view);
  const ctx = { view, parts, query, alive: () => visit === mine && view.isConnected, rerender: route };
  view.addEventListener("click", e => {
    const act = e.target.closest?.("[data-act]")?.dataset.act;
    if (act === "retry") route();
    if (act === "unlock") events.dispatchEvent(new Event("locked"));
  });
  return draw(ctx);
}

async function draw(ctx) {
  const { view } = ctx;
  try {
    await loadAll();
    document.title = state.me.app_name;
    document.getElementById("appName").textContent = state.me.app_name;
    updateLockButton();
    const load = pickScreen(ctx.parts[0] ?? "", SCREENS, SCHOOL_SCREENS, state.me);
    if (!load) {
      view.innerHTML = renderNotFound();
      return;
    }
    const screen = await load();
    if (!ctx.alive()) return;
    await screen.render(ctx);
    if (ctx.alive()) applyColors(view);
  } catch (e) {
    if (!ctx.alive()) return;
    if (e instanceof ApiError && e.locked) view.innerHTML = renderLockedNotice();
    else view.innerHTML = renderError(e instanceof ApiError ? e.message : "Something went wrong. Reload the page.");
    if (!(e instanceof ApiError)) console.error(e);
  }
}

// ---------------------------------------------------------------- the lock
const lockBtn = document.getElementById("lockBtn");
const renderLockLabel = unlocked => `${renderIcon(unlocked ? "unlock" : "lock")}<span>${esc(unlocked ? "Lock" : "Unlock")}</span>`;

function updateLockButton() {
  const unlocked = state.me?.unlocked === true;
  lockBtn.hidden = !lockApplies(state.me); // a school instance has no lock
  lockBtn.innerHTML = renderLockLabel(unlocked);
  lockBtn.classList.toggle("is-open", unlocked);
  lockBtn.setAttribute("aria-label", unlocked ? "Lock now" : "Unlock");
}

// Locks now (the Lock button, the idle timer, the tab going to the background) and draws the route again, so
// nothing protected stays on screen. A protected screen then answers 423 and the pad opens.
let locking = false;
async function lockNow() {
  if (locking) return;
  locking = true;
  try {
    await send("POST", "/api/lock/lock", {});
  } catch {
    // Offline or refused: the server still decides what is shown. The redraw below asks it again.
  }
  if (state.me) state.me.unlocked = false;
  updateLockButton();
  locking = false;
  route();
}

// A 423 anywhere means the lock is closed: remember it and open the pad. Once it unlocks, draw the route again.
// This does not redraw by itself: a screen that gets a 423 would ask again and loop.
events.addEventListener("locked", async () => {
  if (state.me) state.me.unlocked = false;
  updateLockButton();
  if (padIsOpen()) return; // the event that opened it will redraw once, after the unlock
  if (await openPad()) route();
});

lockBtn.addEventListener("click", () => {
  if (state.me?.unlocked) lockNow();
  else events.dispatchEvent(new Event("locked"));
});

// Idle: no pointer or key input for lock_idle_seconds. Hidden tab: lock at once.
let lastInput = Date.now();
for (const type of ["pointerdown", "pointermove", "keydown", "wheel", "touchstart"]) {
  window.addEventListener(type, () => { lastInput = Date.now(); }, { capture: true, passive: true });
}
setInterval(() => {
  if (lockApplies(state.me) && state.me.unlocked && idleExpired(Date.now(), lastInput, state.me.lock_idle_seconds)) lockNow();
}, 1000);
document.addEventListener("visibilitychange", () => {
  if (document.visibilityState === "hidden" && lockApplies(state.me) && state.me.unlocked) lockNow();
});

window.addEventListener("hashchange", route);
route();
