// #/  The school home: one occasion as a grid, staff down the side and gifts across. A cell is empty, included (a
// dot) or done (a tick). Tap an empty cell to add that person; tap a filled one to edit its detail, mark it done or
// remove it. Each column says how many people it is for and how many are done, and its header can add everyone
// with a role in one go. Only the grid is redrawn after a change, so the page keeps its scroll position.
import { get, send } from "./api.js";
import { applyColors, daysLabel, esc, renderChecked, renderColor, renderOptions, setOccasion, state, toast } from "./core.js";
import { occasionChoices } from "./home.js";
import { activeRoles, peopleWithRole } from "./mode.js";

const MAX_DETAIL = 200;
const MARK = { off: "", on: "●", done: "✓" };
const STATE_WORD = { off: "not included", on: "included", done: "done" };

// ---------------------------------------------------------------- pure helpers
export const cellKey = (giftId, personId) => `${giftId}:${personId}`;

export function cellMap(matrix) {
  return new Map(matrix.cells.map(c => [cellKey(c.gift_id, c.person_id), c]));
}

export const cellState = cell => {
  if (!cell) return "off";
  return cell.done ? "done" : "on";
};

export const columnCaption = gift => `${gift.recipient_count} · ${gift.done_count} done`;

// The PUT body for the cell editor: only what the form has.
export function cellPayload(v) {
  const body = {};
  if (v.detail !== undefined) {
    const detail = v.detail.trim();
    if (detail.length > MAX_DETAIL) throw new Error(`A detail can be at most ${MAX_DETAIL} characters.`);
    body.detail = detail;
  }
  if (v.done !== undefined) body.done = Boolean(v.done);
  return body;
}

// The "For" choices of the add-gift form: everyone with a role, or one person (owners are not staff).
export function whoChoices(people) {
  const staff = people.filter(p => p.active && !p.is_user);
  return [
    { value: "", label: "Who is it for?" },
    ...activeRoles(staff).map(role => ({ value: `role:${role}`, label: `Everyone: ${role}` })),
    ...staff.map(p => ({ value: `person:${p.id}`, label: p.name })),
  ];
}

// The POST /api/gifts body. A gift needs a recipient, so the first ones are chosen here.
export function newGiftPayload(v, occasionId, people) {
  const title = v.title.trim();
  if (!title) throw new Error("A gift needs a title.");
  let ids;
  if (v.who.startsWith("role:")) {
    ids = peopleWithRole(people.filter(p => !p.is_user), v.who.slice(5)).map(p => p.id);
    if (!ids.length) throw new Error("No one has that role yet.");
  } else if (v.who.startsWith("person:") && /^\d+$/.test(v.who.slice(7))) {
    ids = [Number(v.who.slice(7))];
  } else {
    throw new Error("Pick who it is for.");
  }
  return { occasion_id: occasionId, title, recipient_ids: ids, giver_ids: [] };
}

// ---------------------------------------------------------------- markup
const renderDim = on => (on ? " dim" : "");
const renderRoleLine = p => (p.role ? `<small>${esc(p.role)}</small>` : "");
const renderDetailClass = cell => (cell?.detail ? " has-detail" : "");

const roleOptions = roles => [{ value: "", label: "Add role…" }, ...roles.map(r => ({ value: r, label: r }))];
const renderRoleSelect = (g, roles) => (roles.length
  ? `<select class="role-add" data-gift="${esc(g.id)}" aria-label="Add everyone with a role to ${esc(g.title)}">${renderOptions(roleOptions(roles), "")}</select>`
  : "");

const renderColumnHead = (g, roles) => `<th scope="col" class="gift-col"><a class="gift-title" href="#/gift/${esc(g.id)}">${esc(g.title)}</a><small>${esc(columnCaption(g))}</small>${renderRoleSelect(g, roles)}</th>`;
const renderStaffHead = p => `<th scope="row" class="staff${renderDim(!p.active)}"${renderColor(p.color)}><a href="#/person/${esc(p.id)}">${esc(p.name)}</a>${renderRoleLine(p)}</th>`;

const cellLabel = (p, g, cell) => `${p.name}, ${g.title}: ${STATE_WORD[cellState(cell)]}`;
const renderCell = (p, g, cell) => `<td><button type="button" class="cell ${esc(cellState(cell))}${renderDetailClass(cell)}" data-gift="${esc(g.id)}" data-person="${esc(p.id)}" aria-label="${esc(cellLabel(p, g, cell))}">${esc(MARK[cellState(cell)])}</button></td>`;
const renderCells = (p, gifts, cells) => gifts.map(g => renderCell(p, g, cells.get(cellKey(g.id, p.id)))).join("");
const renderRow = (p, gifts, cells) => `<tr>${renderStaffHead(p)}${renderCells(p, gifts, cells)}</tr>`;
const renderRows = (staff, gifts, cells) => staff.map(p => renderRow(p, gifts, cells)).join("");
const renderHeads = (gifts, roles) => gifts.map(g => renderColumnHead(g, roles)).join("");
const renderCount = g => `<td>${esc(g.recipient_count)}</td>`;
const renderCounts = gifts => gifts.map(renderCount).join("");

const renderNoStaff = () => `<p class="empty">No staff yet. Add people in <a href="#/settings">Settings</a>.</p>`;
const renderNoGifts = () => `<p class="empty">No gifts yet. Add the first one below.</p>`;

const renderGiftsHint = gifts => (gifts.length ? "" : renderNoGifts());

export function renderMatrix(matrix, roles) {
  if (!matrix.staff.length) return renderNoStaff();
  const cells = cellMap(matrix);
  return `${renderGiftsHint(matrix.gifts)}<div class="matrix-wrap"><table class="matrix"><thead><tr><th class="corner" scope="col">Staff</th>${renderHeads(matrix.gifts, roles)}</tr></thead>
    <tbody>${renderRows(matrix.staff, matrix.gifts, cells)}</tbody><tfoot><tr><th scope="row">${esc(matrix.staff.length)} staff</th>${renderCounts(matrix.gifts)}</tr></tfoot></table></div>`;
}

export const renderCellEditor = (gift, person, cell) => `
  <form id="cell-editor" class="card fields colored" autocomplete="off" data-gift="${esc(gift.id)}" data-person="${esc(person.id)}"${renderColor(person.color)}>
    <h3>${esc(person.name)} · ${esc(gift.title)}</h3>
    <label class="field"><span>Detail</span><input name="detail" type="text" maxlength="200" placeholder="$35 · coffee shop" value="${esc(cell?.detail ?? "")}"></label>
    <label class="check"><input name="done" type="checkbox"${renderChecked(cell?.done)}> Done</label>
    <p class="form-error" role="alert"></p>
    <div class="actions"><button class="btn primary" type="submit">Save</button><button class="btn" type="button" data-act="close">Close</button><span class="spacer"></span><button class="btn danger" type="button" data-act="remove">Remove</button></div>
  </form>`;

export const renderAddGift = people => `
  <form id="add-gift" class="card fields" autocomplete="off">
    <h3>New gift</h3>
    <label class="field"><span>Gift</span><input name="title" type="text" maxlength="200" placeholder="Card $35" required></label>
    <label class="field"><span>For</span><select name="who">${renderOptions(whoChoices(people), "")}</select></label>
    <p class="form-error" role="alert"></p>
    <div class="actions"><button class="btn primary" type="submit">Add gift</button></div>
  </form>`;

export const renderPage = (matrix, occasions, people, today = new Date()) => `
  <div class="head">
    <div class="picker">
      <select id="occasion" aria-label="Occasion">${renderOptions(occasionChoices(occasions), matrix.occasion.id)}</select>
      <span class="countdown">${esc(matrix.occasion.date)} \u00B7 ${esc(untilText(matrix.occasion.date, today))}</span>
    </div>
  </div>
  <div id="matrix">${renderMatrix(matrix, activeRoles(people))}</div>
  <div id="editor"></div>
  ${renderAddGift(people)}`;

const renderNoOccasion = () => `<div class="notice"><p>No occasion yet.</p><p><a class="btn primary" href="#/settings">Create one in Settings</a></p></div>`;

// Days from `today` (a Date) to a YYYY-MM-DD date, for the caption under the picker.
export function daysUntil(date, today) {
  const [y, m, d] = date.split("-").map(Number);
  return Math.round((Date.UTC(y, m - 1, d) - Date.UTC(today.getFullYear(), today.getMonth(), today.getDate())) / 86400000);
}
export const untilText = (date, today) => daysLabel(daysUntil(date, today));

// ---------------------------------------------------------------- behaviour
export async function render(ctx) {
  const { view } = ctx;
  if (state.occasionId === null) {
    view.innerHTML = renderNoOccasion();
    return;
  }
  const url = () => `/api/occasions/${state.occasionId}/matrix`;
  let matrix = await get(url());
  if (!ctx.alive()) return;
  view.innerHTML = renderPage(matrix, state.occasions, state.people);
  const box = view.querySelector("#matrix");
  const editor = view.querySelector("#editor");

  const refresh = async () => {
    const next = await get(url());
    if (!ctx.alive()) return;
    matrix = next;
    box.innerHTML = renderMatrix(matrix, activeRoles(state.people));
    applyColors(box);
  };
  // Run a change, then redraw the grid. A failure is shown where it happened (`error`, or a toast).
  const change = async (action, error) => {
    if (error) error.textContent = "";
    try {
      await action();
      await refresh();
    } catch (err) {
      if (error) error.textContent = err.message;
      else toast(err.message);
      return false;
    }
    return true;
  };
  const path = (giftId, personId) => `/api/gifts/${giftId}/recipients/${personId}`;

  view.querySelector("#occasion").addEventListener("change", e => {
    setOccasion(Number(e.target.value));
    ctx.rerender();
  });

  box.addEventListener("click", async e => {
    const btn = e.target.closest("button.cell");
    if (!btn) return;
    const giftId = Number(btn.dataset.gift), personId = Number(btn.dataset.person);
    const cell = cellMap(matrix).get(cellKey(giftId, personId));
    if (!cell) {
      editor.innerHTML = "";
      await change(() => send("PUT", path(giftId, personId), {}));
      return;
    }
    const gift = matrix.gifts.find(g => g.id === giftId), person = matrix.staff.find(p => p.id === personId);
    editor.innerHTML = renderCellEditor(gift, person, cell);
    applyColors(editor);
    editor.querySelector("input[name=detail]").focus();
  });

  box.addEventListener("change", async e => {
    const select = e.target.closest("select.role-add");
    if (!select || !select.value) return;
    const role = select.value;
    await change(async () => {
      const out = await send("POST", `/api/gifts/${Number(select.dataset.gift)}/recipients`, { role });
      toast(out.added.length ? `Added ${out.added.length}` : "Everyone with that role is already on it");
    });
  });

  editor.addEventListener("submit", async e => {
    e.preventDefault();
    const form = e.target;
    const error = form.querySelector(".form-error");
    let body;
    try {
      body = cellPayload({ detail: form.elements.detail.value, done: form.elements.done.checked });
    } catch (err) {
      error.textContent = err.message;
      return;
    }
    if (await change(() => send("PUT", path(form.dataset.gift, form.dataset.person), body), error)) editor.innerHTML = "";
  });

  editor.addEventListener("click", async e => {
    const act = e.target.closest("[data-act]")?.dataset.act;
    if (!act) return;
    const form = editor.querySelector("form");
    if (act === "close") {
      editor.innerHTML = "";
    } else if (act === "remove") {
      if (await change(() => send("DELETE", path(form.dataset.gift, form.dataset.person)), form.querySelector(".form-error"))) editor.innerHTML = "";
    }
  });

  view.querySelector("#add-gift").addEventListener("submit", async e => {
    e.preventDefault();
    const form = e.target;
    const error = form.querySelector(".form-error");
    let body;
    try {
      body = newGiftPayload({ title: form.elements.title.value, who: form.elements.who.value }, matrix.occasion.id, state.people);
    } catch (err) {
      error.textContent = err.message;
      return;
    }
    if (await change(() => send("POST", "/api/gifts", body), error)) {
      form.reset();
      toast("Added");
    }
  });
}
