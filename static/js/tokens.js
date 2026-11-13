// #/tokens  Agent tokens: make one (shown once), see when each was last used, revoke. The server needs the unlock for
// all of it (a full token is as strong as the PIN), so a locked visit answers 423 and draws the usual locked notice.
// A token is what an agent or the MCP service sends as `Authorization: Bearer ...` to /api/v1. See docs/mcp-setup.md.
import { get, send } from "./api.js";
import { esc, renderChecked, renderOptions, state, toast } from "./core.js";
import { isSchool } from "./mode.js";

export const SCOPES = [
  { value: "delivery", label: "delivery: read what is decided or ordered; record order details and arrivals" },
  { value: "shop", label: "shop: also ideas and budgets; mark ordered" },
  { value: "full", label: "full: everything the agent API offers, adding gifts too" },
];

// The POST body. `unlocked` (see protected gifts and Santa) only for a full token, and never in school mode.
export function tokenPayload(v, school = false) {
  const name = (v.name ?? "").trim();
  if (!name) throw new Error("A token needs a name.");
  const body = { name, scope: v.scope };
  if (!school && v.scope === "full" && v.unlocked) body.unlocked = true;
  return body;
}

export const day = seconds => (Number.isInteger(seconds) ? new Date(seconds * 1000).toISOString().slice(0, 10) : "");

const renderBadge = (on, text) => (on ? ` <span class="badge">${esc(text)}</span>` : "");
const usedText = t => (t.last_used_at ? "last used " + day(t.last_used_at) : "never used");
export const tokenLine = t => ["made " + day(t.created_at), usedText(t), t.revoked_at ? "revoked " + day(t.revoked_at) : ""].filter(Boolean).join(" · ");
const renderDim = on => (on ? " dim" : "");
const renderRevoke = t => (t.revoked_at ? "" : `<button type="button" class="btn small danger" data-act="revoke" data-id="${esc(t.id)}">Revoke</button>`);
const renderToken = t => `<li class="row${renderDim(t.revoked_at)}">
  <div class="main"><div class="name">${esc(t.name)} <span class="badge">${esc(t.scope)}</span>${renderBadge(t.unlocked, "sees protected")}${renderBadge(Boolean(t.revoked_at), "revoked")}</div>
    <div class="line">${esc(tokenLine(t))}</div></div>
  ${renderRevoke(t)}
</li>`;
export const renderTokenRows = list => (list.length ? list.map(renderToken).join("") : `<li class="row"><div class="main muted">No tokens yet.</div></li>`);

export const renderCreated = made => (made ? `<section class="card notice" role="status"><h3>${esc(made.name)}</h3>
  <p>Copy this token now. It will not be shown again.</p>
  <p><code class="token">${esc(made.token)}</code></p></section>` : "");

const renderUnlockedBox = school => (school ? "" : `<label class="check"><input name="unlocked" type="checkbox"${renderChecked(false)}> Full only: may see protected gifts and Santa, as an unlocked owner can</label>`);

export const renderTokens = (list, school, made = null) => `<a class="back" href="#/settings">&larr; Settings</a>
  <div class="head"><h2>Agent tokens</h2></div>
  ${renderCreated(made)}
  <section class="card"><h3>Tokens</h3>
    <p class="muted small">An agent sends a token to /api/v1. No token but a full one made to see protected gifts ever sees them.</p>
    <ul class="rows">${renderTokenRows(list)}</ul>
  </section>
  <form class="card fields" data-form="token-add" autocomplete="off"><h3>New token</h3>
    <label class="field"><span>Name</span><input name="name" type="text" maxlength="60" placeholder="Shipping agent" required></label>
    <label class="field"><span>Scope</span><select name="scope">${renderOptions(SCOPES, "delivery")}</select></label>
    ${renderUnlockedBox(school)}
    <p class="form-error" role="alert"></p>
    <div class="actions"><button class="btn primary" type="submit">Make token</button></div>
  </form>`;

export async function render(ctx) {
  const { view } = ctx;
  const school = isSchool(state.me);
  const list = await get("/api/tokens");
  if (!ctx.alive()) return;
  view.innerHTML = renderTokens(list, school);

  view.addEventListener("submit", async e => {
    e.preventDefault();
    const form = e.target;
    if (form.dataset?.form !== "token-add") return;
    const error = form.querySelector(".form-error");
    if (error) error.textContent = "";
    try {
      const els = form.elements;
      const made = await send("POST", "/api/tokens", tokenPayload({ name: els.name.value, scope: els.scope.value, unlocked: els.unlocked?.checked }, school));
      // Drawn here, not by rerender(): the token exists only in this answer.
      view.innerHTML = renderTokens(await get("/api/tokens"), school, made);
    } catch (err) {
      if (error) error.textContent = err.locked ? "Locked. Unlock, then try again." : err.message;
    }
  });

  view.addEventListener("click", async e => {
    const btn = e.target.closest?.("[data-act]");
    if (btn?.dataset.act !== "revoke") return;
    if (!globalThis.confirm("Revoke this token? Anything using it stops working at once.")) return;
    try {
      await send("DELETE", `/api/tokens/${Number(btn.dataset.id)}`);
    } catch (err) {
      toast(err.locked ? "Locked. Unlock, then try again." : err.message);
      return;
    }
    toast("Revoked");
    ctx.rerender();
  });
}
