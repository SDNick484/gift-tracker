// Entry point: hash routing, the shared header, and what to draw when a screen cannot (locked, not signed in).
// Screens are loaded on demand. Each exports `render(ctx)` with ctx = { view, parts, query, alive, rerender }:
// `view` is a fresh element for this visit, `alive()` is false once the user has moved on, and `rerender()`
// draws the same route again (after an unlock, say).
import { ApiError, events } from "./api.js";
import { applyColors, esc, loadAll, parseHash, state } from "./core.js";

const SCREENS = {
  "": () => import("./home.js"),
  idea: () => import("./idea.js"),
  person: () => import("./person.js"),
  gift: () => import("./gift.js"),
  board: () => import("./board.js"),
  settings: () => import("./settings.js"),
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
    const load = SCREENS[ctx.parts[0] ?? ""];
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

// A 423 anywhere means the lock closed behind our back: remember it, and draw the route again so nothing
// protected stays on screen.
events.addEventListener("locked", () => {
  if (state.me) state.me.unlocked = false;
});

window.addEventListener("hashchange", route);
route();
